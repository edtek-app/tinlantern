# ADR-0006: The dimensional model
Status: Accepted

## Context
M2 turns the append-only `raw` statements into a queryable star schema.
The model has to serve three consumers with different needs, and it has to
be defensible on sight:

- **M3's feature pipeline** wants engagement recency, gaps, assessment
  trajectory, and pacing against a cohort.
- **M4's NL→SQL layer** will generate queries against these tables. A
  model that needs tribal knowledge to query correctly will produce
  confidently wrong answers.
- **M5's dashboard** wants cohort rollups and per-student drill-down.

xAPI is an event stream, so the shape of the source is already atomic.
The design questions are grain, keys, and how assessment events relate to
activity events.

## Decision

**Four conformed dimensions:** `dim_student`, `dim_course`,
`dim_activity`, `dim_date`. **Two facts:** `fact_activity` and
`fact_assessment`.

**Surrogate keys, natural keys retained.** Every dimension has an integer
surrogate primary key, with the natural key kept as a UNIQUE column
(`learner_identifier`, `course_iri`, `activity_iri`, `full_date`). Facts
join on the surrogate. This is the conventional star schema: narrow joins,
and facts insulated from natural keys changing shape.

`dim_date.date_key` is the exception, deliberately: it is `YYYYMMDD` as an
integer rather than an opaque sequence, so a query result is readable
without joining the dimension back in. Date is the one dimension where
that convenience is worth more than uniformity.

**Type 1 dimensions. SCD2 rejected, not overlooked.** Attributes are
overwritten; no history is kept. Nothing in this data has history worth
tracking — a learner's opaque identifier does not change, a course's title
is not analytically interesting when it does, and an activity IRI is
immutable by construction. SCD2 would add effective-dated rows, current-row
flags, and a join predicate on every query, to answer questions ("what was
this student's cohort *at the time*?") that this dataset cannot pose. If
roster import arrives (the known v1 gap in ARCHITECTURE.md), student
attributes would start changing meaningfully and this should be revisited.

**`fact_activity` is the atomic record: one row per xAPI statement, every
verb.** Grain is enforced by `UNIQUE (statement_id)`.

**`fact_assessment` holds graded outcomes: one row per `passed`,
`failed`, or `submitted`,** with scores, success, completion, and attempt
number. Same grain enforcement.

**The two facts overlap on purpose.** An assessment statement produces a
row in *both*. The alternative — excluding graded verbs from
`fact_activity` — avoids double counting but splits the atomic record
across two tables and turns the most common question ("what did this
learner do?") into a permanent union. That optimises against a mistake an
analyst makes once, at a cost paid on every query.

Correct usage, stated plainly:

| question | table |
|---|---|
| how often, how recently, what gaps | `fact_activity` |
| what was scored, what was passed | `fact_assessment` |
| **both, summed together** | **never — double counts** |

Documentation alone would not be enough, so the overlap is stated where
someone actually looks: a `COMMENT ON TABLE` on each fact, visible from
`\d+` in psql, and a paragraph beside the diagram in ARCHITECTURE.md. An
ADR nobody opens is not a guard.

**Idempotency is a schema property, not a loader promise.** Because
`statement_id` is UNIQUE on both facts, re-running the ETL over the same
statements is stopped by the database rather than by the loader's own
bookkeeping. M2's gate requires the ETL to run twice without duplicates;
that guarantee now holds even if the ETL's watermark logic is wrong.

**Facts carry `ingest_seq`** from `raw`, as provenance and to let queries
page "what arrived since". Per ADR-0005 it stays an opaque marker — never
joined on, never read as event time.

**The warehouse is not append-only.** `raw` is the source of truth and is
protected by a trigger; the warehouse is derived and rebuildable, so it
carries no such restriction. Dropping and rebuilding it is a legitimate
recovery path.

## The ETL's two consequential choices

Both touch the platform's premise rather than its plumbing, so they belong
in the record rather than in a commit body a reader will not find.

**The course comes from `context.contextActivities.parent`, never from
parsing the activity IRI.** Parsing is the obvious shortcut — our own
generator emits `.../course/{slug}/module/1`, and a regex would work
today. It would also hard-code this generator's URL convention into the
ETL, and the platform's stated premise (ARCHITECTURE.md) is consuming
*any* xAPI emitter. Another system's IRIs are `urn:x-acme:offering:...` or
worse, and nothing about their shape is guaranteed. The context is the
xAPI-native place to say what a statement belongs to, and it is the only
answer that survives contact with an emitter that is not ours.

A test loads statements whose IRIs follow no TinLantern convention and
asserts the course still resolves, so the shortcut cannot be reintroduced
as an optimisation without the suite noticing.

**The ETL inserts, then corrects.** `attempt_number` is recomputed after
each batch over `occurred_at`, rather than derived at insert time as "one
more than the count so far". The cheap version assumes arrival order
matches event order, and ADR-0005 is explicit that it does not:
`ingest_seq` records when we received a statement, `occurred_at` when the
event happened, and nothing relates them. A statement arriving late but
timestamped early must renumber every attempt that followed it — under
insert-time derivation it would silently mis-number them instead.

This is why the ETL is not a pure insert, and why the warehouse carries no
append-only trigger: it is derived and correctable precisely so that a
late arrival can be absorbed. A test loads attempts out of event order
across two separate runs and asserts the final numbering follows event
time.

## Consequences
- **Easier:** narrow joins; a model an interviewer recognises immediately;
  re-runnable ETL by construction; the warehouse can be rebuilt from `raw`
  after a model change, which is why `raw` keeps statements verbatim.
- **Harder / accepted:** the overlap must be taught, and enforced by
  comments rather than by the schema — nothing stops someone summing both
  tables. The alternative was worse. Attempt numbering is computed by the
  ETL rather than present in the source.
- **Given up:** slowly changing dimensions, and a single non-overlapping
  event table.
- **Revisit triggers:** roster/SIS import lands and student attributes
  start changing, which is the case SCD2 exists for; a verb dimension
  becomes worth conforming (currently `verb` is a text column on both
  facts, since eleven values do not earn a dimension); M4's generated SQL
  is observed summing across both facts, which would mean the comments are
  not doing their job and the model needs a guarded view instead.
