"""Smoke test: the pytest harness and its marker enforcement are wired up."""

import pytest

pytestmark = pytest.mark.m0


def test_milestone_markers_registered(pytestconfig: pytest.Config) -> None:
    lines = pytestconfig.getini("markers")
    registered = {line.split(":", 1)[0].strip() for line in lines}
    expected = {"m0", "m1", "m2", "m3", "m4", "m5", "demo"}
    assert expected <= registered
