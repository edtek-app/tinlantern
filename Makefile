.PHONY: setup migrate lint test run gate-m0 gate-m1 gate-m2 gate-m3 gate-m4 gate-m5 gate-m6 gate-m7

# Local development default. Export DATABASE_URL to override (see .env.example).
# The driver must be psycopg v3 — `postgresql://` alone resolves to psycopg2,
# which this project does not install.
DATABASE_URL ?= postgresql+psycopg://tinlantern:tinlantern@localhost:5432/tinlantern
export DATABASE_URL

setup:
	pip install -e ".[dev]"
	docker compose up -d --wait db
	$(MAKE) migrate

# Alembic owns all DDL, including creating the raw and warehouse schemas
# (ADR-0001). There is no database init-script path.
migrate:
	alembic upgrade head

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
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m6:
	$(MAKE) lint && terraform -chdir=infra fmt -check && terraform -chdir=infra validate && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5"
gate-m7:
	$(MAKE) lint && pytest -q -m "m0 or m1 or m2 or m3 or m4 or m5 or demo"
