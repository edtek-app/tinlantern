"""The advisor summariser: grounding, the two checks, and the fallback.

Every test builds its own facts and its own stub registry. Nothing here
reads the database or calls a model, because `summarise` takes a fact
object rather than a connection — which is the point of that signature.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from app.llm.client import Completion, ModelRefused, UnregisteredPrompt
from app.llm.providers.stub import StubProvider, canned
from app.llm.summary import (
    FALLBACK_MARKER,
    SUMMARY_SCHEMA,
    AdvisorSummary,
    DriverFact,
    FallbackReason,
    LearnerFacts,
    build_prompt,
    decomposition_phrasing,
    fallback_rate,
    summarise,
    template_summary,
    ungrounded_numbers,
    verify,
)
from ml.src.drivers import CONTRIBUTION_CAVEAT

pytestmark = pytest.mark.m4


def facts() -> LearnerFacts:
    """One learner, with figures that are easy to check by eye."""
    return LearnerFacts(
        learner="s-00417",
        risk=0.82,
        alerted=True,
        threshold=0.35,
        window_close=date(2026, 2, 23),
        model_version="abc1234",
        drivers=(
            DriverFact("failures", 0.31),
            DriverFact("mean_score_in_window", 0.18),
        ),
        cohort_size=120,
        cohort_alerted=38,
        caveat=CONTRIBUTION_CAVEAT,
    )


GROUNDED_PAYLOAD = {
    "claims": [
        {"text": "Risk is 0.82, above the 0.35 alert threshold.", "source": "risk"},
        {
            "text": "failures is the largest single factor, raising risk by 0.31.",
            "source": "driver:failures",
        },
        {"text": "38 of 120 learners alerted in this window.", "source": "cohort"},
    ],
    "suggested_next_step": (
        "Ask how the recent assessments felt and whether anything is "
        "getting in the way of resubmitting."
    ),
}


def provider_returning(payload: dict, learner_facts: LearnerFacts) -> StubProvider:
    """A stub registered for exactly this learner's request."""
    from app.llm.prompt_library import load_prompt

    return StubProvider(
        canned(
            load_prompt("grounding"),
            build_prompt(learner_facts),
            json.dumps(payload),
        )
    )


class Refusing:
    """A provider that declines, as the real one can."""

    name = "refusing"

    def complete(self, *, system: str, prompt: str) -> Completion:
        raise ModelRefused("declined")

    def complete_json(
        self, *, system: str, prompt: str, schema: dict
    ) -> tuple[dict, Completion]:
        raise ModelRefused("declined")


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_grounded_payload_becomes_a_summary() -> None:
    subject = facts()
    result = summarise(subject, provider_returning(GROUNDED_PAYLOAD, subject))

    assert result.from_model is True
    assert result.fallback_reason is None
    assert result.problems == ()
    assert "0.82" in result.text
    assert "Suggested next step:" in result.text
    assert FALLBACK_MARKER not in result.text


def test_the_prompt_carries_this_learners_own_figures() -> None:
    """A stub keyed on request text cannot answer for the wrong learner.

    Two learners differing only in score produce different prompts, so a
    response canned for one is not reachable by the other. Without this
    the whole stub arrangement would let a summary about learner A be
    served for learner B and nothing would notice.
    """
    subject = facts()
    other = replace(subject, risk=0.11, alerted=False)

    assert build_prompt(subject) != build_prompt(other)
    assert "0.82" in build_prompt(subject)

    provider = provider_returning(GROUNDED_PAYLOAD, subject)
    with pytest.raises(UnregisteredPrompt):
        summarise(other, provider)


# --------------------------------------------------------------------------
# Layer 1 — claim verification
# --------------------------------------------------------------------------


def test_a_number_that_was_never_supplied_is_rejected() -> None:
    """The central check: a figure with no supplied source is a fabrication."""
    subject = facts()
    payload = {
        "claims": [
            {
                "text": "Risk is 0.82, and attendance has fallen to 41 percent.",
                "source": "risk",
            }
        ],
        "suggested_next_step": "Ask about workload.",
    }

    problems = verify(payload, subject)

    assert problems, "41 traces to no supplied fact and must be caught"
    assert "41" in problems[0]


def test_a_claim_citing_a_fact_that_was_never_supplied_is_rejected() -> None:
    subject = facts()
    payload = {
        "claims": [{"text": "Attendance is poor.", "source": "attendance"}],
        "suggested_next_step": "Ask about workload.",
    }

    problems = verify(payload, subject)

    assert any("attendance" in problem for problem in problems)
    assert any("never supplied" in problem for problem in problems)


def test_percentages_of_a_supplied_proportion_are_grounded() -> None:
    """ "82%" of a risk of 0.82 is the same fact, not an invention.

    Without this the check would reject the register an advisor actually
    reads in, and the model would be pushed toward less useful prose to
    satisfy a rule that was measuring formatting.
    """
    subject = facts()
    assert ungrounded_numbers("Risk is 82%.", subject) == ()
    assert ungrounded_numbers("Risk is 73%.", subject) == (73.0,)


def test_the_window_is_a_date_so_an_invented_time_is_still_rejected() -> None:
    """The summariser holds a DATE, and a time of day is not in it.

    Q&A had to start admitting clock digits, because its rows really do
    hold timestamps. That fix must not leak here: `LearnerFacts` carries
    `window_close` as a date, so "00:00 UTC" is a precision the model was
    never given. Rejecting it is correct, and pinning that keeps the two
    fact shapes from being quietly conflated.
    """
    subject = facts()

    assert ungrounded_numbers("the window closing 2026-02-23", subject) == ()
    assert ungrounded_numbers("the window closing 2026-02-23 09:30 UTC", subject) != ()


def test_a_thousands_separator_is_one_number_not_two() -> None:
    """ "192,431" is a figure, not a 192 and a 431.

    Four of eight failures in the first real-provider eval run were this
    defect. Canned test data never had a number large enough to need a
    separator, so a check that split on commas passed every test while
    rejecting correct answers about a 192,431-statement warehouse.
    """
    subject = replace(facts(), cohort_size=192431)

    assert ungrounded_numbers("There are 192,431 events.", subject) == ()
    assert ungrounded_numbers("There are 192,432 events.", subject) == (192432.0,)


def test_the_opaque_identifier_is_not_read_as_a_figure() -> None:
    """`s-00417` is a fact, but its digits are not a quantity.

    Only the exact supplied literals are exempt — a *different*
    identifier or date stays visible to the check, so the exemption
    cannot be used to smuggle a number through.
    """
    subject = facts()
    assert ungrounded_numbers("Learner s-00417 scored 0.82.", subject) == ()
    assert ungrounded_numbers("Model abc1234 produced it.", subject) == ()
    assert 99.0 in ungrounded_numbers("Learner s-00099 scored 0.82.", subject)


def test_a_figure_in_the_suggested_next_step_is_rejected() -> None:
    """Advice carries no citation, so nothing could check a number in it."""
    subject = facts()
    payload = {
        "claims": GROUNDED_PAYLOAD["claims"],
        "suggested_next_step": "Meet within 3 days.",
    }

    problems = verify(payload, subject)

    assert any("next step" in problem for problem in problems)


# --------------------------------------------------------------------------
# Layer 2 — the rendered prose, and the additivity constraint
# --------------------------------------------------------------------------


def test_presenting_drivers_as_a_decomposition_is_rejected() -> None:
    """`additive: false` is a fact about the method, not a wording taste.

    Contributions are counterfactual and they interact, so a summary
    saying they "account for" the score states something false about the
    model. Caught on the rendered prose, where a reader would meet it.
    """
    subject = facts()
    payload = {
        "claims": [
            {
                "text": "failures accounts for 0.31 of the score.",
                "source": "driver:failures",
            }
        ],
        "suggested_next_step": "Ask about workload.",
    }

    result = summarise(subject, provider_returning(payload, subject))

    assert result.from_model is False
    assert result.fallback_reason is FallbackReason.VERIFICATION_FAILED
    assert any("decomposition" in problem for problem in result.problems)


def test_the_phrasing_check_reads_the_rendered_text() -> None:
    assert decomposition_phrasing("These sum to the score.") == ("sum to",)
    assert decomposition_phrasing("failures raises risk by 0.31.") == ()


# --------------------------------------------------------------------------
# The fallback
# --------------------------------------------------------------------------


def test_verification_failure_still_produces_a_summary() -> None:
    """An advisor sees plain prose, never an empty record.

    The fallback is what makes the model an enhancement rather than a
    dependency: a program director opening a learner and finding nothing
    is worse than a summary without the model's phrasing.
    """
    subject = facts()
    payload = {
        "claims": [{"text": "Attendance is 41 percent.", "source": "risk"}],
        "suggested_next_step": "Ask about workload.",
    }

    result = summarise(subject, provider_returning(payload, subject))

    assert result.text.startswith(FALLBACK_MARKER)
    assert result.fallback_reason is FallbackReason.VERIFICATION_FAILED
    assert result.problems, "the reason must be surfaced, not absorbed"
    assert "0.82" in result.text, "the fallback still reports the score"


def test_a_refusal_falls_back_with_its_own_reason() -> None:
    """Distinguishable from a verification failure, per the client's own
    refusal-versus-empty distinction: they need different responses."""
    result = summarise(facts(), Refusing())

    assert result.fallback_reason is FallbackReason.PROVIDER_REFUSED
    assert result.text.startswith(FALLBACK_MARKER)


def test_the_template_is_grounded_by_construction() -> None:
    """Mechanically, not by assertion: it is subject to the same check.

    Code writes it from the same curated facts, so if it ever contains a
    figure that traces to nothing, this fails — which is what makes
    "grounded by construction" a property rather than a claim.
    """
    subject = facts()
    text = template_summary(subject)

    assert ungrounded_numbers(text, subject) == ()
    assert decomposition_phrasing(text, subject) == ()
    assert CONTRIBUTION_CAVEAT in text


def test_the_caveats_own_negation_is_not_read_as_a_violation() -> None:
    """The check cannot tell a claim from its negation, so it is scoped.

    The supplied caveat says the contributions "do NOT sum to the risk
    score" — forbidden wording asserting the opposite of the thing
    forbidden. Applied to generated text the blacklist is useful;
    applied to our own words it fires on itself. Scoping it is the fix;
    softening the list would be giving up the check.
    """
    subject = facts()

    assert "sum to" in decomposition_phrasing(CONTRIBUTION_CAVEAT)
    assert decomposition_phrasing(CONTRIBUTION_CAVEAT, subject) == ()


def test_an_unregistered_request_raises_rather_than_falling_back() -> None:
    """The fallback must not swallow the stub's whole purpose.

    Catching every LLMError here would turn "nobody wrote a canned
    response" into a silently templated summary, and the raising stub —
    the thing that stops a test passing for the wrong reason — would
    stop being visible at exactly the layer that uses it.
    """
    with pytest.raises(UnregisteredPrompt):
        summarise(facts(), StubProvider({}))


def test_fallback_rate_is_derived_from_what_was_served() -> None:
    """A system quietly serving templates looks healthy on every other
    signal, so the rate is a metric the eval harness reports."""

    def summary(from_model: bool) -> AdvisorSummary:
        return AdvisorSummary(
            text="x",
            from_model=from_model,
            fallback_reason=None if from_model else FallbackReason.PROVIDER_REFUSED,
            synthetic=True,
            problems=(),
        )

    assert fallback_rate([]) == 0.0
    assert fallback_rate([summary(True), summary(True)]) == 0.0
    assert fallback_rate([summary(True), summary(False)]) == 0.5


# --------------------------------------------------------------------------
# The contract with the model
# --------------------------------------------------------------------------


def test_the_schema_forbids_unlisted_fields() -> None:
    """A stray field is a schema violation, not something to interpret."""
    assert SUMMARY_SCHEMA["additionalProperties"] is False
    claim = SUMMARY_SCHEMA["properties"]["claims"]["items"]
    assert claim["additionalProperties"] is False
    assert claim["required"] == ["text", "source"]


def test_the_facts_carry_no_cohort_median() -> None:
    """Deliberate absence, and it must stay deliberate.

    The median profile the contributions were ablated against is fitted
    at scoring time and not persisted. Any median recomputed now could
    differ from the one those numbers describe, so quoting it would be a
    quietly wrong comparison — worse than an absent one. If a median is
    ever added here it must come from the stored payload, not from a
    fresh computation.
    """
    citable = facts().citable()

    assert not [key for key in citable if "median" in key or "typical" in key]
    assert "driver:failures" in citable
