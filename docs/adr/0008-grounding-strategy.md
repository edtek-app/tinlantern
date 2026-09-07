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
- **Revisit triggers:** the drivers payload starts carrying its fitted
  median, which unlocks the cohort comparison. **Ruled in advance, so it
  is not relitigated:** that change must persist BOTH the learner's
  value and the fitted median used at ablation time, keyed to the same
  `model_version`. Recomputing either later reintroduces exactly the
  mismatch this ADR avoids by omitting the comparison. It is its own
  task, raised when M5 shows whether advisors need that sentence; fallback rate against the
  real provider is high enough to suggest the schema or the prompt is
  fighting the model rather than constraining it; M5 finds that advisors
  want the argument, not the claims, which would put the
  collectively-misleading gap on the critical path.
