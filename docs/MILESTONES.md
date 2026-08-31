# Milestones

Each milestone has: goal, acceptance criteria, an automated **gate**
(`make gate-mN`), and an **owner review checklist** done by a human before
merge. The active milestone is marked in `docs/PROGRESS.md`.

Loop per milestone: plan task → build → tests green → owner reviews commit →
commit → repeat → gate audit → owner review → PR merge → tag `mN`.

---

## M0 — Bootstrap & synthetic data
**Goal:** Repo skeleton runs; realistic synthetic xAPI data exists.
- [ ] Docker Compose brings up Postgres; `make setup` installs deps
- [ ] `data/generator/` produces configurable cohorts of xAPI statements
      (enrollments, course activity, assessment attempts, video events)
      with injectable "at-risk" behavior patterns
- [ ] Generator has unit tests incl. statement schema validation
- [ ] CI runs lint + tests on every push
**Gate:** `make gate-m0` — generator tests + schema validation + lint.
**Owner review:** commit log reads cleanly; ADR-0001 (stack) and ADR-0002
(synthetic-data-only policy) merged; generated data looks plausible.

## M1 — Ingestion
**Goal:** xAPI statements land durably via API.
- [ ] `POST /xapi/statements` (single + batch) with Pydantic validation
- [ ] Raw statements stored append-only; idempotent on statement id
- [ ] Rejection path: invalid statements logged, not dropped silently
**Gate:** `make gate-m1` — API tests incl. malformed input, duplicates,
batch loads of ≥10k statements.
**Owner review:** error handling deliberate; ADR for storage layout.

## M2 — Warehouse & ETL
**Goal:** Raw events become a queryable star schema.
- [ ] Dimensional model: fact_activity, fact_assessment; dims student,
      course, activity, date (documented in ARCHITECTURE.md with diagram)
- [ ] Incremental ETL job (idempotent, re-runnable) raw → warehouse
- [ ] Data-quality checks: row counts, null rates, referential integrity
**Gate:** `make gate-m2` — ETL runs twice on same input without dupes;
DQ checks pass; sample analytical queries return expected results.
**Owner review:** schema defensible in an interview; DQ failures fail loudly.

## M3 — Risk model
**Goal:** Per-student risk score with an honest evaluation.
- [ ] Feature pipeline from warehouse (engagement recency, assessment
      trajectory, pacing vs cohort)
- [ ] Baseline model (logistic regression) THEN one stronger model;
      compare in `ml/notebooks/`, promote winner to `ml/src/`
- [ ] Evaluation report: ROC-AUC, precision/recall at alert threshold,
      calibration; written to `evals/reports/`
- [ ] Scoring job writes scores + top contributing features per student
**Gate:** `make gate-m3` — feature/scoring tests; eval report generated;
model beats trivial baseline on held-out cohort.
**Owner review:** metrics honestly framed; ADR on model choice + threshold.

## M4 — LLM layer + evals
**Goal:** Grounded advisor summaries and cited Q&A — with an eval harness.
- [ ] Provider-abstracted LLM client (env-switchable: `anthropic` for
      local dev, `stub` for tests/CI, cloud provider set via infra config)
- [ ] Advisor summary: risk score + features → plain-language, actionable,
      non-deterministic-safe summary (no invented facts)
- [ ] NL Q&A over warehouse: question → SQL/semantic layer → answer WITH
      citations to the underlying data
- [ ] Eval harness in `evals/`: golden-question set, groundedness checks,
      refusal-on-unanswerable checks; results versioned in `evals/reports/`
**Gate:** `make gate-m4` — client contract tests (mocked); eval harness runs;
groundedness score above documented threshold.
**Owner review:** prompts in version control; ADR on grounding strategy;
failure modes documented.

## M5 — Dashboard
**Goal:** A screen a program director would actually use.
- [ ] Cohort overview (risk distribution, trend), student drill-down
      (score, drivers, summary), Q&A panel with citations shown
- [ ] FastAPI endpoints serving the above; React frontend
- [ ] Demo mode: seeded synthetic cohort, cached LLM responses
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
