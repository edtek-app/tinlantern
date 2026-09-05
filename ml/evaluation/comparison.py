"""Head-to-head model comparison.

Both candidates run through the **same folds with the same seed**, so any
difference is the model rather than the split. At 120 learners a different
split would move the numbers more than a different model does.

**How the winner is decided:**

1. **Evidence first.** A model that is at least as good on *both* mean
   per-archetype recall and precision, and strictly better on one,
   dominates. A dominating model wins — there is no reason to prefer one
   that is worse on every axis being measured.
2. **Only a genuine trade-off is a tie** — one model better on recall, the
   other on precision, neither dominating. Then interpretability decides,
   because M3 asks for per-student drivers and a model that makes them
   cheaper is preferable when the evidence does not separate.

*This rule was corrected mid-comparison, and the correction matters.* The
first version compared mean per-archetype recall alone and called anything
inside a ten-point band a tie. On the real cohort that declared a tie while
gradient boosting was missing zero learners against logistic regression's
two, and raising five fewer false alarms — precision 0.949 against 0.833,
a gap the rule could not see. A tiebreak that fires when one model
dominates is not a tiebreak; it is a way of reaching a predetermined
answer. Recorded in the model ADR.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml.evaluation.metrics import Report, evaluate
from ml.evaluation.split import N_SPLITS, SEED, out_of_fold_scores
from ml.src.model import ALERT_THRESHOLD, score, supports_per_learner_drivers

#: Metrics the decision compares. A model must be at least as good on
#: every one of them, and strictly better on at least one, to dominate.
DECISION_METRICS = ("mean_reportable_recall", "precision")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One model in the comparison."""

    name: str
    factory: object
    interpretable: bool


@dataclass(frozen=True, slots=True)
class Comparison:
    """The measured head-to-head, and how it was decided."""

    reports: tuple[Report, ...]
    winner: str
    reason: str
    tied: bool

    def summary(self) -> str:
        parts = [report.summary() for report in self.reports]
        parts.append(f"decision: {self.winner}\n  because {self.reason}")
        return "\n\n".join(parts)


def _learner_for(factory):
    def learn(train_x, train_y, test_x) -> pd.Series:
        model = factory()
        model.fit(train_x, train_y)
        return score(model, test_x)

    return learn


def run_comparison(
    absolute: pd.DataFrame,
    target: pd.Series,
    archetypes: pd.Series,
    candidates: tuple[Candidate, ...],
    threshold: float = ALERT_THRESHOLD,
    n_splits: int = N_SPLITS,
    seed: int = SEED,
) -> Comparison:
    """Evaluate every candidate on identical folds and decide between them.

    Args:
        absolute: Absolute features, indexed by learner.
        target: The binary outcome.
        archetypes: Each learner's archetype, for per-archetype recall.
        candidates: The models to compare.
        threshold: Alert threshold applied to every candidate alike.
        n_splits: Folds, identical across candidates.
        seed: Split seed, identical across candidates.

    Returns:
        The reports and the decision.
    """
    reports = tuple(
        evaluate(
            candidate.name,
            target,
            out_of_fold_scores(
                absolute, target, _learner_for(candidate.factory), n_splits, seed
            ),
            archetypes,
            threshold,
        )
        for candidate in candidates
    )
    return decide(reports, candidates)


def decide(
    reports: tuple[Report, ...], candidates: tuple[Candidate, ...]
) -> Comparison:
    """Apply the stated decision rule to measured reports.

    A tested function rather than a judgement recorded in prose, so the
    reasoning cannot drift from what was actually applied.
    """
    by_name = {candidate.name: candidate for candidate in candidates}
    undominated = [
        report
        for report in reports
        if not any(_dominates(other, report) for other in reports)
    ]

    if len(undominated) == 1:
        winner = undominated[0]
        beaten = [r.name for r in reports if r.name != winner.name]
        return Comparison(
            reports=reports,
            winner=winner.name,
            reason=(
                f"at least as good as {', '.join(beaten)} on both mean "
                f"per-archetype recall ({_mean_reportable_recall(winner):.1%}) "
                f"and precision ({winner.precision:.3f}), and strictly better "
                "on one — a dominating model wins on the evidence"
            ),
            tied=False,
        )

    interpretable = [r for r in undominated if by_name[r.name].interpretable]
    pool = interpretable or undominated
    chosen = max(pool, key=_mean_reportable_recall)
    reason = (
        "no model dominates — they trade recall against precision — so "
        "interpretability decides: M3 asks for top contributing features "
        "per student, and neither model hands those over directly, but a "
        "linear model makes the derivation far cheaper"
        if interpretable
        else "no model dominates and none is interpretable; highest mean "
        "per-archetype recall breaks the tie"
    )
    return Comparison(reports=reports, winner=chosen.name, reason=reason, tied=True)


def _dominates(a: Report, b: Report) -> bool:
    """Whether `a` is at least as good as `b` everywhere and better somewhere."""
    if a.name == b.name:
        return False
    scores = [
        (_mean_reportable_recall(a), _mean_reportable_recall(b)),
        (a.precision, b.precision),
    ]
    return all(x >= y for x, y in scores) and any(x > y for x, y in scores)


def _mean_reportable_recall(report: Report) -> float:
    """Mean recall over archetypes with enough learners to measure."""
    usable = [e.recall for e in report.per_archetype_recall.values() if e.reportable]
    return sum(usable) / len(usable) if usable else 0.0


def candidates() -> tuple[Candidate, ...]:
    """The two models M3 compares."""
    from ml.src.model import build_gradient_boosting, build_model

    return (
        Candidate("logistic regression", build_model, interpretable=True),
        Candidate(
            "gradient boosting",
            build_gradient_boosting,
            interpretable=supports_per_learner_drivers(build_gradient_boosting()),
        ),
    )
