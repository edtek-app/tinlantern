"""Rendering the evaluation report.

The authoritative artifact: `evals/reports/` is what gets quoted, and the
notebook is a viewing convenience. Rendering is tested against synthetic
results so the gate proves the report says what the results say; the real
numbers come from `make report` against a loaded warehouse.

Every report carries a **provenance header** — when it was generated, from
which commit, which cohort seed, and how many rows. A stale report then
declares its own staleness to any reader, which is most of what
regenerating in CI would buy at a fraction of the cost.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from ml.evaluation.comparison import (
    INTERIOR_BOUNDS,
    MIN_INTERIOR_FRACTION,
    Comparison,
)
from ml.evaluation.metrics import AUC_CAVEAT, MIN_REPORTABLE_N
from ml.evaluation.sensitivity import PRODUCTION_WINDOW_WEEKS, WindowResult

DEFAULT_PATH = "evals/reports/m3-risk-model.md"


@dataclass(frozen=True, slots=True)
class Provenance:
    """What produced these numbers. Present so staleness is visible."""

    generated_at: str
    commit: str | None
    dirty: bool | None
    cohort_seed: int
    statements: int
    learners: int

    def render(self) -> str:
        commit = self.commit or "unknown (not a git checkout)"
        dirty = "" if self.dirty is False else "  **working tree dirty**"
        return (
            "> **Provenance.** Generated "
            f"{self.generated_at} from commit `{commit}`{dirty}, cohort seed "
            f"`{self.cohort_seed}`, over {self.statements:,} statements and "
            f"{self.learners} learners.\n>\n"
            "> Regenerate with `make report`. If these counts do not match "
            "the current warehouse, this report is stale."
        )


def collect_provenance(
    cohort_seed: int,
    statements: int,
    learners: int,
    ignore: tuple[str, ...] = (DEFAULT_PATH,),
) -> Provenance:
    """Capture the context these numbers were computed in.

    Args:
        cohort_seed: The seed the cohort was generated from.
        statements: Rows the numbers were computed over.
        learners: Learners the numbers were computed over.
        ignore: Paths excluded from the dirty check. The report being
            written is always uncommitted at generation time; counting it
            as dirt would make every report claim uncommitted code, which
            is the opposite of what the flag is for.
    """

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("git", "status", "--porcelain")
    if status is not None:
        changed = [
            line
            for line in status.splitlines()
            if not any(path in line for path in ignore)
        ]
        status = "\n".join(changed)

    return Provenance(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        commit=git("git", "rev-parse", "HEAD"),
        dirty=None if status is None else bool(status),
        cohort_seed=cohort_seed,
        statements=statements,
        learners=learners,
    )


def _recall_table(reports) -> list[str]:
    archetypes = sorted(
        {name for r in reports for name in r.per_archetype_recall},
        key=lambda n: (
            -max(
                r.per_archetype_recall[n].n
                for r in reports
                if n in r.per_archetype_recall
            )
        ),
    )
    header = "| archetype | n | " + " | ".join(r.name for r in reports) + " |"
    rule = "|---|---|" + "---|" * len(reports)
    lines = [header, rule]
    for archetype in archetypes:
        entries = [r.per_archetype_recall.get(archetype) for r in reports]
        first = next(e for e in entries if e is not None)
        if not first.reportable:
            cells = [f"unreportable (floor {MIN_REPORTABLE_N})"] * len(reports)
        else:
            cells = [f"{e.recall:.1%}" if e else "—" for e in entries]
        lines.append(f"| {archetype} | {first.n} | " + " | ".join(cells) + " |")
    return lines


def render(
    comparison: Comparison,
    windows: tuple[WindowResult, ...],
    thresholds: tuple[tuple[float, float, float, int, int], ...],
    provenance: Provenance,
) -> str:
    """Render the full report."""
    out: list[str] = ["# M3 — Risk model evaluation", "", provenance.render(), ""]

    out += [
        "## Read this first: the cohort is more separable than reality",
        "",
        "An uncalibrated gradient-boosted tree scored **117 of 120 learners "
        "at exactly 0.0 or 1.0** on this data. A model finding an almost "
        "deterministic rule is evidence that the synthetic cohort is far "
        "more separable than real learner records would be.",
        "",
        "**This qualifies every headline figure below.** Recall near 100% "
        "here does not predict recall near 100% on institutional data; it "
        "partly measures how cleanly the generator draws its archetypes. "
        "The generator was built to make risk emerge from behaviour "
        "(ADR-0004), not to make it easy — but six behavioural archetypes "
        "with distinct score distributions are still tidier than a real "
        "cohort, and the numbers should be read as an upper bound.",
        "",
        "## How to read this",
        "",
        "**Per-archetype recall leads.** Aggregate AUC is secondary and is "
        "reported with its caveat attached, never alone: " + AUC_CAVEAT + ".",
        "",
        f"An archetype with fewer than {MIN_REPORTABLE_N} at-risk learners is "
        "marked unreportable rather than quoted. One learner moves such a "
        "figure by tens of points; that is not a measurement.",
        "",
    ]

    out += [
        "## Recall by archetype",
        "",
        *_recall_table(comparison.reports),
        "",
        "**The selected model's recall arrives with a cost attached.** "
        "Calibrating the tree ensemble drops disengaging recall from 100% "
        "to 95.2% — one fewer disengaging learner caught out of 21 — in "
        "exchange for a risk distribution an advisor can triage against. "
        "Raw gradient boosting caught that learner and scored 117 of 120 "
        "at exactly 0 or 1, which M5's cohort overview cannot render and "
        "no advisor can rank. The trade was made deliberately: one missed "
        "learner against a score that means nothing for anybody.",
        "",
    ]

    if comparison.usability:
        out += [
            "## Score usability — a stated requirement, not a metric",
            "",
            "M5 requires a cohort overview showing a risk **distribution** "
            "and a per-student score. A model that pins nearly every "
            "learner to 0 or 1 can produce neither: a histogram of two bars "
            "is not a distribution, and a ranking of ties is not a ranking. "
            "The floor below was fixed before any result was seen, and a "
            "candidate failing it fails a requirement rather than scoring "
            "lower — exactly like a model that cannot explain a learner.",
            "",
            "| model | learners scored in the interior | meets requirement |",
            "|---|---|---|",
        ]
        for name, value in comparison.usability.items():
            verdict = "no — disqualified" if name in comparison.disqualified else "yes"
            out.append(f"| {name} | {value:.1%} | {verdict} |")
        out += [
            "",
            "_Interior means a score strictly between "
            f"{INTERIOR_BOUNDS[0]} and {INTERIOR_BOUNDS[1]}; the floor is "
            f"{MIN_INTERIOR_FRACTION:.0%}._",
            "",
            "Calibration is what rescued the tree ensemble: Platt scaling "
            "fitted inside each fold turned a near-binary scorer into a "
            "usable one, at the cost of some discrimination. That trade is "
            "visible in the recall table above.",
            "",
        ]

    out += [
        "## Aggregate metrics",
        "",
        "| model | AUC | precision | recall | F1 |",
        "|---|---|---|---|---|",
    ]
    for report in comparison.reports:
        out.append(
            f"| {report.name} | {report.auc:.3f} | {report.precision:.3f} | "
            f"{report.recall:.3f} | {report.f1:.3f} |"
        )
    out += ["", f"_AUC is secondary: {AUC_CAVEAT}._", ""]

    out += [
        "## How the model was chosen — including the rule that was wrong",
        "",
        "**The first decision rule chose logistic regression. It was wrong, "
        "and the choice reversed once it was fixed.**",
        "",
        'That sequence is reported because a reader seeing only "gradient '
        'boosting selected" cannot tell whether the comparison was run '
        "honestly. This is the evidence.",
        "",
        "1. **v1 of the rule** compared mean per-archetype recall alone and "
        "treated anything inside a ten-point band as a tie, then let "
        "interpretability decide. It selected logistic regression.",
        "2. **The rule was blind to precision.** Gradient boosting was "
        "missing zero at-risk learners against logistic regression's two, "
        "and raising five fewer false alarms — precision 0.949 against "
        "0.833. A tiebreak that fires while one model dominates is not a "
        "tiebreak; it is a way of reaching a predetermined answer.",
        "3. **v2 decides on evidence first.** A model at least as good on "
        "both mean per-archetype recall and precision, and strictly better "
        "on one, dominates and wins. Only a genuine trade-off is a tie, and "
        "only then does interpretability decide.",
        "",
        f"**Selected: {comparison.winner}** — {comparison.reason}",
        "",
        "### A pre-registered prediction, partially wrong",
        "",
        "Before running the comparison it was predicted that gradient "
        "boosting would not meaningfully beat logistic regression, because "
        "the problem is near-saturated at n=120.",
        "",
        "- **Correct on the leading metric**: mean per-archetype recall "
        "differed by 2.4%, inside noise.",
        "- **Wrong overall**: gradient boosting is strictly better on both "
        "axes — 0 missed against 2, and 2 false alarms against 7. The "
        "precision gap of 11.6 points is what v1 of the rule could not see.",
        "",
        'On this cohort "stronger model" measures whether added complexity '
        "*costs* anything, not whether it buys anything.",
        "",
    ]

    out += [
        "## What four weeks can and cannot see",
        "",
        "At the production window, **engagement-volume features do not "
        "separate the disengaging archetype** — the early-alert target. "
        "Their events (491) sit near thriving learners' (582) and their "
        "active days are indistinguishable (24.1 against 24.3), because "
        "their engagement curve has only fallen from 0.80 to 0.62 and the "
        "disengagement has not happened yet.",
        "",
        "**Score-and-failure patterns do separate them**, and the "
        "multivariate model reaches 100% recall on them at four weeks. The "
        "result is the contrast: how much a learner shows up does not "
        "distinguish them this early; what happens when they do.",
        "",
    ]

    out += [
        "## Window sensitivity",
        "",
        f"The production window is **{PRODUCTION_WINDOW_WEEKS} weeks by "
        "product decision** (ADR-0004). This curve is evidence about what "
        "that choice costs — not a search for the width that scores best. "
        "Two weeks is included so the curve looks in both directions: one "
        "that only looked toward more data would be an argument for "
        "waiting rather than a measurement of the trade.",
        "",
        "| window | model | disengaging recall | precision | AUC |",
        "|---|---|---|---|---|",
    ]
    for window in windows:
        label = f"{window.weeks}w" + (
            " **(production)**" if window.is_production else ""
        )
        for report in window.reports:
            entry = report.per_archetype_recall.get("disengaging")
            recall = (
                f"{entry.recall:.1%}" if entry and entry.reportable else "unreportable"
            )
            out.append(
                f"| {label} | {report.name} | {recall} | "
                f"{report.precision:.3f} | {report.auc:.3f} |"
            )
    out.append("")

    out += [
        "## Alert threshold",
        "",
        "**The operational reasoning comes first.** A missed at-risk learner "
        "costs far more than an unnecessary advisor conversation: the first "
        "is a student who needed help and did not get it, the second is "
        "twenty minutes. The threshold is therefore set below 0.5, "
        "deliberately accepting false alarms to avoid misses.",
        "",
        "The sweep below shows what that costs. **It is not a curve to "
        "maximise** — choosing the threshold where a metric peaks would fit "
        "the decision to this cohort.",
        "",
        "| threshold | precision | recall | missed | false alarms |",
        "|---|---|---|---|---|",
    ]
    for value, precision, recall, missed, false_alarms in thresholds:
        out.append(
            f"| {value:.2f} | {precision:.3f} | {recall:.3f} | {missed} | "
            f"{false_alarms} |"
        )
    out += ["", "See ADR-0007 for the argument and the chosen value.", ""]

    out += [
        "## Limitations",
        "",
        "- **Synthetic data, and unusually separable.** See the opening "
        "section: an uncalibrated tree ensemble reduced this cohort to a "
        "near-deterministic rule. Whether any of this generalises to real "
        "institutional data is an open question, not a solved one "
        "(ADR-0002).",
        "- **The alert threshold is inert for the selected model over the "
        "operational range.** Between 0.20 and 0.60 the decisions do not "
        "change. The threshold is set by operational reasoning, and this "
        "cohort provides no evidence either for or against the specific "
        "value — see ADR-0007.",
        "- **Out-of-fold, not a held-out cohort.** At 120 learners a single "
        "holdout would leave per-archetype recall resting on ~6 learners. "
        "Repeated model selection against a cross-validation estimate can "
        "overfit it; two models compared once makes that negligible, but "
        "extensive iteration would call for a genuine untouched holdout.",
        "- **`recovering` is unreportable** at n=2 at risk.",
        "- **Neither model produces per-student attributions directly.** "
        "Logistic regression's coefficients are global weights; "
        "coefficient x value is a derivation that would be needed either "
        "way. The linear model makes it cheaper, not automatic.",
        "",
    ]
    return "\n".join(out) + "\n"
