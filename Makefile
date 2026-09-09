.PHONY: setup migrate db-reset seed ingest etl dq report score evals evals-variance api-types web-install web-build web-test lint test run gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 gate-m7

# Local development default. Export DATABASE_URL to override (see .env.example).
# The driver must be psycopg v3 — `postgresql://` alone resolves to psycopg2,
# which this project does not install.
DATABASE_URL ?= postgresql+psycopg://tinlantern:tinlantern@localhost:5432/tinlantern
export DATABASE_URL

# The suite runs against its OWN database, which it drops and recreates each
# session. Named explicitly rather than derived from DATABASE_URL: deriving
# a name is how a '_test' suffix ends up appended to something unexpected.
TEST_DATABASE_URL ?= postgresql+psycopg://tinlantern:tinlantern@localhost:5432/tinlantern_test
export TEST_DATABASE_URL

setup:
	pip install -e ".[dev]"
	docker compose up -d --wait db
	$(MAKE) migrate

# Alembic owns all DDL, including creating the raw and warehouse schemas
# (ADR-0001). There is no database init-script path.
migrate:
	alembic upgrade head

# raw.* is append-only, so tests cannot clean up after themselves and a
# development database accumulates rows across runs. This is the sanctioned
# reset: drop the raw tables and rebuild them empty (ADR-0005's escape
# hatch). It destroys every stored statement — never point it at anything
# that matters.
db-reset:
	alembic downgrade 0001
	alembic upgrade head

# The generator is dev tooling and is never installed, so it runs as a
# module from the working tree. Run from the repository root.
seed:
	python -m data.generator

# Posts the generated cohort to a RUNNING api (`make run` in another
# shell). Re-running is a no-op: ingestion is idempotent by statement id.
ingest:
	python -m data.ingest

# Loads raw.statements into the warehouse star schema. Incremental and
# re-runnable: pages above a stored watermark, and the schema's UNIQUE
# grain stops duplicates regardless.
etl:
	python -m pipeline

# Data-quality checks over the warehouse. Separate from `etl` on purpose:
# one exit code, one meaning. The sequence is `make etl && make dq`.
dq:
	python -m pipeline.dq

# Regenerates evals/reports/ from the loaded warehouse. Part of the
# milestone-close ritual: a report that drifts from the model is worse
# than no report.
report:
	python -m ml.evaluation

# Trains on the warehouse and writes per-learner risk scores and drivers.
# Re-running replaces scores for the same window and model.
score:
	python -m ml

# The golden question set against the REAL provider, written to
# evals/reports/. Part of the milestone-close ritual: the gate runs the
# same set against the stub, which proves the harness works and nothing
# about the model. A synthetic run is REFUSED rather than written.
evals:
	python -m evals

# How much the golden set moves between runs. Five runs of eighteen
# questions against the real provider — its own artifact, because a
# single run cannot answer it.
evals-variance:
	python -m evals --runs 5

# The frontend. `npm ci` installs exactly the lockfile, so the gate
# builds what CI builds. `web-build` runs `tsc --noEmit` before vite,
# because a type error that only vite tolerates is still a type error.
web-install:
	cd app/web && npm ci

web-build:
	cd app/web && npm run build

# Component behaviour, in jsdom. `web-build` type-checks; this asserts
# what renders. A chart that compiles and draws nothing passes the
# first and fails the second.
web-test:
	cd app/web && npm test

# Regenerates app/web/src/api-types.ts from the Pydantic response
# models. The committed file is drift-tested, so this is how you change
# it — editing the generated file by hand will not survive the gate.
api-types:
	python -m tools.generate_api_types

# Brings a demo cohort into being, as far as one target honestly can.
#
# `ingest` posts over HTTP to a RUNNING api, so this cannot be
# self-contained without either starting a server or bypassing the
# endpoint. Bypassing it was rejected: a demo that loads by a path the
# real system never uses is evidence for something other than the
# product. So this checks, and fails naming the step you are missing.
demo:
	@curl -sf http://localhost:8000/health >/dev/null || { \
	  echo "the API is not running. Demo mode needs it in another terminal:"; \
	  echo "    DEMO_MODE=true make run"; \
	  echo "then re-run \`make demo\` here."; exit 2; }
	$(MAKE) seed
	$(MAKE) ingest
	$(MAKE) etl
	$(MAKE) score
	$(MAKE) demo-check
	@echo
	@echo "Demo ready. Open the frontend with:  cd app/web && npm run dev"
	@echo "Q&A replays recorded responses; no model is called."

# Are the recorded responses still true of the loaded cohort?
#
# Staleness is already detected — a recording is keyed by the request
# that produced it, and that request embeds the returned rows, so
# changed data means the recording is not found and the stub raises.
# That is loud and arrives MID-DEMO. This finds it first.
demo-check:
	python -m tools.check_demo_recordings

lint:
	ruff check . && ruff format --check .

test:
	pytest -q

run:
	uvicorn app.main:app --reload

# Every gate runs lint first, then the cumulative milestone-marked tests.
gate-m0:
	$(MAKE) lint && pytest -q -m m0
gate-m1:
	$(MAKE) lint && pytest -q -m "m0 or m1"
gate-m2:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2"
gate-m3:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3"
gate-m4:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4"
gate-m5:
	$(MAKE) lint && $(MAKE) web-build && $(MAKE) web-test && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m6:
	$(MAKE) lint && terraform -chdir=infra fmt -check && terraform -chdir=infra validate && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m7:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5 or demo"
