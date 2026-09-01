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
  scripts in `migrations/`.
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
