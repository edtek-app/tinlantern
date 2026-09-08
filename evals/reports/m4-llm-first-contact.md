# M4 — LLM layer evaluation

> **Provenance.** Generated 2026-09-08T06:19:38+00:00 from commit `e5679d8ea3ab289b5a878c90fa8b5a78a61391af`, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

## Results

- **10 of 18 passed** (56%).
- Refusals: 6 of 6 correct. A question the warehouse cannot answer must be refused; answering it produces a real figure for a different question.
- Answers: 4 of 12 correct, where correct means the answer states the value a hand-written reference query computes — not merely that every claim cited a returned row.

Refusal and answer accuracy are reported separately on purpose. Combined, they hide the trade: a system that refuses everything scores perfectly on one and is useless.

## Failures

### `alerted-count` — How many learners are currently flagged as at risk?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
WITH latest_version AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_window AS (
    SELECT max(rs.window_close) AS window_close
    FROM warehouse.risk_score rs
    JOIN latest_version lv ON lv.model_version = rs.model_version
)
SELECT rs.model_version,
       rs.window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE rs.alerted) AS alerted_learners
FROM warehouse.risk_score rs
JOIN latest_version lv ON lv.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
GROUP BY rs.model_version, rs.window_close;
```

### `highest-risk` — What is the highest risk score in the cohort?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
WITH latest AS (
  SELECT model_version
  FROM warehouse.risk_score
  GROUP BY model_version
  ORDER BY max(scored_at) DESC
  LIMIT 1
),
latest_window AS (
  SELECT max(rs.window_close) AS window_close
  FROM warehouse.risk_score rs
  JOIN latest l ON l.model_version = rs.model_version
)
SELECT s.learner_identifier,
       rs.risk,
       rs.alerted,
       rs.model_version,
       rs.window_close,
       rs.scored_at
FROM warehouse.risk_score rs
JOIN latest l ON l.model_version = rs.model_version
JOIN latest_window w ON w.window_close = rs.window_close
JOIN warehouse.dim_student s ON s.student_key = rs.student_key
ORDER BY rs.risk DESC
LIMIT 5;
```

### `retry-count` — How many assessment attempts were retries rather than first attempts?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
SELECT count(*) AS total_attempts, count(*) FILTER (WHERE attempt_number > 1) AS retry_attempts, count(*) FILTER (WHERE attempt_number = 1) AS first_attempts FROM warehouse.fact_assessment;
```

### `mean-score` — What is the average scaled score across all graded work?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
SELECT avg(scaled_score) AS avg_scaled_score, count(*) AS graded_statements FROM warehouse.fact_assessment WHERE scaled_score IS NOT NULL;
```

### `learners-with-a-failure` — How many learners have failed at least one assessment?

Expected answered, got answered.
- the answer does not state the reference value (94). Every claim cited a returned row, so the query ran and the figures are real — they answer a different question than the one asked

Query run:
```sql
SELECT count(DISTINCT fa.student_key) AS learners_with_failed_assessment FROM warehouse.fact_assessment fa WHERE fa.verb = 'failed' OR fa.success IS FALSE;
```

### `activity-events` — How many learning events have been recorded in total?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
SELECT count(*) AS total_learning_events FROM warehouse.fact_activity;
```

### `risk-above-half` — How many learners have a risk score above 0.5?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

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
           rs.model_version,
           rs.window_close,
           rs.risk
    FROM warehouse.risk_score rs
    WHERE rs.model_version = (SELECT model_version FROM latest_model)
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS scored_learners
FROM latest_per_learner
GROUP BY model_version;
```

### `busiest-verb` — Which verb appears most often in the activity records?

Expected answered, got refused.
- expected an answer and got a refusal. Either the schema description does not make the path obvious, or: The answer could not be verified against the rows the query returned, so it is withheld rather than shown with a caveat.

Query run:
```sql
SELECT verb, count(*) AS statement_count FROM warehouse.fact_activity GROUP BY verb ORDER BY statement_count DESC, verb ASC LIMIT 10;
```

## Every question

| id | expected | observed | passed |
|---|---|---|---|
| `learner-count` | answered | answered | yes |
| `alerted-count` | answered | refused | **no** |
| `highest-risk` | answered | refused | **no** |
| `course-count` | answered | answered | yes |
| `retry-count` | answered | refused | **no** |
| `mean-score` | answered | refused | **no** |
| `learners-with-a-failure` | answered | answered | **no** |
| `active-days` | answered | answered | yes |
| `activity-events` | answered | refused | **no** |
| `risk-above-half` | answered | refused | **no** |
| `busiest-verb` | answered | refused | **no** |
| `largest-course` | answered | answered | yes |
| `final-grade` | refused | refused | yes |
| `login-frequency` | refused | refused | yes |
| `time-spent` | refused | refused | yes |
| `dropouts` | refused | refused | yes |
| `learner-emails` | refused | refused | yes |
| `attendance-correlation` | refused | refused | yes |
