"""Tests for the cohort roster.

Two things matter here. The FERPA posture has to be a gate rather than a
habit: no email-shaped data may appear anywhere in a serialized roster,
checked by scanning the output rather than by trusting the constructor.
And archetype assignment has to stay independent of cohort size, or every
learner's identity would shift the moment the cohort grew.
"""

import json
from pathlib import Path

import pytest

from app.xapi import Agent
from data.generator.config import ARCHETYPES, CohortConfig, load_config
from data.generator.roster import (
    assign_archetype,
    build_learner,
    build_roster,
    learner_identifier,
    realized_mix,
)
from tests.ferpa import assert_no_identifying_data

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "generator" / "cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


def serialize(roster) -> str:
    """Dump a roster to the JSON a sidecar or debug log would contain."""
    return json.dumps(
        [
            {
                "index": learner.index,
                "identifier": learner.identifier,
                "archetype": learner.archetype,
                "agent": learner.agent.model_dump(
                    by_alias=True, exclude_none=True, mode="json"
                ),
            }
            for learner in roster
        ]
    )


# --------------------------------------------------------------------------
# FERPA posture — mechanical, not habitual
# --------------------------------------------------------------------------


def test_no_email_shaped_data_anywhere_in_a_roster(config: CohortConfig) -> None:
    """An address in a screenshot, log, or demo cannot be un-seen."""
    assert_no_identifying_data(serialize(build_roster(config)), "roster")


def test_agents_are_identified_by_account_never_mbox(config: CohortConfig) -> None:
    for learner in build_roster(config):
        assert learner.agent.account is not None
        assert learner.agent.mbox is None


def test_identifiers_are_opaque(config: CohortConfig) -> None:
    """s-00417 cannot be mistaken for contact details."""
    for learner in build_roster(config):
        assert learner.identifier.startswith("s-")
        assert learner.identifier[2:].isdigit()


def test_agents_carry_no_display_name(config: CohortConfig) -> None:
    """Narrative names arrive at M7 for a curated few, not for the cohort."""
    assert all(learner.agent.name is None for learner in build_roster(config))


# --------------------------------------------------------------------------
# The statement contract
# --------------------------------------------------------------------------


def test_every_agent_survives_the_xapi_contract(config: CohortConfig) -> None:
    for learner in build_roster(config):
        wire = learner.agent.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert Agent.model_validate(wire) == learner.agent


def test_account_home_page_is_the_configured_base(config: CohortConfig) -> None:
    for learner in build_roster(config):
        assert learner.agent.account is not None
        assert learner.agent.account.homePage == config.base_iri


# --------------------------------------------------------------------------
# Roster shape
# --------------------------------------------------------------------------


def test_roster_size_matches_config(config: CohortConfig) -> None:
    assert len(build_roster(config)) == config.learners


def test_identifiers_are_unique(config: CohortConfig) -> None:
    roster = build_roster(config)
    assert len({learner.identifier for learner in roster}) == len(roster)


def test_identifier_is_zero_padded() -> None:
    """Padding keeps identifiers sortable as strings."""
    assert learner_identifier(417) == "s-00417"
    assert learner_identifier(0) == "s-00000"


def test_every_archetype_is_a_configured_one(config: CohortConfig) -> None:
    for learner in build_roster(config):
        assert learner.archetype in config.archetype_mix
        assert learner.archetype in ARCHETYPES


# --------------------------------------------------------------------------
# Assignment: order independence and proportions
# --------------------------------------------------------------------------


def test_assignment_is_deterministic(config: CohortConfig) -> None:
    assert build_roster(config) == build_roster(load_config(EXAMPLE))


def test_growing_the_cohort_leaves_existing_learners_untouched(
    config: CohortConfig,
) -> None:
    """The ADR-0003 property, on the scope that actually matters."""
    small = build_roster(config)
    large = build_roster(config.model_copy(update={"learners": config.learners * 10}))
    assert large[: len(small)] == small


def test_different_seed_reassigns_archetypes(config: CohortConfig) -> None:
    other = config.model_copy(update={"seed": config.seed + 1})
    mine = [learner.archetype for learner in build_roster(config)]
    theirs = [learner.archetype for learner in build_roster(other)]
    assert mine != theirs


def test_realized_mix_converges_on_the_configured_mix(config: CohortConfig) -> None:
    """Independent draws approximate the mix; they do not enforce it."""
    large = build_roster(config.model_copy(update={"learners": 20_000}))
    realized = realized_mix(large)
    for name, intended in config.archetype_mix.items():
        assert abs(realized[name] - intended) < 0.02, (
            f"{name}: realized {realized[name]:.3f} vs intended {intended}"
        )


def test_realized_mix_is_not_exact_at_cohort_scale(config: CohortConfig) -> None:
    """Property guard: pins identity-stable assignment from its consequence.

    Do NOT "fix" this by deleting it or by making the mix exact. An exact
    realized mix is only achievable by quota assignment — handing out a
    pre-counted list of archetypes — and that makes every learner's
    archetype depend on the cohort size. Learner 17 would become a
    different person the moment a 121st learner was added, breaking
    test_growing_the_cohort_leaves_existing_learners_untouched above and
    the reproducibility guarantee in ADR-0003 with it.

    Sampling noise here is not a defect being tolerated; it is the visible
    evidence that assignment is drawn from identity rather than position.
    """
    realized = realized_mix(build_roster(config))
    assert realized != config.archetype_mix


def test_mix_ordering_does_not_change_assignment(config: CohortConfig) -> None:
    """Assignment must not depend on how the YAML happened to be written."""
    reordered = config.model_copy(
        update={"archetype_mix": dict(reversed(list(config.archetype_mix.items())))}
    )
    assert [learner.archetype for learner in build_roster(reordered)] == [
        learner.archetype for learner in build_roster(config)
    ]


def test_single_archetype_mix_assigns_everyone(config: CohortConfig) -> None:
    forced = config.model_copy(update={"archetype_mix": {"disengaging": 1.0}})
    assert {learner.archetype for learner in build_roster(forced)} == {"disengaging"}


def test_assign_archetype_needs_no_roster(config: CohortConfig) -> None:
    """One learner's archetype is answerable without building the cohort."""
    assert assign_archetype(config, 17) == build_learner(config, 17).archetype


def test_realized_mix_of_an_empty_roster_is_empty() -> None:
    assert realized_mix(()) == {}
