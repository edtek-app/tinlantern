# ADR-0007: Risk model selection and the alert threshold
Status: Accepted

## Context
M3 requires a baseline model, a stronger one, a comparison, and a chosen
alert threshold. Three things had to be decided: how the winner is picked,
which model wins, and where the threshold sits.

The requirements a model must satisfy are stated here **before** any
result, so the argument's order matches the decision's order.

## The requirements, fixed before results

**Per-archetype recall leads.** An early-alert system that misses an
entire archetype has failed at its purpose whatever its aggregate says.
Every recall carries its `n`; an archetype with fewer than five at-risk
learners is unreportable, because one learner moves such a figure by tens
of points.

**Aggregate AUC is secondary and never quoted alone.** In this cohort
`struggling` learners are both highly engaged and all at risk, so
engagement volume points the wrong way and an aggregate figure flatters
any model that catches them on scores alone. The caveat travels with the
number.

**Score usability is a requirement, not a preference.** M5 specifies a
cohort overview with a risk *distribution* and a per-student drill-down
score. A model that pins nearly every learner to 0 or 1 can produce
neither. Mechanically: at least **20%** of learners must score strictly
between 0.05 and 0.95. A candidate below that floor fails a stated
downstream criterion and leaves the running.

**Interpretability breaks ties, and only ties.** M3 asks for top
contributing features per student. Neither candidate produces those
directly — a linear model's coefficients are *global* weights, and
coefficient × value is a derivation needed either way — but a linear model
makes it far cheaper. This decides only when no model dominates.

## The decision rule, and the correction it needed

Requirements are checked first; disqualified candidates leave. Among
survivors, a model at least as good on **both** mean per-archetype recall
and precision, and strictly better on one, **dominates and wins**. Only a
genuine trade-off — one better on recall, the other on precision — is a
tie, and only then does interpretability decide.

**The first version of this rule was wrong, and the choice reversed when
it was fixed.** v1 compared mean per-archetype recall alone and treated
anything inside a ten-point band as a tie, then applied the
interpretability tiebreak. It selected logistic regression — while
gradient boosting was missing zero at-risk learners against logistic
regression's two and raising five fewer false alarms, precision 0.949
against 0.833. A tiebreak that fires while one model dominates is not a
tiebreak; it is a route to a predetermined answer. This is recorded
because a rule that had to be corrected mid-comparison is more informative
than one that appeared to work.

## Decision

**Selected: gradient boosting with Platt-scaled probabilities**, fitted
inside each fold.

| candidate | mean per-archetype recall | precision | interior share | outcome |
|---|---|---|---|---|
| logistic regression | 97.6% | 0.833 | 38.3% | eligible, dominated |
| gradient boosting (raw) | 100.0% | 0.949 | **2.5%** | **disqualified** |
| gradient boosting (calibrated) | 97.6% | 0.923 | 63.3% | **selected** |

Raw gradient boosting scored best on every metric and still lost — it
failed a stated requirement, which is what requirements are for.
Calibration cost some discrimination (disengaging recall 95.2% against
100%) and bought a usable distribution. That trade is reported, not
hidden.

**A pre-registered prediction, partially wrong.** Before running the
comparison it was predicted that gradient boosting would not meaningfully
beat logistic regression, the problem being near-saturated at n=120.
Correct on the leading metric — 2.4% apart, inside noise. Wrong overall:
it was strictly better on both axes, and the 11.6-point precision gap was
exactly what v1 of the decision rule could not see.

**Evaluation protocol:** stratified 5-fold, seed 20260301, out-of-fold
predictions, cohort baselines and calibration both fitted inside each
fold. At 120 learners a single holdout would leave per-archetype recall
resting on about six learners. *Limitation:* repeated model selection
against a cross-validation estimate can overfit it; three candidates
compared once makes that negligible, and extensive iteration would call
for a genuine untouched holdout.

## The alert threshold

**Operational reasoning first.** A missed at-risk learner is a student who
needed help and did not get it. An unnecessary alert is twenty minutes of
an advisor's time. These costs are not close, so the threshold sits below
0.5, deliberately accepting false alarms to avoid misses.

**What that costs, and a finding.** The threshold sweep is in the
evaluation report. On this cohort it is **flat**: between 0.20 and 0.60
the selected model's decisions do not change at all. The threshold is
therefore **inert over the entire operational range**, and this cohort
provides no evidence either for or against the specific number.

`ALERT_THRESHOLD = 0.35` is set by the reasoning above, **not** by any
maximum on a curve — picking the peak of a metric would fit the decision
to this cohort. It is honest to say the value is currently untested by
evidence, and that real data with a meaningful trade-off curve is what
would test it.

## Per-learner drivers

M3 requires top contributing features per student. **Neither candidate
produces them directly**, and the selected model rules out the cheap
route: it wraps a tree ensemble in Platt scaling, so there are no
coefficients, and a tree explainer would attribute the *pre-calibration*
estimator's output rather than the score an advisor sees.

**Chosen: ablation to the cohort median.** Each feature is replaced with
the typical value and the model re-scored; the drop is that feature's
contribution. Three reasons, in order:

1. **It explains the displayed number.** Model-agnostic, so it runs
   through the calibration wrapper. An attribution that explains a
   different number than the one on screen is worse than none.
2. **No new dependency**, and ~14 extra predictions per learner.
3. It reads naturally to a non-technical advisor: *"if this learner's
   failure count were typical, their risk would fall 0.31."*

**The cost, carried on the payload rather than buried here.**
Contributions do not sum to the score and they interact — on this cohort,
ablating `mean_score_in_window` and `failures` together differs from the
sum of ablating each by 0.085. The drivers payload therefore carries
`additive: false` and an explicit caveat, so a dashboard cannot render
them as a decomposition. SHAP would offer additivity, but of the wrong
quantity.

The median profile is a **fitted parameter**, like `CohortBaseline`:
fitted on training rows and carried, never recomputed from the rows being
explained.

## Model persistence

**Retrain every run.** At 120 learners it costs under a second, and it
removes a class of confusion about which model produced a given score — a
run is reproducible from the seed and the code alone. `model_version`
records the git commit plus a dirty marker, derived rather than declared
so it cannot decay into a constant nobody updates.

**The tradeoff, which becomes a real constraint at M6:** there is no way
to score against a previous model without checking out the code that
produced it. Reproducing last month's alerts means reproducing last
month's checkout. That is acceptable while scoring is a local job; when it
moves to a deployed runtime and retraining per invocation stops being
free, persisting a model artifact is the answer, and the version recorded
here becomes the key that identifies it.

## Resolving "the current model version"

`warehouse.risk_score` keeps one row per learner **per scoring run**, so
every consumer has to pick a version before it can count learners rather
than rows. The obvious implementation is wrong and should not be
re-derived:

```sql
ORDER BY max(scored_at) DESC LIMIT 1   -- WRONG
```

`scored_at` defaults to `now()`, which in PostgreSQL is **transaction
time**. Two scoring runs inside one transaction — or two within the same
clock tick — carry an identical timestamp, and the tie-break is then
undefined: a consumer can silently serve an older model's scores while
appearing current. Observed, not hypothesised; a dashboard query written
this way selected the older run and a test caught it.

**The newest version is resolved by `scored_at` and then by insertion
order:**

```sql
ORDER BY max(scored_at) DESC, max(risk_score_key) DESC LIMIT 1
```

`risk_score_key` is a `BIGSERIAL`, so it strictly increases with
insertion. It plays exactly the role `ingest_seq` plays in `raw`
(ADR-0005): an opaque arrival marker, never read as event time. It is
the tie-break, not the ordering — `scored_at` remains the semantic
answer, and the surrogate key only decides between runs that claim the
same instant.

## Consequences
- **Easier:** a usable risk distribution for M5; a decision rule that is
  tested code rather than prose; requirements that disqualify rather than
  merely subtract points.
- **Harder / accepted:** the selected model needs a derivation for
  per-student drivers, scoped as its own question in the scoring task.
  Calibration adds an inner cross-validation to every fit.
- **Given up:** raw gradient boosting's slightly better discrimination.
- **Revisit triggers:** real institutional data, where the threshold
  sweep would have a genuine curve and the separability caveat could be
  tested; a cohort large enough for a true holdout; M5 finding 20%
  interior share insufficient for a readable distribution.
