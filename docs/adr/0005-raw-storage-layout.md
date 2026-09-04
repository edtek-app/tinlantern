# ADR-0005: Raw statement storage layout
Status: Accepted

## Context
M1 lands ingested xAPI statements durably. `raw` is the append-only
landing zone and `warehouse` is the star schema built from it (ADR-0001),
so the question here is narrow: what shape should the landing table take,
and what must it guarantee?

Three forces:

- **M2 needs to page through new rows** incrementally and re-runnably. It
  needs a reliable "everything above this marker is new".
- **Ingestion must be idempotent.** Clients retry timed-out requests, and
  a retry must not create a second row.
- **Nothing may be lost silently.** A statement that is refused has to
  leave evidence. Data that vanishes at ingestion surfaces much later as
  warehouse numbers that fail to reconcile, with nothing to explain them.

## Decision

**`raw.statements` keeps the statement verbatim.**

| column | purpose |
|---|---|
| `statement_id UUID PRIMARY KEY` | identity; the idempotency key |
| `payload JSONB NOT NULL` | the statement exactly as received |
| `received_at TIMESTAMPTZ` | when it arrived |
| `ingest_seq BIGSERIAL` | ingestion order; M2's watermark |

Shredding into columns is the warehouse's job. `raw` stores what arrived
so that a future model change can be rebuilt from the original data rather
than from whatever we thought was worth extracting at the time.

**`ingest_seq` exists because a timestamp cannot be a watermark.** A batch
insert shares `received_at` to the microsecond, so "everything after time
T" either skips rows sharing T or replays them. Clocks are also not
monotonic — NTP corrections move them backwards, and a watermark that
moves backwards silently re-imports. A sequence has neither problem.

`ingest_seq` is **ingestion metadata, not statement data**. It describes
when we received something, not anything a learner did. M2 reads it as an
**opaque high-water mark only**: compare it, store the maximum, page above
it. It must never be treated as ordered by event time, joined on, or
surfaced as a fact attribute.

**Idempotent, but never silently lossy.** Insert uses
`ON CONFLICT (statement_id) DO NOTHING`. On conflict the held payload is
compared with the incoming one:

- **Identical** → the client is retrying. Success, one row.
- **Different** → a different event wearing a taken name. Refused, with a
  409 at the endpoint, and written to `raw.rejections`.

`DO NOTHING` alone would have been one line and wrong: it silently
discards the second statement, and the loss is invisible until the
warehouse disagrees with the source system.

Comparison is **canonical** — sorted keys, fixed separators. JSON objects
are unordered, so a client that serialises the same statement with its
keys in a different order is retrying. Reporting that as a conflict would
punish correct behaviour.

**`raw.rejections` is the single rejection path.** A durable table rather
than a log line, because **a log line is not queryable evidence**: nobody
can join it, count it, or replay from it, and it ages out of retention
exactly when an investigation needs it. A rejection must be readable back
with enough to act on — which statement, why, the offending payload
verbatim, and when. A rejection table nobody can act on fails the same way
a log line does, one step later.

The row carries the refused payload unmodified, a machine-readable
`reason`, a human-readable `detail`, and `received_at`. `statement_id` is
nullable — a malformed payload may not have a parseable one. Id conflicts
here and validation failures at the endpoint both write through
`record_rejection`, so a rejection means the same thing wherever it came
from.

**Append-only is enforced by the database.** A statement-level trigger on
both tables raises on `UPDATE`, `DELETE`, and `TRUNCATE`. Statement-level
rather than row-level, so an `UPDATE` matching no rows still fails instead
of looking like a successful no-op.

*Escape hatch, documented so an incident has a path instead of an
improvisation:* correcting raw data requires an Alembic migration that
drops the trigger, performs the correction, and recreates it — in one
reviewed, reversible change. `alembic downgrade 0001 && alembic upgrade
head` rebuilds both tables empty, which is the right move for a
development database.

## Consequences
- **Easier:** M2's incremental load is a `WHERE ingest_seq > :mark` scan;
  retries are safe; a lost statement is a contradiction rather than a
  possibility; the raw layer can be replayed into a redesigned warehouse.
- **Harder / accepted:** correcting bad raw data is a migration, not a
  quick `UPDATE`. That friction is the point — it makes tampering with the
  source of truth a reviewed act. The conflict path costs one extra read,
  on the conflict branch only.
- **Given up:** in-place correction, and the simplicity of unconditional
  `DO NOTHING`.
- **Measured throughput (M1, local container):** 941 statements/sec —
  192,431 statements in 204s at batch size 500, through the HTTP endpoint.
  The cost is per-statement `INSERT` round trips: `store_statements` loops
  rather than issuing multi-row inserts, because per-statement conflict
  detection is what the guarantees above rest on. Deliberately not
  optimised in M1; the trigger for that work is a real constraint, not a
  number that looks improvable.
- **Revisit triggers:** the measured throughput becomes a constraint —
  M6's cost model or M7's demo seeding finds 941/s unacceptable — in which
  case batching the insert and its conflict comparison needs an ADR of its
  own; ingestion volume makes per-statement conflict reads measurable; a
  second producer needs to write to `raw` and the sequence becomes
  contended; retention policy
  requires deleting old statements, which would need the trigger to permit
  a documented archival path.
