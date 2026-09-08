# M4 — LLM layer evaluation

> **Provenance.** Generated 2026-09-08T06:36:55+00:00 from commit `0b420b7f595f6c4630bb9dc3329aa9f116f5e813`, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

## Results

- **15 of 18 passed** (83%).
- Refusals: 6 of 6 correct. A question the warehouse cannot answer must be refused; answering it produces a real figure for a different question.
- Answers: 9 of 12 correct, where correct means the answer states the value a hand-written reference query computes — not merely that every claim cited a returned row.

Refusal and answer accuracy are reported separately on purpose. Combined, they hide the trade: a system that refuses everything scores perfectly on one and is useless.

## Failures

### `alerted-count` — How many learners are currently flagged as at risk?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Verification objected to:
- claim 2 contains 5, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 2 contains 0, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row

Query run:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_window AS (
    SELECT max(rs.window_close) AS window_close
    FROM warehouse.risk_score rs
    JOIN latest_model lm ON lm.model_version = rs.model_version
)
SELECT lm.model_version,
       lw.window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE rs.alerted) AS alerted_learners
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
GROUP BY lm.model_version, lw.window_close;
```

### `highest-risk` — What is the highest risk score in the cohort?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Verification objected to:
- claim 1 contains 16, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 1 contains 36, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 1 contains 5, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 1 contains 5, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 1 contains 0, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row

Query run:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_window AS (
    SELECT max(rs.window_close) AS window_close
    FROM warehouse.risk_score rs
    JOIN latest_model lm ON lm.model_version = rs.model_version
)
SELECT ds.learner_identifier,
       rs.risk AS risk_score,
       rs.alerted,
       rs.window_close,
       rs.model_version,
       rs.scored_at
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
JOIN warehouse.dim_student ds ON ds.student_key = rs.student_key
ORDER BY rs.risk DESC
LIMIT 1;
```

### `risk-above-half` — How many learners have a risk score above 0.5?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Verification objected to:
- claim 2 contains 5, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row
- claim 2 contains 0, which is not in the row it cites (row:0) — either the model computed something the query did not return, or it cited the wrong row

Query run:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    ORDER BY scored_at DESC
    LIMIT 1
),
latest_per_learner AS (
    SELECT DISTINCT ON (rs.student_key)
           rs.student_key,
           rs.risk,
           rs.window_close,
           rs.model_version
    FROM warehouse.risk_score rs
    WHERE rs.model_version = (SELECT model_version FROM latest_model)
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT (SELECT model_version FROM latest_model) AS model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner;
```

## Every question

| id | expected | observed | passed |
|---|---|---|---|
| `learner-count` | answered | answered | yes |
| `alerted-count` | answered | refused | **no** |
| `highest-risk` | answered | refused | **no** |
| `course-count` | answered | answered | yes |
| `retry-count` | answered | answered | yes |
| `mean-score` | answered | answered | yes |
| `learners-with-a-failure` | answered | answered | yes |
| `active-days` | answered | answered | yes |
| `activity-events` | answered | answered | yes |
| `risk-above-half` | answered | refused | **no** |
| `busiest-verb` | answered | answered | yes |
| `largest-course` | answered | answered | yes |
| `final-grade` | refused | refused | yes |
| `login-frequency` | refused | refused | yes |
| `time-spent` | refused | refused | yes |
| `dropouts` | refused | refused | yes |
| `learner-emails` | refused | refused | yes |
| `attendance-correlation` | refused | refused | yes |
