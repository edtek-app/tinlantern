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
## Demo

Running locally in demo mode — no language model is called; Q&A replays
answers recorded once from a real model, and each reply says so.

### Cohort overview

![Cohort overview: 38 of 120 learners flagged, a risk distribution, weekly
active learners, and the ranked learner list](docs/images/cohort-overview.png)

The headline is the flagged count, not a chart — "who needs attention"
should not arrive after two visualisations. The risk distribution's
middle bin edge IS the alert threshold, so the chart cannot disagree
with the flag on the same learner. The second chart is **engagement over
time, deliberately not risk over time**: only one feature window has
been scored, so a risk line would be a single point or a line drawn
through repeated scoring runs.

### Learner drill-down

![Learner drill-down: risk score, the features driving it, and an advisor
summary](docs/images/learner-drilldown.png)

Per-learner drivers, measured by moving one feature to the cohort median
and re-scoring. They are shown as absolute contributions with the
non-additivity caveat inline — never a pie chart, a stacked bar, or a
"top three explain X%" line, because the contributions are
counterfactual, they interact, and they do not sum.

### Q&A with citations

![A question answered with the executed SQL and the cited row shown: the
query SELECT round(avg(scaled_score)::numeric, 2) ... and row:0 carrying
avg_scaled_score 0.67 over 6,919 graded statements](docs/images/qa-with-citations.png)

**This is the part that matters.** The answer is not asserted, it is
evidenced: the panel shows **the exact SQL that ran** and **the rows it
returned**, and every figure in the answer is checked against the row
the claim cites before a reader sees it. Here the answer states 0.67 and
6,919, and `row:0` beneath it carries `avg_scaled_score: 0.67` and
`graded_statements: 6919` — the same numbers, from the query above them.
An answer whose figures do not trace to a cited row is withheld rather
than shown with a caveat.

Rows are cited positionally rather than by primary key because most
questions produce aggregates, and an aggregate has no key to cite.

### Refusal

![The question "What is each learner's final grade for the course?"
answered with a refusal explaining the warehouse holds no final-grade
field](docs/images/qa-refusal.png)

A question the warehouse cannot answer is **refused, not approximated**.
`fact_assessment` holds per-assessment scores and no course grade, so
averaging them into a "final grade" would produce a real number
answering a different question — which an advisor cannot detect. Across
five evaluation runs the system refused 6 of 6 unanswerable questions,
including four written to *look* answerable.
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
missing rather than doing half the job. It finishes by running
`make demo-check`, which confirms the recorded responses are still true
of the cohort now loaded — a recording is keyed to the data it was
captured against, so re-seeding invalidates it, and without this the
failure would surface mid-demo.

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
