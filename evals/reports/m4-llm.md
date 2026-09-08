# M4 — LLM layer evaluation

> **Provenance.** Generated 2026-09-08T07:12:53+00:00 from commit `27738f296dd0e9db5df244fa0509c17f4b3f1c4a`, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

## Results

- **18 of 18 passed** (100%).
- Refusals: 6 of 6 correct. A question the warehouse cannot answer must be refused; answering it produces a real figure for a different question.
- Answers: 12 of 12 correct, where correct means the answer states the value a hand-written reference query computes — not merely that every claim cited a returned row.

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
