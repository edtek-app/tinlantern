# ADR-0008: Grounding strategy for advisor summaries
Status: Accepted

## Context
M4 puts a language model in front of a risk score an advisor will act on.
The failure that matters is not a clumsy sentence — it is a confident
sentence about a number nobody computed. "Attendance has fallen to 41
percent" reads exactly like the true claims beside it, and an advisor has
no way to tell them apart. Whatever we build has to make that difference
visible to *code*, because it is not visible to the reader.

Three approaches were considered.

**Numeric grounding on free prose.** Ask for a paragraph, extract every
number, require each to trace to a supplied fact. Cheap, and it catches
fabricated statistics — the highest-consequence lie. It cannot see a
fabricated prose claim ("she has stopped attending"), which carries no
number at all.

**LLM-as-judge.** A second model call scores groundedness. Rejected: it
cannot run deterministically against the stub provider, so CI could not
execute it, and the judge's own reliability is unestablished. It would
move the unverified step rather than remove it.

**Structured claims.** The model returns claims, each naming the fact it
rests on; code checks each one and then arranges them into prose.

## Decision

**Structured claims, with the numeric check layered on the rendered
prose.**

The model is constrained to `SUMMARY_SCHEMA` — a list of
`{text, source}` claims plus one `suggested_next_step` — via structured
output, not a prompt-level request for JSON. A prompt-level request is a
preference the model may decline under pressure, and a summary falling
back because the JSON did not parse would blame the wrong layer.

Verification then runs in two overlapping layers:

1. **Per claim.** Its `source` must be a key that was actually supplied,
   and every figure in its text must trace to a supplied fact.
2. **On the rendered prose.** The same numeric test against what a human
   will read. This layer exists to catch **the renderer**: the code that
   assembles claims into text is as capable of introducing a figure as
   the model that wrote them, and layer 1 cannot see it. Keeping both is
   the point — the second is not redundant with the first, it watches a
   different component.

**What this costs, stated as a trade.** The model no longer writes the
briefing. It supplies verified facts and our renderer arranges them, so
the prose is flatter and more repetitive than an unconstrained model
would produce — no connective reasoning across facts, no varied
sentence structure, four or five short claims in a row. We are buying
checkability with fluency. That is the trade, and it was made knowingly:
an advisor can work with plain prose, and cannot work with prose they
have to fact-check.

Q&A (the next M4 task) needs per-claim citations regardless, so this is
the shared foundation rather than a heavier alternative to numeric-only
checking. Building the weaker check first would have meant building it
twice.

**The context is a curated fact set** (`LearnerFacts`), not the learner's
whole feature row. A fact that was never supplied cannot be fabricated by
paraphrase, and a context this small can be audited by eye. What an
advisor needs, and why these fields:

| field | why an advisor needs it |
|---|---|
| `risk`, `alerted`, `threshold` | the number they opened the record for, and whether it crosses the line |
| `window_close` | how old the judgement is — a four-week window closed in February says nothing about April |
| `cohort_size`, `cohort_alerted` | whether this learner is unusual or the whole cohort is |
| `drivers` (ranked, with contributions) | what to actually ask about; a score with no driver is not actionable |
| `caveat` | so the model is told, in the context, that the drivers do not decompose |
| `model_version` | which code produced it, for anyone reconciling a screenshot later |

The curation risk is real: a fact set can be perfectly grounded and
useless. Naming the fields and the reason here makes "grounded but
unhelpful" a reviewable claim rather than an invisible one.

**There is deliberately no cohort median in the fact set,** although
"7 failures against a typical 1" would be the most useful sentence in
the summary. The median profile the contributions were ablated against
is fitted at scoring time and **not persisted** (`ml/src/drivers.py`), so
any median recomputed now could differ from the one those contributions
describe. A comparison against the wrong median is a quiet error of
exactly the kind this ADR exists to prevent. Adding it requires the
drivers payload to carry the fitted median — an M3-payload change, and a
real gap recorded rather than worked around.

**When verification fails, the advisor still gets a summary.** A template
built by code from the same curated facts is grounded by construction,
and is tested by putting it through the same numeric and phrasing checks
rather than by assertion. This makes the language model an *enhancement
over something that always works* rather than a dependency: refusing
would mean a program director opens a learner and sees nothing, which is
worse than plain prose, and it would make the feature's availability
hostage to a provider.

**Transport failures are not fallbacks — they propagate.** `summarise`
converts a refusal and a provider that is not built yet; it does not
catch a 429, a 5xx, or a connection error. Turning a retryable outage
into plausible template prose would disguise a broken provider as a
working feature, which is precisely what the fallback rate exists to
expose. **Retry, timeout, and backoff policy belongs to the caller** —
concretely, to M5's endpoint, which owns the request the advisor is
waiting on. A caller that swallows these instead of retrying them has
moved the problem, not solved it.

The fallback is marked in the text (`FALLBACK_MARKER`) and its reason is
recorded and distinguishable — `verification-failed`,
`provider-refused`, `provider-unavailable`. `fallback_rate` derives the
share from the results actually served, and the eval harness reports it:
a system quietly serving templates half the time looks healthy on every
other signal.

## Failure modes

Named because a mechanism whose limits are unstated gets read as
coverage.

**Individually true, collectively misleading. Nothing here addresses
this.** Every claim can cite a supplied fact and the summary can still
mislead by selection, emphasis, or omission — leading with a driver that
is second by contribution, omitting that the cohort as a whole alerted
heavily, framing a borderline score as decisive. Both layers check
*claims*; neither checks the *argument*. This is the largest open gap in
M4's grounding and no mechanism in this ADR reduces it.

**The additivity check is a substring blacklist, not a semantic one.** It
matches phrasings like "accounts for" and "sum to". It cannot tell a
claim from its negation — the caveat we supply says the contributions
"do NOT sum to the risk score", which contains a forbidden phrase while
asserting the opposite. The check is therefore scoped to generated text
with the supplied caveat's wording removed first. A model that invents a
*new* way to imply decomposition will not be caught.

**The numeric check exempts supplied literals.** The learner identifier,
the model version, and the window date are removed before extraction, so
their digits are not read as quantities. A *different* identifier or date
stays visible, so the exemption cannot smuggle a figure through — but it
is an exemption, and it is why `s-00417` is not flagged.

**Percentages are accepted as a rendering of a proportion.** "82%" of a
risk of 0.82 passes. This is deliberate — rejecting it would push the
model toward less useful prose to satisfy a rule that was measuring
formatting — but it does widen what counts as grounded.

**The real provider's structured-output path is not exercised by the
gate.** CI runs the stub, which parses a hand-written canned response.
Only `make evals` sends the schema to a live model. This is why a
committed real-provider eval run is an M4 acceptance criterion.

## Consequences
- **Easier:** every figure an advisor reads is traceable to a stored row;
  the citation machinery Q&A needs already exists; the feature degrades
  to plain prose instead of to nothing.
- **Harder / accepted:** summaries read flatter than an unconstrained
  model's; every new fact an advisor wants is a schema and prompt change,
  not a prompt tweak; the canned response must be re-registered whenever
  a prompt is edited.
- **Given up:** fluent connective prose, and any claim that would require
  the model to compute something from what it was given.
- **Revisit triggers:** **the golden question set is authored by the
  same person who wrote the reference queries and the schema description
  the model plans against**, so it cannot detect a misconception shared
  across all three — a question phrased around an assumption, graded by
  an oracle carrying the same assumption, answered from a description
  that encodes it. Nothing in M4 reduces this, and a better score does
  not: the arrangement is what limits the evidence, not the result. An
  independently authored question set, or questions drawn from real
  advisor queries, is what would make this a stronger instrument. Named
  here so a reader knows it was seen rather than missed; the score in
  every report carries it in the opening framing. The drivers payload
  starts carrying its fitted median, which unlocks the cohort comparison. **Ruled in advance, so it
  is not relitigated:** that change must persist BOTH the learner's
  value and the fitted median used at ablation time, keyed to the same
  `model_version`. Recomputing either later reintroduces exactly the
  mismatch this ADR avoids by omitting the comparison. It is its own
  task, raised when M5 shows whether advisors need that sentence; fallback rate against the
  real provider is high enough to suggest the schema or the prompt is
  fighting the model rather than constraining it; M5 finds that advisors
  want the argument, not the claims, which would put the
  collectively-misleading gap on the critical path.

---

# Addendum: citations for natural-language Q&A

Status: Accepted. Added with the Q&A layer.

Q&A grounds against **returned query rows** rather than a curated fact
set, but it is the same mechanism, not a second one: the model returns
claims, each naming its source, and code checks each claim against the
thing it named. Only the source changes — a row label instead of a fact
key. A separate ADR would have split one strategy across two records.

**Citations are positional row labels** (`row:0`, `row:1`), not primary
keys. Most of what an advisor asks is an aggregate, and an aggregate has
no key to cite; a scheme that only worked for row-level queries would
push the model toward citing nothing on exactly the questions people ask
most. The rows and **the executed SQL** are both retained on the result,
so a dashboard can show what was run beside what was cited, and an eval
artifact records how an answer was reached rather than only that it was
grounded.

**A claim is checked against the row it named, not the result set.** A
claim citing one learner while quoting another's score reads perfectly
and is wrong; grounding against every returned row would accept it.
Checking the cited row specifically is what makes the citation mean
something rather than decorate the sentence.

## The database is the boundary; the Python check is not

Generated SQL runs behind two independent database protections:

1. **`tinlantern_readonly`** (migration `0006`) — SELECT on `warehouse`,
   nothing else, and no access to `raw` at all.
2. **A read-only transaction** for the duration of the query.

**Why `NOLOGIN` + `SET LOCAL ROLE` rather than a separate login role and
a second connection URL.** A reviewer will ask, because a second
connection is the more familiar shape. The privilege enforcement is
identical — PostgreSQL checks the current role, however it was reached —
but a login role needs a password, which means either a secret in the
repository or a second credential to distribute and rotate, against a
project rule that says neither. The threat being defended against is
**generated SQL doing something it should not**, and `SET LOCAL ROLE`
answers that completely. It does not defend against our own connection
pool being compromised; neither would a second URL held by the same
process. Buying nothing for the cost of a credential is the wrong trade.

`SET LOCAL` (not `SET`) for both settings, so they revert when the
surrounding transaction ends and a pooled connection that once answered
a question does not silently refuse writes for whatever uses it next.
That revert is asserted across two real transactions with a **commit**
between them — a rollback reverts a session-level `SET` too, so a test
that rolls back cannot tell the two apart, and the first version of that
test passed with the boundary deliberately broken.

Each refuses an INSERT, an UPDATE, and a DROP **with the other absent**,
and the tests assert that separately — two layers only ever tested
together are one layer with extra steps. A further test calls the
executor directly with a write statement, bypassing the Python check
entirely, because that is the arrangement's actual claim.

**`statement_problem` in `app/llm/query.py` is a fast-fail convenience
and is NOT the security boundary.** It turns "the model returned prose,
or a write, or two statements" into a clear, retryable rejection instead
of an opaque database error. It is pattern matching over text and it is
wrong in ways nobody predicts. **No database protection may ever be
removed on the grounds that the SQL is validated** — the role's own
`COMMENT ON ROLE` says so too, where a DBA reading `\du` will see it.

## The schema description is curated, and its drift is tested

The model plans against a hand-written description
(`app/llm/schema_context.py`), not `information_schema`. Introspection
would supply every column name for free and stay current by itself, but
it cannot convey the one thing this schema most needs said: **ADR-0006
made `fact_activity` and `fact_assessment` overlap on purpose**, an
assessment statement produces a row in both, and summing across them
double counts. That ADR named this exact situation — "M4's generated SQL
is observed summing across both facts" — as its own revisit trigger, on
the grounds that it would mean the table comments were not doing their
job. A description the model reads *before* it writes SQL is the earlier
place to say it, and generated SQL never sees a `COMMENT ON TABLE`.

Curation's cost is drift, so drift is asserted against the live schema in
both directions. The direction that matters is **undescribed**: a table
or column the database has and the description omits becomes silently
wrong SQL, because a model that guesses at what it was not told about
writes plausible nonsense. A described column that no longer exists fails
loudly the first time it is used, which is the safer failure. Pipeline
bookkeeping tables are described as **excluded rather than omitted** —
silently leaving `etl_rejections` out invites a guess; naming it as
off-limits forecloses one.

## No fallback here, unlike advisor summaries

A summary has a curated fact set that code can always render into
grounded prose, which is why its failure mode is a template. An arbitrary
question has no such thing. So when the warehouse cannot answer a
question — or when an answer fails verification — Q&A **refuses and says
what is missing**. Showing an unverified answer with a caveat attached
would be precisely the failure this ADR exists to prevent, wearing a
disclaimer; and a question about attendance answered with a real number
from a different quantity is undetectable by the person reading it.

## The check's fundamental weakness, stated as a class

Four defects of the same shape have now been found, all of them by
running against a real model and none by any test:

| # | the check was wrong about | cost |
|---|---|---|
| 1 | thousands separators — "192,431" read as 192 and 431 | 4 failures |
| 2 | figures the *question* supplied, echoed in the answer | 1-2 failures |
| 3 | digits inside a *column name* the query itself produced | 2 failures |
| 4 | a timestamp's time of day, and how a timestamp is rendered | 3 failures |

**The class: numeric grounding compares RENDERED PROSE against STRUCTURED
VALUES, so every mismatch between how a value is rendered and how it is
represented is a false rejection.** Not an oversight in four places — one
weakness with four instances, and there will be more, because the set of
ways a model may legitimately render a value it was correctly given is
open-ended. Currency, units, ordinals, spelled-out numbers ("three
learners"), scientific notation, and locale-specific separators are all
unhandled today.

The failures are safe in direction — a false rejection withholds a
correct answer, it never shows a wrong one — but they are *not* cheap:
they cost the user an answer the system had, and they make the eval score
measure the instrument as much as the model. The first two runs are on
record together for exactly this reason: 10/18 became 15/18 with no
change to the model.

What would remove the class rather than its instances is comparing
structured values to structured values — having the model return the
figure as data beside the prose, and checking that, so rendering never
enters the comparison. That is a larger change than M4 has room for and
is the honest answer to "why not just add another pattern".

## Additional failure modes

- **Query shape varies between runs, and verification is sensitive to
it.** Measured over five runs of the golden set: outcomes were stable
18/18, but **nine of the twelve answerable questions produced a
different SQL statement almost every run, and four never repeated a
query once**. The model re-derives its plan each time and reaches the
same answer by different routes — selecting a descriptive column here, a
timestamp there.

This is a coupling, not nondeterminism, and the distinction matters
because the two have different fixes. The concrete case: `highest-risk`
went fail, pass, fail, pass across the first four eval runs, and that
history **is explained by query-shape variance meeting a
column-sensitive check, not by unstable reasoning.** It generated five
distinct queries in five runs; under the old grounding check, whether it
passed depended on whether `scored_at` happened to be among the columns
returned, because a timestamp the check could not admit was enough to
withhold a correct answer. The variance did not go away when the check
was fixed. Its consequence did.

**Forward-looking consequence, and it is a design constraint on
everything that consumes Q&A results: any check sensitive to which
columns come back will make stable answers appear unstable, and the
cause will look like the model.** M5's citation panel is the first
consumer — a panel that renders or validates against an expected column
set will flicker for questions whose answer never changed. Build against
the answer and its cited rows, not against the shape of the result set.

It also vindicates leaving `highest-risk`'s wording alone rather than
treating it as an ambiguous question: rewording would have suppressed
the symptom on one question while the property was present in nine of
twelve.

**The plan step is only as good as the description.** A question the
  warehouse *could* answer may be refused because the description does
  not make the path obvious. That direction is safe but invisible — a
  refusal rate against real questions is worth watching in the eval
  harness.
- **A grounded claim can still answer the wrong question.** Every figure
  can trace to the cited row while the SQL measured something other than
  what was asked. Nothing here checks that the query matches the
  question's intent; that is the same collectively-misleading gap named
  above, arriving one step earlier.
- **Truncation is reported, not prevented.** Past `MAX_ROWS` the answer
  layer is told the rows are incomplete and instructed not to total them,
  which is an instruction rather than a mechanism.
