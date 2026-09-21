# Milestones

Each milestone has: goal, acceptance criteria, an automated **gate**
(`make gate-mN`), and an **owner review checklist** done by a human before
merge. The active milestone is marked in `docs/PROGRESS.md`.

Loop per milestone: **opening a milestone bumps CI's gate target** →
plan task → build → tests green → owner reviews commit → commit → repeat
→ gate audit → owner review → PR merge → tag `mN`.

The bump lands as its own `ci:` commit before any of the milestone's code,
so the first feature commit is already validated by the new gate. Folded
into a feature task it is the kind of step that silently does not happen.

**Closing a milestone regenerates its reports** (`make report`, and from
M4 also `make evals` against the real provider) before the tag, **and
from M5 runs `make demo-check`** — recorded demo responses are keyed to
the cohort they were captured against, so a re-seed silently invalidates
them and the failure otherwise surfaces mid-demo.

**Any re-score requires re-recording the demo** (`python -m
tools.record_demo_responses`, which needs a real provider). Recorded
answers embed the rows the query returned, and one of those columns is
`model_version` — the git commit SHA — so a re-score after any commit
makes every recording unreachable. `make demo` therefore does not score;
it checks. **This makes the demo cohort a pinned artifact: a specific
seed AND a specific model_version, which is a constraint on M7's public
deployment story and belongs in its planning rather than being
discovered there.** Reports in
`evals/reports/` are committed artifacts quoted elsewhere;
each carries a provenance header naming the commit and row counts it was
computed from, so a stale one declares itself — but only if someone looks.
Regenerating at close means a report never drifts silently across a
milestone boundary.

CI runs the current milestone's gate — `.github/workflows/ci.yml` invokes
`make gate-mN` for the active `N`, and that target advances as milestones
complete. Every test carries a milestone marker, so nothing escapes a gate.

---

## M0 — Bootstrap & synthetic data
**Goal:** Repo skeleton runs; realistic synthetic xAPI data exists.
- [x] Docker Compose brings up Postgres 16; `make setup` installs deps,
      starts the database, and runs `alembic upgrade head`
- [x] Alembic bootstrapped (`alembic.ini`, `migrations/`); migration `0001`
      creates the `raw` and `warehouse` schemas. Per ADR-0001 Alembic owns
      all DDL from here on — there is no database init-script path
- [x] xAPI statement models in `app/xapi/` — hand-authored Pydantic v2,
      `extra="forbid"`, written to the receiver's contract (ADR-0001). The
      generator is a client of this schema, not its owner
- [x] `data/generator/` produces configurable cohorts of xAPI statements
      with injectable "at-risk" behavior patterns, covering: enrollments
      (`registered`, one per learner-course), course activity
      (`initialized`, `experienced`), video events (`played`, `paused`,
      `completed`), assessment attempts (`attempted`, `answered`,
      `passed`, `failed`) and submissions (`submitted`)
- [x] Generator has unit tests incl. statement schema validation
- [x] CI runs lint + tests on every push
**Gate:** `make gate-m0` — generator tests + schema validation + lint.
**Owner review:** commit log reads cleanly; ADR-0001 (stack) and ADR-0002
(synthetic-data-only policy) merged; generated data looks plausible.

## M1 — Ingestion
**Goal:** xAPI statements land durably via API.
- [x] `POST /xapi/statements` (single + batch) validating against the
      `app/xapi/` models built in M0. M1 adds transport concerns — batch
      envelopes, idempotency, rejection logging — not schema work
- [x] Raw statements stored append-only in the `raw` schema; idempotent on
      statement id. The table lands as an Alembic migration on the `0001`
      baseline created in M0 — the migration tooling already exists
- [x] Rejection path: invalid statements logged, not dropped silently.
      The mechanism is the durable `raw.rejections` table created in
      migration `0002` — a log line is not queryable evidence (ADR-0005).
      Every refusal writes through `app.raw.record_rejection`, so a
      rejection means the same thing wherever it came from
**Gate:** `make gate-m1` — API tests incl. malformed input, duplicates,
batch loads of ≥10k statements.
**Owner review:** error handling deliberate; ADR for storage layout.

## M2 — Warehouse & ETL
**Goal:** Raw events become a queryable star schema.
- [x] Dimensional model: fact_activity, fact_assessment; dims student,
      course, activity, date (documented in ARCHITECTURE.md with diagram)
- [x] Incremental ETL job (idempotent, re-runnable) raw → warehouse.
      Must populate `fact_assessment.attempt_number`, with a test showing
      a `struggling` learner has `attempt_number > 1` in the loaded
      warehouse — the column was added in `0003` on M3's anticipated need,
      and a column added on anticipation quietly stays null otherwise
- [x] Data-quality checks: row counts, null rates, referential integrity.
      Reconciliation is the headline check — every statement at or below
      the ETL watermark is in `fact_activity` or `etl_rejections`, and
      none in neither, plus the same narrowed to graded statements and
      `fact_assessment`. **This suite owns the assertion that
      `warehouse.etl_rejections` is empty**: the ETL deliberately fails
      only on what its own run rejected, since an ETL that failed forever
      after one bad statement would be unusable. Checks declare
      ABSOLUTE (one violation fails) or REPORTED (informational), and
      every ABSOLUTE check is mutation-verified
**Gate:** `make gate-m2` — ETL runs twice on same input without dupes;
DQ checks pass; sample analytical queries return expected results.
**Owner review:** schema defensible in an interview; DQ failures fail loudly.

## M3 — Risk model
**Goal:** Per-student risk score with an honest evaluation.
- [x] Feature pipeline from warehouse (engagement recency, assessment
      trajectory, pacing vs cohort)
- [x] **Features come only from the first `risk.feature_window_weeks` of
      the term; the outcome is measured across the whole term.** Without
      this the model can recompute the label from full-term scores and
      score perfectly while predicting nothing (ADR-0004). The leakage
      guard must assert no feature reads a statement timestamped after the
      window closes — not merely that the sidecar stays out of the
      warehouse.
- [x] Baseline model (logistic regression) THEN one stronger model.
      **Comparison logic lives in `ml/evaluation/` as tested code**; the
      notebook in `ml/notebooks/` renders it and is explicitly
      non-authoritative — authoritative numbers are in `evals/reports/`.
      A notebook that could silently diverge from what was tested is the
      same class of problem as a stale diagram, and making it
      non-authoritative removes the risk rather than guarding it. Winner
      promoted to `ml/src/`
- [x] Evaluation report: ROC-AUC, precision/recall at alert threshold,
      calibration; written to `evals/reports/`
- [x] Scoring job writes scores + top contributing features per student
**Gate:** `make gate-m3` — feature/scoring tests; eval report generated;
model beats trivial baseline on held-out cohort.
**Owner review:** metrics honestly framed; ADR on model choice + threshold.

## M4 — LLM layer + evals
**Goal:** Grounded advisor summaries and cited Q&A — with an eval harness.
- [x] Provider-abstracted LLM client (env-switchable: `anthropic` for
      local dev, `stub` for tests/CI, cloud provider set via infra config)
- [x] Advisor summary: risk score + features → plain-language, actionable,
      non-deterministic-safe summary (no invented facts)
- [x] NL Q&A over warehouse: question → SQL/semantic layer → answer WITH
      citations to the underlying data
- [x] Eval harness in `evals/`: golden-question set, groundedness checks,
      refusal-on-unanswerable checks; results versioned in `evals/reports/`
- [x] **A committed eval run against the REAL provider.** The gate runs
      the golden set against `stub`, which keeps CI fast and
      credential-free but means a green gate proves only that the harness
      works. `make evals` runs the same set against `anthropic` and writes
      to `evals/reports/` with a provenance header, and closing the
      milestone runs it. Without this criterion, a suite that has only
      ever seen canned responses would pass every check while measuring
      nothing about the model
**Gate:** `make gate-m4` — client contract tests (stub provider); the
eval harness runs the golden set; citation checks and
refusal-on-unanswerable both pass. **There is no groundedness
threshold, deliberately.** Grounding is binary here: an answer whose
claim cites nothing supplied is withheld, not scored lower. A number to
clear would either sit at 100% and measure nothing, or turn a binary
check into something tunable — and tuning the check is how a
groundedness score stops tracking groundedness. Quality against a real
model is evidenced by the committed `make evals` run, not by a gate
number.
**Owner review:** prompts in version control; ADR on grounding strategy;
failure modes documented.

## M5 — Dashboard
**Goal:** A screen a program director would actually use.
- [x] Cohort overview: risk distribution, plus an **ENGAGEMENT trend**
      (weekly active learners) — deliberately not a risk trend. Only one
      `window_close` exists, so a risk line would be a single point, or a
      line drawn through repeated scoring runs of the same window, which
      is the double count that already cost three eval runs wearing a
      chart. The endpoint, its fields and the UI heading all say
      engagement, and a test asserts no risk-shaped key reaches the
      response — a chart labelled one way and read another is the
      misreading a risk line would invite. **Revisit trigger:** scoring
      several windows makes a real risk trend available; that is M3 work
      to reopen, not a gap here
- [x] Student drill-down (score, drivers with their non-additivity
      caveat, summary), Q&A panel with citations shown
- [x] FastAPI endpoints serving the above; React frontend
- [x] Demo mode: seeded synthetic cohort, cached LLM responses
**Gate:** `make gate-m5` — API contract tests; frontend builds; smoke test
against demo seed.
**Owner review:** UX coherent; screenshots/GIF captured for README.

## M6 — AWS deployment (IaC / solutions-architecture showcase)
**Goal:** The locally-proven stack (M0–M5) deployed to AWS entirely via
Terraform. Nothing cloud-side exists before this milestone; nothing here
is created by console-clicking.
- [ ] Terraform: remote state (S3 + state locking), reusable modules
      (web, api, data, observability), environments via tfvars
- [ ] ADR: deployment architecture — serverless-first (Lambda + API GW +
      CloudFront + S3) argued against at least two alternatives
      (containers/ECS, single EC2), with a written monthly cost model at
      idle and under demo load
- [ ] Database decision ADR: managed Postgres option (e.g. Aurora
      Serverless v2 scale-to-zero) vs precomputed demo store; cost and
      cold-start tradeoffs documented
- [ ] IAM: least-privilege roles per component; no wildcard actions
      without a written justification comment
- [ ] CI deploys via GitHub Actions OIDC role assumption — zero
      long-lived AWS keys anywhere
- [ ] Edge: CloudFront + ACM TLS + custom domain; WAF rate limiting on
      the API; demo-mode LLM responses cached
- [ ] Guardrails: AWS Budget + alarm; tags on every resource;
      teardown proven (`terraform destroy` leaves nothing billable)
**Gate:** `make gate-m6` — fmt/validate/plan clean; deployed smoke test
green; teardown/redeploy cycle documented.
**Owner review:** read every IAM policy; check the bill after 48h idle;
could you defend each choice in an SAA-style design review?

## M7 — Public demo on edtek.consulting + case study
**Goal:** A demo a prospect or hiring manager can reach in one click.
- [ ] Curated demo cohort (see data/generator/DESIGN.md §Demo cohort):
      seeded + reproducible, narrative students an onlooker can follow
- [ ] Demo hardening: DEMO_MODE banner ("All data is synthetic"),
      read-only role, rate limits verified from the public internet
- [ ] Embed/link on edtek.consulting: subdomain via Route53/CNAME;
      if iframed, CSP `frame-ancestors` allows edtek.consulting only
- [ ] README completed as case study (all TODOs resolved): problem,
      architecture diagram, ADR links, eval results, limitations,
      run-it-yourself
- [ ] 3–4 min narrated walkthrough video linked from README and site
**Gate:** `make gate-m7` — production smoke test from outside AWS;
README link check; demo loads with seeded cohort on a cold start.
**Owner review:** open it on your phone from the EDTEK site. Would you
hire this person? Would you buy this?
