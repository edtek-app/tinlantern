# M3 — Risk model evaluation

> **Provenance.** Generated 2026-09-06T16:21:16+00:00 from commit `488ef0e3a9553905b941c02e75b8a910a1bc5acf`, cohort seed `20260301`, over 192,431 statements and 120 learners.
>
> Regenerate with `make report`. If these counts do not match the current warehouse, this report is stale.

## Read this first: the cohort is more separable than reality

An uncalibrated gradient-boosted tree scored **117 of 120 learners at exactly 0.0 or 1.0** on this data. A model finding an almost deterministic rule is evidence that the synthetic cohort is far more separable than real learner records would be.

**This qualifies every headline figure below.** Recall near 100% here does not predict recall near 100% on institutional data; it partly measures how cleanly the generator draws its archetypes. The generator was built to make risk emerge from behaviour (ADR-0004), not to make it easy — but six behavioural archetypes with distinct score distributions are still tidier than a real cohort, and the numbers should be read as an upper bound.

## How to read this

**Per-archetype recall leads.** Aggregate AUC is secondary and is reported with its caveat attached, never alone: secondary — this cohort's struggling learners are highly engaged AND at risk, so aggregate AUC flatters any model that catches them on scores alone; read per-archetype recall first.

An archetype with fewer than 5 at-risk learners is marked unreportable rather than quoted. One learner moves such a figure by tens of points; that is not a measurement.

## Recall by archetype

| archetype | n | logistic regression | gradient boosting | gradient boosting (calibrated) |
|---|---|---|---|---|
| disengaging | 21 | 95.2% | 100.0% | 95.2% |
| struggling | 14 | 100.0% | 100.0% | 100.0% |
| recovering | 2 | unreportable (floor 5) | unreportable (floor 5) | unreportable (floor 5) |

**The selected model's recall arrives with a cost attached.** Calibrating the tree ensemble drops disengaging recall from 100% to 95.2% — one fewer disengaging learner caught out of 21 — in exchange for a risk distribution an advisor can triage against. Raw gradient boosting caught that learner and scored 117 of 120 at exactly 0 or 1, which M5's cohort overview cannot render and no advisor can rank. The trade was made deliberately: one missed learner against a score that means nothing for anybody.

## Score usability — a stated requirement, not a metric

M5 requires a cohort overview showing a risk **distribution** and a per-student score. A model that pins nearly every learner to 0 or 1 can produce neither: a histogram of two bars is not a distribution, and a ranking of ties is not a ranking. The floor below was fixed before any result was seen, and a candidate failing it fails a requirement rather than scoring lower — exactly like a model that cannot explain a learner.

| model | learners scored in the interior | meets requirement |
|---|---|---|
| logistic regression | 38.3% | yes |
| gradient boosting | 2.5% | no — disqualified |
| gradient boosting (calibrated) | 63.3% | yes |

_Interior means a score strictly between 0.05 and 0.95; the floor is 20%._

Calibration is what rescued the tree ensemble: Platt scaling fitted inside each fold turned a near-binary scorer into a usable one, at the cost of some discrimination. That trade is visible in the recall table above.

## Aggregate metrics

| model | AUC | precision | recall | F1 |
|---|---|---|---|---|
| logistic regression | 0.984 | 0.833 | 0.946 | 0.886 |
| gradient boosting | 0.996 | 0.949 | 1.000 | 0.974 |
| gradient boosting (calibrated) | 0.990 | 0.923 | 0.973 | 0.947 |

_AUC is secondary: secondary — this cohort's struggling learners are highly engaged AND at risk, so aggregate AUC flatters any model that catches them on scores alone; read per-archetype recall first._

## How the model was chosen — including the rule that was wrong

**The first decision rule chose logistic regression. It was wrong, and the choice reversed once it was fixed.**

That sequence is reported because a reader seeing only "gradient boosting selected" cannot tell whether the comparison was run honestly. This is the evidence.

1. **v1 of the rule** compared mean per-archetype recall alone and treated anything inside a ten-point band as a tie, then let interpretability decide. It selected logistic regression.
2. **The rule was blind to precision.** Gradient boosting was missing zero at-risk learners against logistic regression's two, and raising five fewer false alarms — precision 0.949 against 0.833. A tiebreak that fires while one model dominates is not a tiebreak; it is a way of reaching a predetermined answer.
3. **v2 decides on evidence first.** A model at least as good on both mean per-archetype recall and precision, and strictly better on one, dominates and wins. Only a genuine trade-off is a tie, and only then does interpretability decide.

**Selected: gradient boosting (calibrated)** — at least as good as logistic regression on both mean per-archetype recall (97.6%) and precision (0.923), and strictly better on one — a dominating model wins on the evidence

### A pre-registered prediction, partially wrong

Before running the comparison it was predicted that gradient boosting would not meaningfully beat logistic regression, because the problem is near-saturated at n=120.

- **Correct on the leading metric**: mean per-archetype recall differed by 2.4%, inside noise.
- **Wrong overall**: gradient boosting is strictly better on both axes — 0 missed against 2, and 2 false alarms against 7. The precision gap of 11.6 points is what v1 of the rule could not see.

On this cohort "stronger model" measures whether added complexity *costs* anything, not whether it buys anything.

## What four weeks can and cannot see

At the production window, **engagement-volume features do not separate the disengaging archetype** — the early-alert target. Their events (491) sit near thriving learners' (582) and their active days are indistinguishable (24.1 against 24.3), because their engagement curve has only fallen from 0.80 to 0.62 and the disengagement has not happened yet.

**Score-and-failure patterns do separate them**, and the multivariate model reaches 100% recall on them at four weeks. The result is the contrast: how much a learner shows up does not distinguish them this early; what happens when they do.

## Window sensitivity

The production window is **4 weeks by product decision** (ADR-0004). This curve is evidence about what that choice costs — not a search for the width that scores best. Two weeks is included so the curve looks in both directions: one that only looked toward more data would be an argument for waiting rather than a measurement of the trade.

| window | model | disengaging recall | precision | AUC |
|---|---|---|---|---|
| 2w | logistic regression | 85.7% | 0.786 | 0.961 |
| 2w | gradient boosting | 81.0% | 0.969 | 0.984 |
| 2w | gradient boosting (calibrated) | 90.5% | 0.897 | 0.988 |
| 4w **(production)** | logistic regression | 95.2% | 0.833 | 0.984 |
| 4w **(production)** | gradient boosting | 100.0% | 0.949 | 0.996 |
| 4w **(production)** | gradient boosting (calibrated) | 95.2% | 0.923 | 0.990 |
| 6w | logistic regression | 100.0% | 0.881 | 0.995 |
| 6w | gradient boosting | 100.0% | 0.946 | 0.993 |
| 6w | gradient boosting (calibrated) | 100.0% | 0.897 | 0.994 |
| 8w | logistic regression | 100.0% | 0.878 | 0.996 |
| 8w | gradient boosting | 90.5% | 0.917 | 0.990 |
| 8w | gradient boosting (calibrated) | 100.0% | 0.854 | 0.991 |

## Alert threshold

**The operational reasoning comes first.** A missed at-risk learner costs far more than an unnecessary advisor conversation: the first is a student who needed help and did not get it, the second is twenty minutes. The threshold is therefore set below 0.5, deliberately accepting false alarms to avoid misses.

The sweep below shows what that costs. **It is not a curve to maximise** — choosing the threshold where a metric peaks would fit the decision to this cohort.

| threshold | precision | recall | missed | false alarms |
|---|---|---|---|---|
| 0.20 | 0.900 | 0.973 | 1 | 4 |
| 0.25 | 0.900 | 0.973 | 1 | 4 |
| 0.30 | 0.900 | 0.973 | 1 | 4 |
| 0.35 | 0.923 | 0.973 | 1 | 3 |
| 0.40 | 0.923 | 0.973 | 1 | 3 |
| 0.50 | 1.000 | 0.946 | 2 | 0 |
| 0.60 | 1.000 | 0.919 | 3 | 0 |

See ADR-0007 for the argument and the chosen value.

## Limitations

- **Synthetic data, and unusually separable.** See the opening section: an uncalibrated tree ensemble reduced this cohort to a near-deterministic rule. Whether any of this generalises to real institutional data is an open question, not a solved one (ADR-0002).
- **The alert threshold is inert for the selected model over the operational range.** Between 0.20 and 0.60 the decisions do not change. The threshold is set by operational reasoning, and this cohort provides no evidence either for or against the specific value — see ADR-0007.
- **Out-of-fold, not a held-out cohort.** At 120 learners a single holdout would leave per-archetype recall resting on ~6 learners. Repeated model selection against a cross-validation estimate can overfit it; two models compared once makes that negligible, but extensive iteration would call for a genuine untouched holdout.
- **`recovering` is unreportable** at n=2 at risk.
- **Neither model produces per-student attributions directly.** Logistic regression's coefficients are global weights; coefficient x value is a derivation that would be needed either way. The linear model makes it cheaper, not automatic.

