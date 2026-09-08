# M4 — LLM layer evaluation

> **Provenance.** Generated 2026-09-08T07:00:08+00:00 from commit `42d2aa9ee8a7ab1b2fbfb2f991cde8f2f2868ed2`, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

## Results

- **16 of 18 passed** (89%).
- Refusals: 6 of 6 correct. A question the warehouse cannot answer must be refused; answering it produces a real figure for a different question.
- Answers: 10 of 12 correct, where correct means the answer states the value a hand-written reference query computes — not merely that every claim cited a returned row.

Refusal and answer accuracy are reported separately on purpose. Combined, they hide the trade: a system that refuses everything scores perfectly on one and is useless.

## Failures

### `alerted-count` — How many learners are currently flagged as at risk?

Expected answered, got answered.
- the answer does not state the reference value (76). Every claim cited a returned row, so the query ran and the figures are real — they answer a different question than the one asked

Query run:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_per_learner AS (
    SELECT DISTINCT ON (r.student_key)
           r.student_key,
           r.model_version,
           r.window_close,
           r.alerted
    FROM warehouse.risk_score r
    JOIN latest_model m ON m.model_version = r.model_version
    ORDER BY r.student_key, r.window_close DESC, r.scored_at DESC
)
SELECT model_version,
       max(window_close) AS latest_window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE alerted) AS alerted_learners
FROM latest_per_learner
GROUP BY model_version;
```

### `risk-above-half` — How many learners have a risk score above 0.5?

Expected answered, got answered.
- the answer does not state the reference value (74). Every claim cited a returned row, so the query ran and the figures are real — they answer a different question than the one asked

Query run:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_per_learner AS (
    SELECT DISTINCT ON (rs.student_key)
           rs.student_key,
           rs.risk,
           rs.window_close,
           rs.model_version
    FROM warehouse.risk_score rs
    JOIN latest_model lm ON lm.model_version = rs.model_version
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT model_version AS model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner
GROUP BY model_version;
```

## Every question

| id | expected | observed | passed |
|---|---|---|---|
| `learner-count` | answered | answered | yes |
| `alerted-count` | answered | answered | **no** |
| `highest-risk` | answered | answered | yes |
| `course-count` | answered | answered | yes |
| `retry-count` | answered | answered | yes |
| `mean-score` | answered | answered | yes |
| `learners-with-a-failure` | answered | answered | yes |
| `active-days` | answered | answered | yes |
| `activity-events` | answered | answered | yes |
| `risk-above-half` | answered | answered | **no** |
| `busiest-verb` | answered | answered | yes |
| `largest-course` | answered | answered | yes |
| `final-grade` | refused | refused | yes |
| `login-frequency` | refused | refused | yes |
| `time-spent` | refused | refused | yes |
| `dropouts` | refused | refused | yes |
| `learner-emails` | refused | refused | yes |
| `attendance-correlation` | refused | refused | yes |
