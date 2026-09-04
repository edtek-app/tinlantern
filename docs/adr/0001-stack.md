# ADR-0001: Technology stack
Status: Accepted

## Context
TinLantern is a solo-maintained portfolio project that must be defensible
in a technical interview, run end-to-end on a local machine through M5, and
deploy serverless on AWS at M6. The stack was fixed at project inception.
This ADR records the rationale so every choice is auditable from the public
repository alone, and folds in three related decisions raised during the
pre-implementation audit: the migrations tool, the xAPI validation
approach, and the warehouse schema layout.

## Decision
- **Language / runtime:** Python 3.12 — one language across API, ETL, and
  ML.
- **API:** FastAPI + Pydantic v2 — validation at the edge, generated
  OpenAPI, async-capable.
- **DB access:** SQLAlchemy 2.x.
- **Migrations:** Alembic. `alembic.ini` at the repo root, versioned
  scripts in `migrations/`. Alembic **owns ALL DDL including initial schema
  creation; no init-script path.** Migration `0001` creates the `raw` and
  `warehouse` schemas. `docker-entrypoint-initdb.d` was rejected: those
  hooks fire only on a fresh volume (so existing local databases drift) and
  do not exist at all on managed Postgres at M6, making that path
  throwaway. One mechanism owns DDL in every environment, from the first
  schema to the last table.
- **Database:** PostgreSQL 16, run as a **single container / single
  database** with **two schemas from day one** — `raw` (append-only
  ingested statements) and `warehouse` (star schema). The schema split is
  sufficient isolation at this project's scale and keeps local setup to one
  container.
- **xAPI validation:** hand-authored Pydantic v2 models covering the
  statement subset TinLantern emits and accepts, with strict tests. No
  third-party xAPI/LRS library — the maintained options are stale or
  heavier than needed. Consequence: v1 is an **LRS-style endpoint**, not a
  fully ADL-conformant Learning Record Store. Documented in README
  limitations.
  - **The models live in `app/xapi/` and the application owns them.**
    `data/generator/` imports the contract; the dependency never runs the
    other way. The alternative — the ingestion API importing its schema
    *from* the synthetic data generator — inverts the dependency and would
    be awkward to defend in review.
  - **Conformance deviations live in one place.** Every point where
    TinLantern knowingly departs from a conformant LRS is listed in the
    README limitations section. Deviations are now a category rather than
    a one-off, so they get a single home a reader can check rather than
    being scattered across ADRs and module docstrings.
  - **Written to the receiver's contract, with `extra="forbid"` on every
    model.** An unrecognised key at any nesting depth is a rejection, not
    decoration. This is the strict-subset posture, made mechanical rather
    than intentional: a conformant LRS must accept the *whole* statement
    shape including properties this platform ignores, and TinLantern
    deliberately does not. The test suite is the guard — it must keep
    exercising shapes the generator would never emit (extra keys, batch
    envelopes, numeric timestamps), or the models would drift into fitting
    their own emitter rather than an untrusted sender.
  - Built at M0 because the generator needs the contract to emit against.
    M1 then hardens *transport* — batching, idempotency, rejection
    logging — not the schema.
  - `registered` was added to the accepted verb set during the M0 audit,
    which found MILESTONES.md asking for enrollments as a statement
    category while DESIGN.md's verb set had none. This widens the receiver
    contract by one verb, deliberately: an enrollment is a fact in its own
    right, and inferring it from a learner's first `initialized` would
    leave a learner who never opens a course with no enrollment at all —
    precisely the population an early-alert system exists to find. M2's
    dimensional model wants enrollment as a fact.
  - The accepted verb set is closed (see `VERB_IRIS`). M1's rejection path
    is what makes that safe: an LMS sending an unmodelled verb produces a
    visible rejection log, never silent data loss — and those logs are the
    evidence any future case for widening the set would rest on.
    **Loosening the verb set is an ADR, not a quiet edit.**
- **ML:** pandas + scikit-learn. Exploration in `ml/notebooks/`;
  production code promoted to `ml/src/` as importable, tested modules.
- **LLM:** provider-abstracted client in `app/llm/`. Providers:
  `anthropic` (local dev), `stub` (deterministic canned responses for
  tests/CI, no credentials), and a cloud provider (AWS Bedrock) selected
  via infrastructure config at M6. No provider calls outside the client.
- **Frontend:** React + Vite (`app/web/`).
- **IaC:** Terraform (`infra/`), serverless-first (Lambda, API Gateway,
  S3), introduced at M6.
- **Tooling:** pytest with milestone markers (`m0`–`m5` plus `demo`);
  ruff for lint and formatting.
- **Packaging:** the `packages` list in `pyproject.toml` is a **deployment
  manifest** — if a package is listed it ships to the M6 runtime.
  Development tooling (`data/`, `evals/`) stays importable from the
  repository root but is never listed; a synthetic data generator has no
  business in a production Lambda. Packages are enumerated explicitly
  rather than found by `find:` discovery, so adding one is a visible diff
  line and forgetting one fails loudly at import in the same session.
  A gate test enforces this rather than review memory.

  The manifest follows the architecture actually being built, and changes
  with it. `pipeline` is listed from M2 because the ETL is designed to run
  in the deployed environment. If M6's database ADR instead chooses a
  precomputed demo store, the manifest changes then — that is the ADR's
  job at the time, not a hedge to build in now.

## Consequences
- **Easier:** one language end-to-end; local stack is a single Postgres
  container; the architecture story uses standard, widely understood
  components.
- **Harder / accepted:** we maintain our own xAPI models as the spec
  surface we touch grows; the `raw`/`warehouse` split in one database
  means the warehouse cannot scale independently.
- **Given up:** out-of-the-box full xAPI conformance; multi-database
  isolation.
- **Revisit triggers:** ingestion broadens beyond xAPI (Caliper, SIS
  sync); warehouse query load outgrows a shared instance (reassess in the
  M6 managed-database ADR); a well-maintained xAPI validation library
  appears.

This supersedes the "single vs. two Postgres instances" question tracked
in progress notes before implementation began.
