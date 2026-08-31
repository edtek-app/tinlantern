# TinLantern — Product Requirements

## Problem
Institutions run learning across an LMS (Moodle etc.), authored content
(Articulate/xAPI packages), and other tools. Each system sees only its own
slice, so struggling learners surface late — often at midterm grades, when
intervention is least effective. Staff need earlier, cross-system signals
and explanations they can act on.

## Product
An open-source (AGPL-3.0) analytics layer that sits beside the learning
stack. Anything that speaks xAPI can feed it. It warehouses the event
stream, scores learner risk with ML, and uses an LLM to produce grounded
advisor summaries and cited natural-language Q&A.

Tagline: light made from tin cans. (xAPI's original name was the Tin Can
API — see ADR-0000.)

## Users
- **Program director / student-success staff (primary):** monitors cohort,
  triages alerts, drills into a learner, asks questions in plain language.
- **Institution admin (secondary):** connects sources (e.g. Moodle
  logstore_xapi), manages deployment.
- **EDTEK / integrator (tertiary):** deploys and customizes for clients.

## v1 scope
1. xAPI ingestion endpoint (single + batch, idempotent, validating)
2. Synthetic data generator (sole data source — FERPA-clean by design)
3. Star-schema warehouse + idempotent incremental ETL + DQ checks
4. Risk scoring with honest evaluation and per-learner drivers
5. LLM advisor summaries + cited NL Q&A, with an eval harness
6. Advisor dashboard with demo mode
7. Serverless AWS deployment (Terraform), live demo URL

## Non-goals (v1)
- Real institutional data of any kind
- LMS plugins (consume existing emitters instead), Caliper, SIS sync
- Multi-tenancy, billing, SSO — revisit only if commercialized
- Intervention workflow (case management); we surface signal, not process

## Success criteria
- A stranger can run the whole stack locally with three commands
- Live demo loads in demo mode at near-zero idle cost
- Eval report shows risk model beats baseline; LLM groundedness above
  documented threshold; limitations honestly stated in README

## Sensitivity note
Early-alert systems draw a "student surveillance" critique. Design posture:
synthetic-only repo, transparency about features driving each score, no
per-learner data exposed beyond the advisor role in the UI. Document this
in README — it is a feature, not a disclaimer.
