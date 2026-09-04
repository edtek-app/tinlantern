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

## The dimensional model (M2)

Four conformed dimensions, two facts. Grain, key strategy, and the
deliberate overlap between the facts are argued in ADR-0006.

```mermaid
erDiagram
    dim_student   ||--o{ fact_activity   : "did"
    dim_course    ||--o{ fact_activity   : "within"
    dim_activity  ||--o{ fact_activity   : "acted on"
    dim_date      ||--o{ fact_activity   : "on"
    dim_student   ||--o{ fact_assessment : "attempted"
    dim_course    ||--o{ fact_assessment : "within"
    dim_activity  ||--o{ fact_assessment : "assessed by"
    dim_date      ||--o{ fact_assessment : "on"
    dim_course    ||--o{ dim_activity    : "contains"

    dim_student {
        bigint student_key PK
        text   learner_identifier UK "opaque, never an email (ADR-0002)"
        text   account_home_page
    }
    dim_course {
        bigint course_key PK
        text   course_iri UK
        text   course_slug
        text   title
    }
    dim_activity {
        bigint activity_key PK
        text   activity_iri UK
        text   activity_type "course|module|video|assessment|assignment"
        bigint course_key FK
        int    module_index
    }
    dim_date {
        int     date_key PK "YYYYMMDD, readable without a join"
        date    full_date UK
        int     iso_week
        int     day_of_week
        boolean is_weekend
    }
    fact_activity {
        bigint      activity_event_key PK
        uuid        statement_id UK "grain: one row per statement"
        text        verb
        timestamptz occurred_at
        bigint      ingest_seq "provenance; opaque marker (ADR-0005)"
    }
    fact_assessment {
        bigint      assessment_result_key PK
        uuid        statement_id UK "grain: one row per graded outcome"
        text        verb "passed|failed|submitted"
        numeric     scaled_score
        numeric     raw_score
        boolean     success
        int         attempt_number
        timestamptz occurred_at
        bigint      ingest_seq
    }
```

**The two facts overlap, by design.** `fact_activity` is the atomic
record and holds *every* statement, assessment events included.
`fact_assessment` holds the graded outcomes again, with their scores.

- **Behaviour and engagement questions** — how often, how recently, what
  gaps — read `fact_activity`.
- **Outcome and attainment questions** — what was scored, what was passed
  — read `fact_assessment`.
- **Never sum across both.** An assessment event is counted in each. Both
  tables carry a Postgres comment saying so, because an analyst reads
  `\d+` far more often than an ADR.

Idempotency is a schema property, not a loader promise: `statement_id` is
UNIQUE on both facts, so re-running the ETL over the same statements is
stopped by the database.

Diagram: rendered above from source, so it cannot drift silently from the
model — a test asserts every table in `pipeline.warehouse` appears here.

## Key stances (each gets a full ADR when implemented)
- Standards-based decoupling: no LMS plugins; consume xAPI emitters.
- Synthetic-only data in repo and demo (FERPA posture).
- LLM answers must cite warehouse rows/queries; unanswerable → refuse.
- Known v1 limitation: rosters/enrollment context typically live in
  SIS/LMS, not the event stream. Real deployments add a roster import.
  Documented, deliberately out of scope.
