"""Metrics for the risk model.

**Per-archetype recall leads. Aggregate AUC is secondary, and its framing
travels with it.** In this cohort `struggling` learners are both highly
engaged and all at risk, so engagement volume points the wrong way and an
aggregate figure flatters any model that catches them on scores alone.
Quoting the AUC without that context misleads, so `Report.summary()`
prints the caveat next to the number rather than in a footnote.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sklearn.metrics import (
    precision_recall_fscore_support,
    roc_auc_score,
)

#: Printed beside every aggregate figure. Not a footnote: a number quoted
#: without it is misleading.
AUC_CAVEAT = (
    "secondary — this cohort's struggling learners are highly engaged AND "
    "at risk, so aggregate AUC flatters any model that catches them on "
    "scores alone; read per-archetype recall first"
)


#: Below this many at-risk learners, a per-archetype recall is not a
#: measurement — one learner moves it by tens of points. Stated once and
#: applied uniformly: such archetypes are marked unreportable rather than
#: quoted, and every recall figure carries its n.
MIN_REPORTABLE_N = 5


@dataclass(frozen=True, slots=True)
class ArchetypeRecall:
    """One archetype's recall, with the n it rests on."""

    archetype: str
    recall: float
    n: int

    @property
    def reportable(self) -> bool:
        """Whether n is large enough for the figure to mean anything."""
        return self.n >= MIN_REPORTABLE_N

    def line(self) -> str:
        if not self.reportable:
            return (
                f"    {self.archetype:<16} unreportable (n={self.n}, "
                f"floor {MIN_REPORTABLE_N})"
            )
        return f"    {self.archetype:<16} {self.recall:6.1%}  (n={self.n})"


@dataclass(frozen=True, slots=True)
class Report:
    """One model's measured performance."""

    name: str
    threshold: float
    auc: float
    precision: float
    recall: float
    f1: float
    per_archetype_recall: dict[str, ArchetypeRecall] = field(default_factory=dict)
    calibration: tuple[tuple[float, float, int], ...] = ()

    def summary(self) -> str:
        """Per-archetype recall first, then the caveated aggregate."""
        lines = [f"{self.name}  (alert threshold {self.threshold:.2f})"]
        lines.append("  recall by archetype:")
        for entry in sorted(
            self.per_archetype_recall.values(),
            key=lambda e: (not e.reportable, -e.recall),
        ):
            lines.append(entry.line())
        lines.append(
            f"  precision {self.precision:.3f}  recall {self.recall:.3f}  "
            f"f1 {self.f1:.3f}"
        )
        lines.append(f"  AUC {self.auc:.3f}  [{AUC_CAVEAT}]")
        return "\n".join(lines)


def per_archetype_recall(
    truth: pd.Series, predicted: pd.Series, archetypes: pd.Series
) -> dict[str, ArchetypeRecall]:
    """Share of each archetype's at-risk learners that were flagged.

    The metric that leads the report: a model can post a strong aggregate
    while missing an entire archetype, and for an early-alert system
    missing one is the failure that matters.

    Each figure carries its n. An archetype with too few at-risk learners
    is not a measurement, and quoting it as one would be the same error as
    quoting an uncaveated aggregate.
    """
    frame = pd.DataFrame(
        {"truth": truth, "predicted": predicted, "archetype": archetypes}
    )
    at_risk = frame[frame["truth"].astype(bool)]
    return {
        str(name): ArchetypeRecall(
            archetype=str(name),
            recall=float(group["predicted"].astype(bool).mean()),
            n=int(len(group)),
        )
        for name, group in at_risk.groupby("archetype")
    }


def calibration_bins(
    truth: pd.Series, scores: pd.Series, bins: int = 5
) -> tuple[tuple[float, float, int], ...]:
    """(mean predicted, observed rate, count) per score bin.

    A model can rank well and still be badly calibrated, and an advisor
    told "0.8 risk" reasonably expects roughly four in five to struggle.
    """
    frame = pd.DataFrame({"truth": truth.astype(float), "score": scores})
    frame["bin"] = pd.cut(frame["score"], bins=bins, labels=False, include_lowest=True)
    return tuple(
        (float(group["score"].mean()), float(group["truth"].mean()), int(len(group)))
        for _, group in frame.groupby("bin", observed=True)
    )


def evaluate(
    name: str,
    truth: pd.Series,
    scores: pd.Series,
    archetypes: pd.Series,
    threshold: float,
) -> Report:
    """Measure one model's out-of-fold predictions."""
    predicted = (scores >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        truth.astype(int), predicted, average="binary", zero_division=0
    )
    return Report(
        name=name,
        threshold=threshold,
        auc=float(roc_auc_score(truth.astype(int), scores)),
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        per_archetype_recall=per_archetype_recall(truth, predicted, archetypes),
        calibration=calibration_bins(truth, scores),
    )
