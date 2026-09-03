# ADR-0002: Synthetic data only
Status: Accepted

## Context
Early-alert analytics over student learning data draws a "student
surveillance" critique and sits in FERPA-regulated territory. TinLantern
is open source with a publicly reachable demo, so any real data would be a
liability in the repo, in CI, and in the deployed environment.

## Decision
Only synthetic data ever enters this repository or any deployed
environment. The generator in `data/generator/` is the **sole** data
source for development, tests, and demos — no real institutional data in
fixtures, notebooks, or the demo.

- Generated cohorts are written to a gitignored `data/output/` path.
- **Learners are identified by an opaque xAPI `account`, never an `mbox`.**
  `homePage` is the cohort's configured base IRI and `name` is an id like
  `s-00417`. Generating thousands of fictional `mailto:` addresses would
  put email-shaped data into a repository whose whole claim is that nothing
  resembling a learner record enters it — and an address in a screenshot, a
  log line, or a public demo cannot be un-seen. An opaque id cannot be
  mistaken for contact details. M7's named narrative students carry a
  display `name` for a curated few; the identifier stays opaque.
  A roster test scans serialized output for `@`, `mbox`, and `mailto`, so
  this is a gate rather than a habit.
- The generator also emits a ground-truth sidecar (learner archetype +
  outcome) consumed **only** by `evals/`. It must never flow through the
  ingestion API or the warehouse; an automated leakage guard enforces this
  from M3 onward.

## Consequences
- **Easier:** no PII handling, no data-use agreements, no FERPA compliance
  burden; the demo is safe to expose publicly; any contributor can run the
  whole stack.
- **Harder / accepted:** evaluation quality is bounded by how realistic
  the generator is. "Does this generalize to real institutional data?" is
  an explicit, documented open question — not a solved problem.
- Stated as a feature in the README, not buried as a disclaimer.
- **Revisit trigger:** only if the project is commercialized and a
  customer deployment requires real data — which would be a separate,
  isolated codebase and deployment, never this repository.
