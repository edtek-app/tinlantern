"""Shared pytest configuration.

Enforces ruling #6: every test must carry a milestone marker so it is
attributable to a ``make gate-mN`` run. An unmarked test would silently
escape every gate.
"""

import pytest

MILESTONE_MARKERS = frozenset({"m0", "m1", "m2", "m3", "m4", "m5", "demo"})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    unmarked = [
        item.nodeid
        for item in items
        if MILESTONE_MARKERS.isdisjoint(m.name for m in item.iter_markers())
    ]
    if unmarked:
        raise pytest.UsageError(
            f"tests are missing a milestone marker ({sorted(MILESTONE_MARKERS)}): "
            + ", ".join(unmarked)
        )
