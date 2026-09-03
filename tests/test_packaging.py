"""The `packages` list in pyproject.toml is a deployment manifest.

If a package is listed, it ships to the M6 runtime. Development tooling —
the synthetic data generator, the eval harness — must stay importable from
the repository root while never being listed.

This is the mechanical half of that rule. A future convenience edit that
adds `data` to the manifest so an import resolves more tidily would ship a
synthetic data generator into a production Lambda; here it fails a gate
instead of sliding through review.
"""

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.m0

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

#: Top-level directories that are development tooling, never shipped.
DEV_ONLY_ROOTS = frozenset({"data", "evals", "tests", "ml", "infra"})


def _packages() -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["packages"]


def test_dev_tooling_is_never_in_the_deployment_manifest() -> None:
    listed = {name.split(".")[0] for name in _packages()}
    shipped_dev_tooling = sorted(listed & DEV_ONLY_ROOTS)
    assert not shipped_dev_tooling, (
        f"{shipped_dev_tooling} would ship to the M6 runtime. The packages "
        "list is a deployment manifest; dev tooling stays importable from "
        "the repo root without being listed."
    )


def test_generator_is_importable_without_being_shipped() -> None:
    """Both halves of the rule at once: importable, and not in the manifest."""
    from data.generator import load_config  # noqa: F401

    assert not [name for name in _packages() if name.split(".")[0] == "data"]


def test_application_packages_are_listed_explicitly() -> None:
    """app/ is shipped, and every subpackage needs its own entry."""
    packages = _packages()
    assert "app" in packages
    assert "app.xapi" in packages
