"""The cohort roster: learner identities and archetype assignment.

Learners are identified by an opaque **account**, never an ``mbox``. Even
fictional ``mailto:`` addresses would put email-shaped data into a
repository whose entire FERPA claim is that nothing resembling a learner
record enters it — and an address in a screenshot, a log line, or a demo
cannot be un-seen. An opaque id like ``s-00417`` cannot be mistaken for
contact details (ADR-0002).

Archetypes are drawn independently per learner from the configured mix,
using that learner's own ``archetypes`` stream. Quota-based assignment
would match the mix exactly, but changing the cohort size would then
reshuffle everyone — so learner 17 is whoever they are regardless of
whether the cohort has 120 learners or 12,000 (ADR-0003). The realized mix
is therefore approximate, converging on the configured one as the cohort
grows.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.xapi import Account, Agent
from data.generator.config import CohortConfig
from data.generator.rng import stream_rng


@dataclass(frozen=True, slots=True)
class Learner:
    """One synthetic learner: a stable identity and a behavioural archetype."""

    index: int
    identifier: str
    agent: Agent
    archetype: str


def learner_identifier(index: int) -> str:
    """Return the opaque account name for a learner index."""
    return f"s-{index:05d}"


def assign_archetype(config: CohortConfig, index: int) -> str:
    """Draw one learner's archetype from the configured mix.

    The draw uses only the root seed and the learner's index, so it does
    not depend on cohort size or on generation order.

    Args:
        config: The cohort configuration.
        index: Zero-based learner index.

    Returns:
        The archetype name assigned to that learner.
    """
    rng = stream_rng(config.seed, "archetypes", index)
    # Sorted for a stable iteration order: dict order would otherwise make
    # the assignment depend on how the YAML happened to be written.
    names = sorted(config.archetype_mix)
    weights = [config.archetype_mix[name] for name in names]
    return rng.choices(names, weights=weights, k=1)[0]


def build_learner(config: CohortConfig, index: int) -> Learner:
    """Build one learner's identity and archetype.

    Args:
        config: The cohort configuration.
        index: Zero-based learner index.

    Returns:
        The learner, with an account-identified xAPI agent.
    """
    identifier = learner_identifier(index)
    return Learner(
        index=index,
        identifier=identifier,
        agent=Agent(account=Account(homePage=config.base_iri, name=identifier)),
        archetype=assign_archetype(config, index),
    )


def build_roster(config: CohortConfig) -> tuple[Learner, ...]:
    """Build the whole cohort roster.

    Args:
        config: The cohort configuration.

    Returns:
        One ``Learner`` per configured learner, in index order.
    """
    return tuple(build_learner(config, index) for index in range(config.learners))


def realized_mix(roster: tuple[Learner, ...]) -> dict[str, float]:
    """Return the archetype proportions a roster actually contains.

    The realized mix drifts from the configured one by sampling noise; that
    is the accepted cost of order-independent assignment. Useful for
    inspecting a cohort before committing to a demo seed.
    """
    if not roster:
        return {}
    counts: dict[str, int] = {}
    for learner in roster:
        counts[learner.archetype] = counts.get(learner.archetype, 0) + 1
    return {name: count / len(roster) for name, count in sorted(counts.items())}
