# Commit conventions

Format: Conventional Commits — `type(scope): summary` (≤72 chars,
imperative). Types: feat, fix, refactor, test, docs, chore, ci, perf.
Scopes: ingest, etl, warehouse, ml, llm, evals, app, infra, data, docs.

Body (when non-trivial): what changed and WHY — constraints, tradeoffs,
alternatives rejected. Reference ADRs (`See ADR-0003`) and milestones
(`[M2]`).

Examples:
- feat(ingest): add batch statement endpoint with idempotency on id [M1]
- fix(etl): make fact_activity load re-runnable; dedupe on natural key
- test(ml): add calibration check to risk model eval suite [M3]

Rules:
- One logical change per commit. No "WIP", "fixes", "update".
- Every commit message is drafted, then reviewed and approved by the owner
  before commit. Nothing lands unreviewed.
- History is portfolio evidence: write messages a hiring manager will read.
