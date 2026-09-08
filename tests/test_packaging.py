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
DEV_ONLY_ROOTS = frozenset({"data", "evals", "tests", "infra", "tools"})

#: Packages that must never ship even though their parent does. Label
#: handling is an evaluation concern; shipping code that reads the
#: ground-truth sidecar into production would undo ADR-0002.
NEVER_SHIPPED = ("ml.evaluation",)


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


def test_label_handling_never_ships() -> None:
    """The sidecar must not reach the deployed runtime (ADR-0002).

    ml/src is production code and ships; ml/evaluation reads labels and
    does not. Enforced by the manifest rather than by trusting that
    nothing imports it — a convention about imports is not a mechanism.
    """
    listed = set(_packages())
    for package in NEVER_SHIPPED:
        assert package not in listed, f"{package} would ship to the M6 runtime"
        assert not [p for p in listed if p.startswith(f"{package}.")]

    from ml.evaluation import load_labels  # noqa: F401  importable, unshipped


def test_generator_is_importable_without_being_shipped() -> None:
    """Both halves of the rule at once: importable, and not in the manifest."""
    from data.generator import load_config  # noqa: F401

    assert not [name for name in _packages() if name.split(".")[0] == "data"]


def test_application_packages_are_listed_explicitly() -> None:
    """app/ is shipped, and every subpackage needs its own entry."""
    packages = _packages()
    assert "app" in packages
    assert "app.xapi" in packages
    assert "app.llm" in packages, "the LLM client is application code"
    assert "app.dashboard" in packages, "the dashboard reads ship to M6"
    assert "ml.src" in packages, "promoted model code must ship"


def test_prompts_travel_with_the_package() -> None:
    """Prompts are data files loaded by name, so they need declaring.

    Listing `app.llm` ships the modules; without a package-data entry the
    `prompts/` directory is left behind and every LLM feature raises on a
    fresh install — a failure that never appears in a source checkout.
    """
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["app.llm"]
    assert any(pattern.startswith("prompts/") for pattern in patterns)
