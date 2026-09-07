"""Advisor summaries: grounded by construction, checked by code.

The model is not asked for a paragraph. It is asked for **claims, each
naming the fact it rests on**, and this module verifies every one against
the facts it supplied before arranging them into prose (ADR-0008). A
claim citing a fact that was never supplied does not reach an advisor.

Two layers, deliberately overlapping:

1. **Claim verification** — every claim's ``source`` must be a fact that
   was actually handed over, and every number in the claim must trace to
   one of those facts.
2. **A numeric check on the rendered prose** — the same test applied to
   what a human will read. It exists to catch *the renderer*, not only
   the model: the layer that assembles text is as capable of introducing
   a number as the layer that writes it.

Neither layer catches a summary whose claims are each true and whose
selection is misleading. That gap is named in ADR-0008 rather than
implied away.

**When verification fails, an advisor still gets a summary.** A template
built from the same facts is grounded by construction, so the language
model is an enhancement over something that always works rather than a
dependency — a program director opening a learner and seeing nothing is
worse than plain prose. The fallback is marked in the text and its reason
is recorded, and ``fallback_rate`` exists so that a system quietly
serving templates half the time cannot look healthy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.llm.client import (
    Completion,
    ModelRefused,
    Provider,
    ProviderNotAvailable,
)
from app.llm.grounding import numbers_in, ungrounded
from app.llm.prompt_library import load_prompt

#: Opens every fallback summary. Visible, because the difference between
#: "the model wrote this" and "the model could not" is exactly what a
#: reader needs and cannot otherwise infer.
FALLBACK_MARKER = (
    "[Plain summary — generated from the stored figures without the language model.]"
)

#: Phrasings that present drivers as a decomposition of the score. They
#: are not one: contributions are counterfactual, they interact, and they
#: do not sum (`ml/src/drivers.py`). This is a phrasing check, not a
#: semantic one — see ADR-0008's failure modes.
FORBIDDEN_DECOMPOSITION: tuple[str, ...] = (
    "accounts for",
    "account for",
    "makes up",
    "make up",
    "adds up",
    "add up",
    "sums to",
    "sum to",
    "breakdown of the score",
    "of the total risk",
)


class FallbackReason(StrEnum):
    """Why a summary came from the template instead of the model."""

    VERIFICATION_FAILED = "verification-failed"
    PROVIDER_REFUSED = "provider-refused"
    PROVIDER_UNAVAILABLE = "provider-unavailable"


@dataclass(frozen=True, slots=True)
class DriverFact:
    """One driver, as the advisor is allowed to hear it."""

    feature: str
    contribution: float

    @property
    def source(self) -> str:
        """The key a claim cites this driver by."""
        return f"driver:{self.feature}"


@dataclass(frozen=True, slots=True)
class LearnerFacts:
    """Everything the model is given, and therefore everything it may say.

    The composition is the curation decision, and it is deliberately
    narrow: a fact that was never supplied cannot be fabricated by
    paraphrase, and a context this small can be audited by eye.

    Every field comes from one row of ``warehouse.risk_score`` plus that
    window's cohort counts, so the whole set is internally consistent by
    construction. In particular there is **no cohort median here**: the
    median profile that the contributions were ablated against is fitted
    at scoring time and not persisted, so any median recomputed now could
    differ from the one the numbers describe. Quoting it would be a
    quietly wrong comparison, which is worse than an absent one.

    Attributes:
        learner: The opaque account identifier. Never a name or an email.
        risk: The calibrated probability, 0-1.
        alerted: Whether it crosses the threshold.
        threshold: The alert threshold this score was judged against.
        window_close: The date the feature window closed.
        model_version: The code that produced the score.
        drivers: Ranked, highest contribution first.
        cohort_size: Learners scored in this window.
        cohort_alerted: How many of them alerted.
        caveat: The non-additivity caveat carried with the drivers.
    """

    learner: str
    risk: float
    alerted: bool
    threshold: float
    window_close: date
    model_version: str
    drivers: tuple[DriverFact, ...]
    cohort_size: int
    cohort_alerted: int
    caveat: str

    def citable(self) -> dict[str, str]:
        """The facts a claim may cite, keyed by the name it cites them by."""
        facts = {
            "risk": f"{self.risk:.2f}",
            "threshold": f"{self.threshold:.2f}",
            "alerted": "yes" if self.alerted else "no",
            "window_close": self.window_close.isoformat(),
            "cohort": (
                f"{self.cohort_size} learners scored, {self.cohort_alerted} alerted"
            ),
        }
        for driver in self.drivers:
            facts[driver.source] = f"raises risk by {driver.contribution:.2f}"
        return facts

    def allowed_numbers(self) -> tuple[float, ...]:
        """Every quantity a claim is permitted to contain."""
        values = [
            self.risk,
            self.threshold,
            float(self.cohort_size),
            float(self.cohort_alerted),
            float(self.window_close.year),
            float(self.window_close.month),
            float(self.window_close.day),
        ]
        values.extend(driver.contribution for driver in self.drivers)
        return tuple(values)

    def as_prompt_block(self) -> str:
        """Render the facts for the model, source keys visible.

        The model has to cite by key, so the keys are what it sees.
        """
        lines = [f"- `{key}`: {value}" for key, value in self.citable().items()]
        lines.append(f"- learner: `{self.learner}` (opaque account identifier)")
        lines.append(f"- model version: `{self.model_version}`")
        lines.append(f"\nCaveat carried with the drivers: {self.caveat}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class AdvisorSummary:
    """A summary and the provenance needed to judge it.

    Attributes:
        text: What an advisor reads.
        from_model: False when the template produced it.
        fallback_reason: Why, when it did.
        synthetic: True when no model was called at all (stub provider).
        problems: What verification objected to. Kept on the result
            rather than logged and dropped, because a fallback whose
            cause is invisible is indistinguishable from a healthy one.
    """

    text: str
    from_model: bool
    fallback_reason: FallbackReason | None
    synthetic: bool
    problems: tuple[str, ...]


#: What the model must return. `additionalProperties: false` so a stray
#: field is a schema violation rather than something to reason about.
SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        },
        "suggested_next_step": {"type": "string"},
    },
    "required": ["claims", "suggested_next_step"],
    "additionalProperties": False,
}


def ungrounded_numbers(text: str, facts: LearnerFacts) -> tuple[float, ...]:
    """Numbers in ``text`` that trace to nothing this learner supplied.

    The identifier, the model version, and the window date are scrubbed
    first: they are facts, but their digits are not quantities.
    """
    return ungrounded(
        text,
        facts.allowed_numbers(),
        scrub=(facts.learner, facts.model_version, facts.window_close.isoformat()),
    )


def decomposition_phrasing(
    text: str, facts: LearnerFacts | None = None
) -> tuple[str, ...]:
    """Phrasings that present the drivers as an additive breakdown.

    A substring blacklist cannot distinguish a claim from its negation:
    the caveat we supply says the contributions "do NOT sum to the risk
    score", which contains a forbidden phrase while asserting the
    opposite. So the check applies to *generated* text only — when the
    facts are given, the caveat's own wording is removed first, on the
    same principle as `_scrub_identifiers`. The limitation is real and
    is recorded in ADR-0008's failure modes, not worked around by
    softening the list.
    """
    lowered = text.lower()
    if facts is not None:
        lowered = lowered.replace(facts.caveat.lower(), " ")
    return tuple(phrase for phrase in FORBIDDEN_DECOMPOSITION if phrase in lowered)


def verify(payload: dict, facts: LearnerFacts) -> tuple[str, ...]:
    """Check a model payload against the facts it was given.

    Args:
        payload: The parsed response, matching ``SUMMARY_SCHEMA``.
        facts: What the model was supplied.

    Returns:
        One string per problem, empty when the payload is grounded. The
        strings are surfaced on the result, so they say what was wrong
        rather than that something was.
    """
    problems: list[str] = []
    citable = facts.citable()

    claims = payload.get("claims") or []
    if not claims:
        problems.append("no claims returned")

    for index, claim in enumerate(claims):
        text = claim.get("text", "")
        source = claim.get("source", "")
        if source not in citable:
            problems.append(
                f"claim {index} cites {source!r}, which was never supplied. "
                f"Supplied facts: {sorted(citable)}"
            )
        for value in ungrounded_numbers(text, facts):
            problems.append(
                f"claim {index} contains {value:g}, which traces to no "
                "supplied fact — either the model computed something it "
                "was not given, or a fact is missing from the context"
            )

    step = payload.get("suggested_next_step", "")
    if numbers_in(step):
        problems.append(
            "the suggested next step contains a figure. It is advice, not "
            "a claim, so it carries no citation and nothing can check it — "
            "figures belong in claims"
        )

    return tuple(problems)


def render(payload: dict) -> str:
    """Arrange verified claims into what an advisor reads."""
    claims = " ".join(claim["text"].strip() for claim in payload["claims"])
    step = payload["suggested_next_step"].strip()
    return f"{claims}\n\nSuggested next step: {step}"


def template_summary(facts: LearnerFacts) -> str:
    """The fallback: grounded by construction, because code writes it.

    Built from the same curated facts, so it cannot say anything the
    model would not have been allowed to say.
    """
    band = "above" if facts.alerted else "below"
    lines = [
        FALLBACK_MARKER,
        "",
        f"Learner {facts.learner} scored {facts.risk:.2f}, {band} the "
        f"{facts.threshold:.2f} alert threshold, for the window closing "
        f"{facts.window_close.isoformat()}. Across the cohort, "
        f"{facts.cohort_alerted} of {facts.cohort_size} learners alerted.",
    ]
    if facts.drivers:
        lines.append("")
        lines.append("Largest single factors, most influential first:")
        lines.extend(
            f"- {driver.feature}: raises risk by {driver.contribution:.2f}"
            for driver in facts.drivers
        )
    lines.append("")
    lines.append(facts.caveat)
    return "\n".join(lines)


def _fallback(
    facts: LearnerFacts,
    reason: FallbackReason,
    problems: Sequence[str] = (),
    synthetic: bool = False,
) -> AdvisorSummary:
    return AdvisorSummary(
        text=template_summary(facts),
        from_model=False,
        fallback_reason=reason,
        synthetic=synthetic,
        problems=tuple(problems),
    )


def build_prompt(facts: LearnerFacts) -> str:
    """The instructions from the prompt file, plus this learner's facts.

    The facts are appended rather than interpolated into the file, so the
    prompt stays pure prose and no brace in it can become a format slot.
    """
    return f"{load_prompt('advisor_summary')}\n\n## Facts\n\n{facts.as_prompt_block()}"


def summarise(facts: LearnerFacts, provider: Provider) -> AdvisorSummary:
    """Produce a grounded summary, or the template if one is not available.

    Args:
        facts: The curated fact set. Deliberately not a connection — the
            summariser is a pure function of what it is given, so its
            behaviour is testable without a database or a model.
        provider: Where to send the request.

    Returns:
        Either a verified model summary or the template, with the reason
        recorded either way.
    """
    system = load_prompt("grounding")
    try:
        payload, completion = provider.complete_json(
            system=system, prompt=build_prompt(facts), schema=SUMMARY_SCHEMA
        )
    except ModelRefused:
        return _fallback(facts, FallbackReason.PROVIDER_REFUSED)
    except ProviderNotAvailable:
        return _fallback(facts, FallbackReason.PROVIDER_UNAVAILABLE)

    problems = verify(payload, facts)
    if problems:
        return _fallback(facts, FallbackReason.VERIFICATION_FAILED, problems)

    text = render(payload)

    # The same numeric test, now against what a human will actually read.
    # This layer exists to catch the renderer: assembling text is as
    # capable of introducing a figure as writing it.
    rendered_problems = [
        f"the rendered summary contains {value:g}, which traces to no "
        "supplied fact — the claims verified individually, so this came "
        "from rendering"
        for value in ungrounded_numbers(text, facts)
    ]
    rendered_problems.extend(
        f"the rendered summary says {phrase!r}, presenting the drivers as "
        "a decomposition of the score. They are counterfactual, they "
        "interact, and they do not sum."
        for phrase in decomposition_phrasing(text, facts)
    )
    if rendered_problems:
        return _fallback(
            facts,
            FallbackReason.VERIFICATION_FAILED,
            rendered_problems,
            synthetic=completion.synthetic,
        )

    return AdvisorSummary(
        text=text,
        from_model=True,
        fallback_reason=None,
        synthetic=completion.synthetic,
        problems=(),
    )


def fallback_rate(summaries: Sequence[AdvisorSummary]) -> float:
    """The share of summaries the model did not produce.

    Derived from the results rather than accumulated in a counter, so it
    cannot drift from what was actually served. A system quietly falling
    back half the time looks healthy on every other signal, which is why
    the eval harness reports this as a metric rather than logging it.
    """
    if not summaries:
        return 0.0
    return sum(1 for item in summaries if not item.from_model) / len(summaries)


__all__ = [
    "FALLBACK_MARKER",
    "SUMMARY_SCHEMA",
    "AdvisorSummary",
    "Completion",
    "DriverFact",
    "FallbackReason",
    "LearnerFacts",
    "build_prompt",
    "decomposition_phrasing",
    "fallback_rate",
    "render",
    "summarise",
    "template_summary",
    "ungrounded_numbers",
    "verify",
]
