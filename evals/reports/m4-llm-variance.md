# M4 — LLM layer run-to-run variance

> **Provenance.** Generated 2026-09-08T08:23:50+00:00 from commit `69b27dc4b36bfceb4fe02f9d59d19b8c4cd32f67`  **working tree dirty**, against provider `anthropic` model `claude-opus-5`, over 18 golden questions.
>
> Regenerate with `make evals`. A report generated against the `stub` provider is refused rather than written: canned responses measure this harness, not the model.

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

> **Why the first four runs are not in this measurement.** They are not
> one regime with noise in it; they are four different instruments. The
> grounding check changed twice across them and three reference queries
> were corrected, so the same question was graded by a different oracle
> in run 1 than in run 4. Pooling them would measure this harness's
> development history rather than the model's variability. Measurement
> starts from the first run under the current code.

## Hypothesis, recorded before measuring

The variance is in query SHAPE — which columns the model selects — and not in the answer. The figure a question asks for is stable; what moves is how much else comes back beside it, and that reaches verification only because the grounding check is sensitive to the columns a row contains.

A question raised during planning — whether `highest-risk` is underspecified, since "the highest risk score" might admit either a single scalar or a top-N reading — was checked and **rejected on the question's wording**: it asks for a score, and the score is unambiguous. `LIMIT 1` versus top-N is the model choosing how much context to return alongside an unambiguous scalar, which is the coupling itself rather than an ambiguity in the question. Recorded so a reader knows it was considered.

## Result

- **18/18 every run** across 5 runs.
- Outcome stable (same verdict every run): **18 of 18**.
- SQL stable (identical query every run): **9 of 18**.
- Malformed responses: **0** across 90 question-runs. Its own outcome category — a structured response that would not parse is neither a refusal nor a verification failure. Not retried, so this is a failure rate. A zero here is meaningful rather than reassuring: the fault is known to exist, having aborted an earlier attempt at this very measurement.

SQL stability is reported for every question, including ones that never fail. A question passing five times from five different queries is passing by luck; only this distinguishes it from one whose query is settled, and a pass rate alone would show both as a row of ticks.

## Per question

| id | passed | outcome stable | distinct queries |
|---|---|---|---|
| `learner-count` | 5/5 | yes | 1 |
| `alerted-count` | 5/5 | yes | 5 |
| `highest-risk` | 5/5 | yes | 5 |
| `course-count` | 5/5 | yes | 1 |
| `retry-count` | 5/5 | yes | 2 |
| `mean-score` | 5/5 | yes | 2 |
| `learners-with-a-failure` | 5/5 | yes | 1 |
| `active-days` | 5/5 | yes | 4 |
| `activity-events` | 5/5 | yes | 2 |
| `risk-above-half` | 5/5 | yes | 5 |
| `busiest-verb` | 5/5 | yes | 4 |
| `largest-course` | 5/5 | yes | 5 |
| `final-grade` | 5/5 | yes | 0 |
| `login-frequency` | 5/5 | yes | 0 |
| `time-spent` | 5/5 | yes | 0 |
| `dropouts` | 5/5 | yes | 0 |
| `learner-emails` | 5/5 | yes | 0 |
| `attendance-correlation` | 5/5 | yes | 0 |

## Questions whose query changed between runs

### `alerted-count` — How many learners are currently flagged as at risk?

Passed 5 of 5 runs, from 5 distinct queries.

Query 1:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
latest_window AS (
    SELECT rs.model_version, max(rs.window_close) AS window_close
    FROM warehouse.risk_score rs
    JOIN latest_model lm ON lm.model_version = rs.model_version
    GROUP BY rs.model_version
)
SELECT rs.model_version,
       lw.window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE rs.alerted) AS alerted_learners
FROM warehouse.risk_score rs
JOIN latest_window lw
  ON lw.model_version = rs.model_version
 AND lw.window_close = rs.window_close
GROUP BY rs.model_version, lw.window_close;
```

Query 2:
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
SELECT
  lm.model_version,
  lw.window_close,
  count(*) AS scored_learners,
  count(*) FILTER (WHERE rs.alerted) AS alerted_learners
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
GROUP BY lm.model_version, lw.window_close;
```

Query 3:
```sql
WITH latest AS (
    SELECT model_version, window_close
    FROM warehouse.risk_score
    ORDER BY scored_at DESC, window_close DESC
    LIMIT 1
)
SELECT l.model_version,
       l.window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE rs.alerted) AS alerted_learners
FROM warehouse.risk_score rs
JOIN latest l
  ON rs.model_version = l.model_version
 AND rs.window_close = l.window_close
GROUP BY l.model_version, l.window_close;
```

Query 4:
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
SELECT rs.model_version,
       rs.window_close,
       count(*) FILTER (WHERE rs.alerted) AS alerted_learners,
       count(*) AS scored_learners
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
GROUP BY rs.model_version, rs.window_close;
```

Query 5:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
), latest_window AS (
    SELECT max(r.window_close) AS window_close
    FROM warehouse.risk_score r
    JOIN latest_model m ON m.model_version = r.model_version
)
SELECT r.model_version,
       w.window_close,
       count(DISTINCT r.student_key) AS scored_learners,
       count(DISTINCT r.student_key) FILTER (WHERE r.alerted) AS alerted_learners
FROM warehouse.risk_score r
JOIN latest_model m ON m.model_version = r.model_version
JOIN latest_window w ON w.window_close = r.window_close
GROUP BY r.model_version, w.window_close;
```

### `highest-risk` — What is the highest risk score in the cohort?

Passed 5 of 5 runs, from 5 distinct queries.

Query 1:
```sql
WITH latest_version AS (
    SELECT model_version
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY max(scored_at) DESC
    LIMIT 1
),
scored AS (
    SELECT rs.student_key,
           rs.risk,
           rs.alerted,
           rs.window_close,
           rs.model_version,
           rank() OVER (ORDER BY rs.risk DESC) AS risk_rank
    FROM warehouse.risk_score rs
    JOIN latest_version lv ON lv.model_version = rs.model_version
)
SELECT ds.learner_identifier,
       s.risk AS highest_risk,
       s.alerted,
       s.window_close,
       s.model_version
FROM scored s
JOIN warehouse.dim_student ds ON ds.student_key = s.student_key
WHERE s.risk_rank = 1
ORDER BY ds.learner_identifier;
```

Query 2:
```sql
WITH latest_model AS (
  SELECT model_version
  FROM warehouse.risk_score
  GROUP BY model_version
  ORDER BY max(scored_at) DESC
  LIMIT 1
),
latest_rows AS (
  SELECT rs.student_key, rs.risk, rs.alerted, rs.window_close, rs.model_version,
         row_number() OVER (PARTITION BY rs.student_key ORDER BY rs.window_close DESC, rs.scored_at DESC) AS rn
  FROM warehouse.risk_score rs
  JOIN latest_model lm ON lm.model_version = rs.model_version
),
current_scores AS (
  SELECT * FROM latest_rows WHERE rn = 1
)
SELECT s.learner_identifier,
       c.risk AS risk,
       c.alerted AS alerted,
       c.window_close AS window_close,
       c.model_version AS model_version
FROM current_scores c
JOIN warehouse.dim_student s ON s.student_key = c.student_key
WHERE c.risk = (SELECT max(risk) FROM current_scores)
ORDER BY s.learner_identifier;
```

Query 3:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    ORDER BY scored_at DESC
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
       rs.model_version,
       rs.window_close,
       rs.scored_at
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
JOIN warehouse.dim_student ds ON ds.student_key = rs.student_key
ORDER BY rs.risk DESC
LIMIT 1;
```

Query 4:
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
       rs.model_version,
       rs.window_close,
       rs.scored_at,
       rs.drivers
FROM warehouse.risk_score rs
JOIN latest_model lm ON lm.model_version = rs.model_version
JOIN latest_window lw ON lw.window_close = rs.window_close
JOIN warehouse.dim_student ds ON ds.student_key = rs.student_key
ORDER BY rs.risk DESC
LIMIT 1;
```

Query 5:
```sql
WITH latest_model AS (
    SELECT model_version
    FROM warehouse.risk_score
    ORDER BY scored_at DESC
    LIMIT 1
),
latest_window AS (
    SELECT max(r.window_close) AS window_close
    FROM warehouse.risk_score r
    JOIN latest_model m ON m.model_version = r.model_version
)
SELECT s.learner_identifier,
       r.risk AS risk,
       r.alerted AS alerted,
       r.model_version AS model_version,
       r.window_close AS window_close
FROM warehouse.risk_score r
JOIN latest_model m ON m.model_version = r.model_version
JOIN latest_window w ON w.window_close = r.window_close
JOIN warehouse.dim_student s ON s.student_key = r.student_key
ORDER BY r.risk DESC
LIMIT 1;
```

### `retry-count` — How many assessment attempts were retries rather than first attempts?

Passed 5 of 5 runs, from 2 distinct queries.

Query 1:
```sql
SELECT count(*) AS total_attempts, count(*) FILTER (WHERE attempt_number > 1) AS retry_attempts, count(*) FILTER (WHERE attempt_number = 1) AS first_attempts FROM warehouse.fact_assessment;
```

Query 2:
```sql
SELECT count(*) FILTER (WHERE attempt_number > 1) AS retry_attempts, count(*) FILTER (WHERE attempt_number = 1) AS first_attempts, count(*) AS total_graded_statements FROM warehouse.fact_assessment;
```

### `mean-score` — What is the average scaled score across all graded work?

Passed 5 of 5 runs, from 2 distinct queries.

Query 1:
```sql
SELECT avg(scaled_score) AS avg_scaled_score, count(*) AS scored_statements FROM warehouse.fact_assessment WHERE scaled_score IS NOT NULL;
```

Query 2:
```sql
SELECT avg(fa.scaled_score) AS avg_scaled_score, count(*) AS scored_statements FROM warehouse.fact_assessment fa WHERE fa.scaled_score IS NOT NULL;
```

### `active-days` — On how many distinct days was there any recorded activity?

Passed 5 of 5 runs, from 4 distinct queries.

Query 1:
```sql
SELECT count(DISTINCT date_key) AS active_days FROM warehouse.fact_activity;
```

Query 2:
```sql
SELECT count(DISTINCT date_key) AS distinct_active_days FROM warehouse.fact_activity;
```

Query 3:
```sql
SELECT count(DISTINCT fa.date_key) AS distinct_active_days FROM warehouse.fact_activity fa;
```

Query 4:
```sql
SELECT count(DISTINCT fa.date_key) AS active_days FROM warehouse.fact_activity fa;
```

### `activity-events` — How many learning events have been recorded in total?

Passed 5 of 5 runs, from 2 distinct queries.

Query 1:
```sql
SELECT count(*) AS total_events FROM warehouse.fact_activity;
```

Query 2:
```sql
SELECT count(*) AS total_learning_events FROM warehouse.fact_activity;
```

### `risk-above-half` — How many learners have a risk score above 0.5?

Passed 5 of 5 runs, from 5 distinct queries.

Query 1:
```sql
WITH latest_version AS (
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
    JOIN latest_version lv ON lv.model_version = rs.model_version
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner
GROUP BY model_version;
```

Query 2:
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
           rs.model_version,
           rs.window_close,
           rs.risk
    FROM warehouse.risk_score rs
    JOIN latest_model lm ON lm.model_version = rs.model_version
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner
GROUP BY model_version;
```

Query 3:
```sql
WITH latest_version AS (
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
    JOIN latest_version lv ON lv.model_version = rs.model_version
    ORDER BY rs.student_key, rs.window_close DESC, rs.scored_at DESC
)
SELECT model_version AS model_version,
       max(window_close) AS latest_window_close,
       count(*) AS scored_learners,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5
FROM latest_per_learner
GROUP BY model_version;
```

Query 4:
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
SELECT model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner
GROUP BY model_version;
```

Query 5:
```sql
WITH latest_model AS (
    SELECT model_version, max(scored_at) AS last_scored_at
    FROM warehouse.risk_score
    GROUP BY model_version
    ORDER BY last_scored_at DESC
    LIMIT 1
),
latest_per_learner AS (
    SELECT DISTINCT ON (r.student_key)
           r.student_key,
           r.risk,
           r.window_close
    FROM warehouse.risk_score r
    JOIN latest_model m ON r.model_version = m.model_version
    ORDER BY r.student_key, r.window_close DESC, r.scored_at DESC
)
SELECT (SELECT model_version FROM latest_model) AS model_version,
       max(window_close) AS latest_window_close,
       count(*) FILTER (WHERE risk > 0.5) AS learners_risk_above_0_5,
       count(*) AS learners_scored
FROM latest_per_learner;
```

### `busiest-verb` — Which verb appears most often in the activity records?

Passed 5 of 5 runs, from 4 distinct queries.

Query 1:
```sql
SELECT verb, count(*) AS event_count FROM warehouse.fact_activity GROUP BY verb ORDER BY event_count DESC, verb ASC LIMIT 10;
```

Query 2:
```sql
SELECT verb, count(*) AS statement_count FROM warehouse.fact_activity GROUP BY verb ORDER BY statement_count DESC, verb LIMIT 10;
```

Query 3:
```sql
SELECT verb, count(*) AS statement_count
FROM warehouse.fact_activity
GROUP BY verb
ORDER BY statement_count DESC, verb
LIMIT 5;
```

Query 4:
```sql
SELECT verb, count(*) AS event_count FROM warehouse.fact_activity GROUP BY verb ORDER BY event_count DESC, verb LIMIT 20;
```

### `largest-course` — How many activities does the largest course contain?

Passed 5 of 5 runs, from 5 distinct queries.

Query 1:
```sql
SELECT c.course_key, c.course_slug, c.title, count(*) AS activity_count FROM warehouse.dim_activity a JOIN warehouse.dim_course c ON c.course_key = a.course_key GROUP BY c.course_key, c.course_slug, c.title ORDER BY activity_count DESC, c.course_slug LIMIT 1;
```

Query 2:
```sql
WITH activity_counts AS (
  SELECT
    c.course_key,
    c.course_slug,
    c.title,
    count(*) AS activity_count
  FROM warehouse.dim_activity a
  JOIN warehouse.dim_course c ON c.course_key = a.course_key
  GROUP BY c.course_key, c.course_slug, c.title
)
SELECT
  course_key,
  course_slug,
  title,
  activity_count
FROM activity_counts
WHERE activity_count = (SELECT max(activity_count) FROM activity_counts)
ORDER BY course_slug;
```

Query 3:
```sql
SELECT c.course_key, c.course_slug, c.title, count(*) AS activity_count FROM warehouse.dim_activity a JOIN warehouse.dim_course c ON c.course_key = a.course_key GROUP BY c.course_key, c.course_slug, c.title ORDER BY activity_count DESC, c.course_key LIMIT 1;
```

Query 4:
```sql
WITH counts AS (
  SELECT c.course_key,
         c.course_slug,
         c.title,
         count(*) AS activity_count
  FROM warehouse.dim_activity a
  JOIN warehouse.dim_course c ON c.course_key = a.course_key
  GROUP BY c.course_key, c.course_slug, c.title
),
ranked AS (
  SELECT course_slug,
         title,
         activity_count,
         rank() OVER (ORDER BY activity_count DESC) AS size_rank
  FROM counts
)
SELECT course_slug, title, activity_count
FROM ranked
WHERE size_rank = 1
ORDER BY course_slug;
```

Query 5:
```sql
WITH counts AS (
  SELECT c.course_key,
         c.course_slug,
         c.title,
         count(*) AS activity_count
  FROM warehouse.dim_activity a
  JOIN warehouse.dim_course c ON c.course_key = a.course_key
  GROUP BY c.course_key, c.course_slug, c.title
)
SELECT course_key,
       course_slug,
       title,
       activity_count
FROM counts
WHERE activity_count = (SELECT max(activity_count) FROM counts)
ORDER BY course_slug;
```
