# TinLantern

**An open-source AI early-alert platform for xAPI learning data.**
*Light made from tin cans.*

> Status: pre-M0 scaffold. This README becomes a full case study as
> milestones land (see docs/MILESTONES.md). Sections below are the
> skeleton to fill — replace every TODO before calling M6 done.

TinLantern sits beside your learning stack — Moodle (via logstore_xapi),
Articulate-published content, or any system that speaks xAPI (the
Experience API) — warehouses the event stream, scores learner risk with
ML, and uses an LLM to produce grounded advisor summaries and
natural-language Q&A with citations.

## Why it exists — TODO(M6): problem narrative, 2 paragraphs
## Demo — TODO(M6): live URL + 3 screenshots/GIF + walkthrough video link
## Architecture — TODO(M2): diagram + component walkthrough
## Key decisions — TODO(ongoing): link each ADR with a one-line tradeoff
## Evaluation — TODO(M3/M4): risk-model metrics table + LLM groundedness results
## Limitations — TODO(M6): roster/SIS gap, synthetic data, single-tenant
- The ingestion endpoint is **LRS-style**, not a fully ADL-conformant
  Learning Record Store: it accepts and validates the xAPI statement
  subset this platform uses, not the complete xAPI spec (state/activity
  profile APIs, full attachment handling, OAuth, etc.). See ADR-0001.
  **Conformance deviations are tracked here**, in this list, so there is
  one place to look. There are currently two, and both trade
  compatibility for a guarantee the platform depends on:

  1. **Unmodelled properties are rejected, not ignored.** The statement
     models set `extra="forbid"`, so a statement carrying any property
     TinLantern does not model is refused. A conformant LRS would accept
     and ignore it. For an analytics warehouse, unmodelled data that
     silently vanishes is worse than a loud rejection — but a standard
     xAPI emitter may need its statements narrowed before this platform
     will take them.
  2. **A statement must carry a client-supplied `id`.** A conformant LRS
     assigns one when the client omits it. TinLantern refuses the
     statement instead, because an assigned id cannot be idempotent: the
     client retries a timed-out request, a second id is minted, and the
     same event is stored twice — on exactly the path idempotency exists
     to protect. Client-supplied ids are what make safe retries possible.

  Rejected statements are recorded in a durable table with the payload
  kept verbatim, never merely logged and dropped, so a sender outside the
  subset produces queryable evidence rather than silent data loss.
## Run it yourself

Prerequisites: Python 3.12 and Docker (both provided by the dev container
in `.devcontainer/`).

```sh
make setup   # install deps, start Postgres 16, run migrations
make seed    # generate a synthetic cohort
make run     # serve the API

# in a second shell, once the API is up:
make ingest          # load the cohort through the ingestion endpoint
make etl && make dq  # build the warehouse, then check it
make score           # risk scores, drivers, and advisor summaries
```

### The demo

Two terminals, because ingestion goes through the HTTP endpoint. A
single self-contained target would have to bypass it, and a demo that
loads by a path the real system never uses is evidence for something
other than the product.

```sh
# terminal 1
DEMO_MODE=true make run

# terminal 2 — seeds, ingests, builds, and scores
make demo

# terminal 3 — the dashboard
cd app/web && npm run dev
```

**`DEMO_MODE=true` overrides `LLM_PROVIDER`.** With it on, no language
model is ever called: Q&A replays responses recorded once from a real
model, and each reply says which model wrote it and when. A public demo
that can spend money or exercise a credential is a liability, so the
switch does not defer to the environment — and `GET /health` reports
which provider actually won, so you can check it from outside the
process rather than trusting the setting.

`make demo` refuses to run without the API and names the step you are
missing rather than doing half the job.

`make etl` and `make dq` are separate on purpose: one exit code with one
meaning each. A data-quality failure means the warehouse is wrong, not
that the load broke.

`make seed` writes a reproducible cohort to `data/output/` — roughly
190k xAPI statements for 120 learners across two courses, in about 25
seconds. Alongside them it writes `ground_truth.ndjson`, the evaluation
sidecar, and `manifest.json` recording the seed and the full resolved
configuration that produced the run.

Cohort shape is configured in `data/generator/cohort.example.yaml`; copy
it to `cohort.yaml` to change it. Every contestable number lives there —
cohort size, term length, the archetype mix, how much engagement converts
into submitted work, and what counts as an at-risk outcome.

`make setup` brings up the `db` service from `docker-compose.yml`, waits
for it to report healthy, then runs `alembic upgrade head` — which creates
the `raw` and `warehouse` schemas. Alembic owns all DDL, so there is no
database init-script step; see ADR-0001.

The default connection string is in `.env.example`. Export `DATABASE_URL`
to point at a different database.

`make test` runs against its own database (`TEST_DATABASE_URL`, default
`tinlantern_test`), which it drops and recreates each run. Your
development data is never touched by the suite.

The `raw` schema is append-only — statements cannot be updated or deleted
once stored (ADR-0005) — so a development database accumulates rows as you
run the suite. `make db-reset` drops and rebuilds the raw tables empty.
It destroys every stored statement, so never point it at anything that
matters.
## Privacy posture
This repo and its demo contain **synthetic data only**, generated by
`data/generator/`. See docs/PRD.md sensitivity note.

## License
AGPL-3.0 — see LICENSE. Commercial hosting/integration: EDTEK Consulting.
