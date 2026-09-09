# ADR-0009: Operational safety at the deployment boundary
Status: Accepted

## Context
Two decisions made during M5 share a shape, which is why they share a
record: **each is a property that must hold regardless of how the
environment is configured or what the caller happens to be doing.** Both
were previously explained only in code and in a gitignored progress
file, and M6 is the first reader who needs them — it sets the
environment variables, and it runs the code against a deployed database.

They are separated below because they are independent; a reader needing
one does not need the other.

---

## 1. `DEMO_MODE` overrides `LLM_PROVIDER`

**A publicly reachable demo that can spend money or exercise a
credential is a liability.** M7 makes this thing publicly reachable, and
the question is what stops a visitor's question from reaching a paid
API.

The obvious arrangement is for `DEMO_MODE` to select data and
`LLM_PROVIDER` to select a provider, each doing one job. **Rejected: it
makes the safety property depend on whoever set the environment
variables.** Two settings that must agree are two chances to disagree,
and the failure is silent and expensive.

**So `DEMO_MODE=true` forces the `stub` provider and ignores
`LLM_PROVIDER` entirely.** A safety property that defers to
configuration is not a safety property.

**The override is loud, in three places.** The requested value is kept
so the explanation can name what was ignored; the reason is available as
a sentence; and `GET /health` reports which provider actually won,
`demo_mode`, and `provider_overridden`. **An override decided inside a
process and reported only in logs is a property nobody can check without
finding the logs first** — this one is verifiable from outside with a
single request, which is what M6's smoke checks need.

**It fails toward honouring the real provider.** Anything but an
explicit true — `""`, `"false"`, `"no"`, `"0"`, `"off"` — leaves demo
mode off. A misread value cannot silently serve recordings in
production; the worst case is a demo that calls a real model, which
costs money rather than integrity.

**Consequence, accepted:** setting `DEMO_MODE=true` alongside
`LLM_PROVIDER=anthropic` silently *works* but ignores half of what was
asked. The mitigation is that it says so rather than pretending both
took effect.

## 2. Telemetry writes in its own transaction

`warehouse.llm_call` records what every provider call did. It was
written on the caller's connection, which is the natural thing to do and
was wrong.

**Two correct decisions combined into a silent hole.** Generated SQL
runs under `SET LOCAL transaction_read_only` (ADR-0008), which applies
to the **remainder of the caller's transaction**, not just the query.
And `record()` swallows its own failures deliberately, because a metrics
write that takes down an endpoint is worse than a missing metric. So
every telemetry write after a generated query was refused by the
database and discarded without a trace.

**What that cost:** the `qa` outcomes where a query ran — `ok` and
`refused`-after-verification, the two most common results — were never
recorded, while malformed and transport failures, which raise *before*
the query, were. **The table held Q&A's failures and none of its
successes.** Any rate computed from it would have been wrong in the
direction that makes the system look broken.

**So `record()` opens its own transaction**, following the precedent M1
set for rejections: *the record must outlive the constraints of the
thing it describes.* A rejection that vanishes with the batch it
describes is not a rejection; a metric that vanishes with the read-only
transaction it describes is not a metric.

**A swallowed failure is now logged.** Silence is right for one
transient and wrong for a persistent fault — a write that fails every
time is a misconfiguration, and it looked identical to a system with
nothing to report. It still does not raise.

**An unclassified failure is recorded before being re-raised.** The
endpoint re-raises what it cannot classify, which is right: a bug must
not be served as a provider outage. But recording nothing on the way out
would leave the same hole from the other direction — the table reporting
health while every request returns 500.

**Why this was invisible for so long:** every telemetry test drove a
path that raises *before* the query runs, so none touched the path where
telemetry was broken. **A guard exercised only where the mechanism it
protects cannot fail is untested.**

## Consequences
- **Easier:** M6 can verify the demo's provider from outside the
  process; observability survives whatever the request is doing.
- **Harder / accepted:** an explicit `LLM_PROVIDER` is ignored under
  demo mode; telemetry costs a second connection per recorded call,
  which at this volume is not measurable.
- **Given up:** nothing that was working.
- **Revisit triggers:** a deployment needs demo mode *and* a real
  provider simultaneously — that is a different feature and this
  decision is the wrong place to bend for it; or telemetry volume grows
  enough that a connection per call matters, at which point batching
  belongs behind the same function rather than in its callers.
