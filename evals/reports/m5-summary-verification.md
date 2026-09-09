# M5 — Advisor summary verification, first production-shaped run

> **Provenance.** Generated 2026-09-09T13:36:48+00:00 from
> commit `a9d43ff743bf42e6ed856f7bf7676f79f5eee36e`, provider `anthropic`,
> model `claude-opus-5`, over 120 scored learners on the cohort loaded at
> the time (192,431 statements, model_version `34ffde7`).
>
> **Frozen. Never regenerated.** This is the first measurement of the
> summary path against a real model, taken before anything was changed
> in response to it. A later run will be better; this is what it was.

## Read the numbers with this

The questions, the fact set the model answers from, and the checks that
judge its answers were **all written by the same author** — the same
structural limitation the M4 eval reports carry in their opening
framing. It does not weaken as the rate improves.

## Headline: the 21% splits three ways, and only one part is ours

Production run: **25 of 120 summaries fell back** (20.8%), every one
`verification-failed`. That single rate is the least useful way to state
it. Split by what verification actually objected to:

| share | cause | whose |
|---|---|---|
| **~6%** | The model asserted the drivers **sum to** or **add up to** the score | **The model's.** The claim is false and the check is right |
| **~2.5%** | A claim cited `caveat` / `driver_caveat` — text the model was SHOWN but given no key for | **Ours** |
| **~1.7%** | A figure appeared in `suggested_next_step` | Neither — the rule is deliberate |

## ZERO ungrounded numbers

**Not one figure in any summary failed to trace to a supplied fact.**
Dates in three renderings, percentages of supplied proportions, driver
contributions — all passed. The numeric check, the component with four
recorded defects behind it and the first thing suspected here, was
never the cause.

This result is worth freezing precisely because it will become
invisible: once the fallback rate drops for other reasons, nobody will
be able to see that the numeric layer was already clean.

## M4's prior did not hold

Across M4, **eleven of eleven** verification failures turned out to be
defects in the checking layer, four of them the numeric check being
wrong about how a model renders a value it was correctly given. That
made "harness defect" the right first hypothesis here, and it was
tested first — including the specific suggestion that `LearnerFacts`
carrying `window_close` as a DATE while the Q&A path carries full
timestamps would be a fifth instance of the rendered-versus-structured
class.

**It was refuted.** All three date renderings pass, because
`allowed_numbers()` already contributes year, month and day and the ISO
form is scrubbed. The decision not to apply M4's timestamp fix to the
summariser was correct and remains correct.

Stated plainly because a prior quoted when it holds and dropped when it
does not is not a prior: **most of this failure rate is the model, not
the harness.**

## Run-to-run variance is large and neither run measures it

The same 25 learners were re-run against the same facts:

- Production: **25 of 120 failed**
- Reproduction of those 25: **11 of 25 failed** — 14 passed

Same learner, same fact set, different outcome. **Do not read 20.8% as a
stable rate**, and do not read the reproduction as a correction of it.
Neither run alone measures the variance, and this artifact deliberately
reports both rather than smoothing them into one number. ADR-0008
records the equivalent finding for Q&A as a coupling between query shape
and verification; the summary path has its own version and it is
unmeasured.

## Categorised objections, reproduction run

11 of 25 learners failed, producing 12 objections.

| count | objection |
|---|---|
| 5 | `decomposition:sum to` |
| 2 | `advice-figure` |
| 2 | `decomposition:add up` |
| 2 | `citation:driver_caveat` |
| 1 | `citation:caveat` |

### One of each, verbatim

**advice-figure** — `s-00001`

> the suggested next step contains a figure. It is advice, not a claim, so it carries no citation and nothing can check it — figures belong in claims

**citation** — `s-00081`

> claim 7 cites 'caveat', which was never supplied. Supplied facts: ['alerted', 'cohort', 'driver:failures', 'driver:mean_score_in_window', 'driver:sessions', 'risk', 'threshold', 'window_close']

**decomposition** — `s-00010`

> the rendered summary says 'add up', presenting the drivers as a decomposition of the score. They are counterfactual, they interact, and they do not sum.

## What this establishes, and what it does not

**Establishes:** the numeric grounding layer is clean on this path; the
additivity constraint catches real violations at roughly 7/25 of
attempts; the fallback does its job — every one of these learners still
got a grounded summary from the template.

**Does not establish:** a stable failure rate, anything about a
different cohort, or anything about a model other than the one named
above. And the additivity rate is the number any future prompt tuning
must be measured against — **not against an unmeasured impression that
it got better.**

## The diagnostic gap that made this expensive

`warehouse.llm_call.detail` stored the string `"verification-failed"` —
a duplicate of the `outcome` column beside it — while
`AdvisorSummary.problems`, which says which claim broke and why, was
computed, attached to the result, and dropped at the moment of writing
the row. Recovering it cost 25 live API calls to re-derive information
the code had already produced.

Second occurrence of that shape; the eval report's "could not be
verified" was the first, at seven calls.
