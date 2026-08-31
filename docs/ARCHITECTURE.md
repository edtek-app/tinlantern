# Architecture

## System view
Sources (Moodle logstore_xapi, Articulate content, any xAPI emitter — all
simulated by data/generator in dev) → **ingestion API** (LRS-style
endpoint, validate + store raw, idempotent) → **ETL** (incremental,
re-runnable) → **warehouse** (star schema: fact_activity, fact_assessment;
dims student/course/activity/date) → **ML** (feature pipeline, risk model,
scoring job with per-learner drivers) → **LLM layer** (provider-abstracted;
summaries + NL→SQL Q&A grounded with citations) → **dashboard** (FastAPI +
React) . Deployment: Terraform, serverless-first on AWS.

Diagram: docs/diagrams/system.svg (create in M2; embed in README).

## Key stances (each gets a full ADR when implemented)
- Standards-based decoupling: no LMS plugins; consume xAPI emitters.
- Synthetic-only data in repo and demo (FERPA posture).
- LLM answers must cite warehouse rows/queries; unanswerable → refuse.
- Known v1 limitation: rosters/enrollment context typically live in
  SIS/LMS, not the event stream. Real deployments add a roster import.
  Documented, deliberately out of scope.
