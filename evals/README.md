# evals — LLM eval harness + versioned reports (M3/M4): golden questions, groundedness, refusal checks.


## The oracle has been wrong three times out of twelve

The golden set grades an answer against a hand-written `reference_sql`.
That query is the oracle, and it has been wrong on three of the twelve
answerable questions — every time producing a confident failure
attributed to the model:

| question | the reference did | the model did |
|---|---|---|
| `learners-with-a-failure` | counted `verb = 'failed'` only | also counted submissions marked unsuccessful |
| `alerted-count` | counted rows: **76** for 120 learners | scoped to the latest model version: **38** |
| `risk-above-half` | counted rows: **74** | scoped: **37** |

**A wrong reference is worse than no reference.** No reference grades on
disposition alone and misses wrong answers; a wrong one manufactures
failures and points them at the wrong component. Across three
real-provider runs, eleven distinct failures, **none was the model's**.

Two mechanisms now guard the oracle, and both are tested:

- **Every answerable question declares a `bound`** — the population its
  value cannot exceed — and a test asserts it against a cohort the tests
  build. A count above its population would have caught the risk_score
  double count on the day it was written, without a model and without a
  provider.
- **Anything counting learners in `risk_score` must scope to one
  `model_version`.** That table accumulates a row per learner per
  scoring run (ADR-0007). A test adds a second scoring run and asserts
  the references still count learners.

**The bound is one word, not a query, on purpose.** Letting each
question supply its own bound SQL is the obvious-looking improvement and
it reproduces the hazard one level up: a hand-written guard against
hand-written queries, unchecked by anything. Six bound queries live in
`evals/harness/golden.py`, each counting a whole table, and a question
picks one by name. If a question needs a bound that does not exist, add
it there — where it is shared, reviewed once, and covered by the same
tests — rather than inline.

If you add a question: assume your reference is wrong until a test says
otherwise.

## Reports on record

`make evals` writes `reports/m4-llm.md` — the current run, regenerated at
milestone close.

`reports/m4-llm-first-contact.md` is **frozen and never regenerated**. It
is the first time the golden set met a real model, byte-for-byte as
measured: 10 of 18, refusals 6/6, answers 4/12.

It is kept because the run that followed it is much better, and the
improvement came from fixing *this repository's* grounding check, not the
model. Six of those eight failures were defects in our own numeric check
— thousands separators read as two numbers, figures the question itself
supplied treated as fabrications, digits inside a column name read as an
assertion — and one was a reference query encoding a narrower reading of
an ambiguous question than the model chose. None was a model failure; the
model wrote correct SQL for all twelve answerable questions.

Nobody should have to take a later number on trust. Both runs carry their
own provenance header, and the difference between them is the honest
record of what was wrong and where.
