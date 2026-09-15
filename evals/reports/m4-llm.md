# M4 — LLM layer evaluation

> **Provenance.** Generated 2026-09-09T18:42:38+00:00 from commit `600d80b9effea888ce5c9424e1e61c2e3c1ecb4b`, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

## Results

> **Read the score with this.** The questions, the reference queries
> that decide whether an answer is right, and the schema description the
> model plans against were **all written by the same author**. This set
> cannot detect a misconception shared between the person who wrote the
> questions and the person who wrote the context the model answers from.
>
> That is a structural limitation of the instrument, not a caveat about
> any one run. It does not weaken with a better score — a perfect run
> under this arrangement and a perfect run under an independent one are
> not the same evidence. What would strengthen it is an independently
> authored question set, or questions drawn from real advisor queries
> (ADR-0008, revisit triggers).

- **18 of 18 passed** (100%).
- Refusals: 6 of 6 correct. A question the warehouse cannot answer must be refused; answering it produces a real figure for a different question.
- Answers: 12 of 12 correct, where correct means the answer states the value a hand-written reference query computes — not merely that every claim cited a returned row.

- Malformed responses: **0**. A structured response that would not parse — neither a refusal nor a verification failure, and not retried, so this is a failure rate and not a retry rate.

Refusal and answer accuracy are reported separately on purpose. Combined, they hide the trade: a system that refuses everything scores perfectly on one and is useless.

## Failures

None.
## Every question

| id | expected | observed | passed |
|---|---|---|---|
| `learner-count` | answered | answered | yes |
| `alerted-count` | answered | answered | yes |
| `highest-risk` | answered | answered | yes |
| `course-count` | answered | answered | yes |
| `retry-count` | answered | answered | yes |
| `mean-score` | answered | answered | yes |
| `learners-with-a-failure` | answered | answered | yes |
| `active-days` | answered | answered | yes |
| `activity-events` | answered | answered | yes |
| `risk-above-half` | answered | answered | yes |
| `busiest-verb` | answered | answered | yes |
| `largest-course` | answered | answered | yes |
| `final-grade` | refused | refused | yes |
| `login-frequency` | refused | refused | yes |
| `time-spent` | refused | refused | yes |
| `dropouts` | refused | refused | yes |
| `learner-emails` | refused | refused | yes |
| `attendance-correlation` | refused | refused | yes |
