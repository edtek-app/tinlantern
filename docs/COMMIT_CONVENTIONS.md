# Commit conventions

Format: Conventional Commits — `type(scope): summary` (≤72 chars,
imperative). Types: feat, fix, refactor, test, docs, chore, ci, perf.
Scopes: ingest, pipeline, warehouse, ml, llm, evals, app, infra,
devcontainer, data, docs.
(`warehouse` covers schema, DDL, and migration commits even though that
code lives under `pipeline/`.)

Body (when non-trivial): what changed and WHY — constraints, tradeoffs,
alternatives rejected. Reference ADRs (`See ADR-0003`) and milestones
(`[M2]`).

Examples:
- feat(ingest): add batch statement endpoint with idempotency on id [M1]
- fix(pipeline): make fact_activity load re-runnable; dedupe on natural key
- test(ml): add calibration check to risk model eval suite [M3]

Rules:
- One logical change per commit. No "WIP", "fixes", "update".
- Every commit belonging to a milestone's work carries that milestone's
  `[Mn]` tag — feat, fix, build, docs, chore alike. The tag marks
  membership in a milestone, not the type of the commit.
- Every commit message is drafted, then reviewed and approved by the owner
  before commit. Nothing lands unreviewed.
- History is portfolio evidence: write messages a hiring manager will read.
