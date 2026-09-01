"""Guards on the dev container definition (audit rulings #1 and #14).

The container must ship Python 3.12 and Docker-in-Docker, absorb the
known host-friction fixes, and contain no references to personal tooling.
"""

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.m0

DEVCONTAINER_DIR = Path(__file__).resolve().parents[1] / ".devcontainer"
DEVCONTAINER_JSON = DEVCONTAINER_DIR / "devcontainer.json"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?<![:\"'])//[^\n\"]*")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _load_jsonc(path: Path) -> dict:
    """Parse a JSONC file (strip comments and trailing commas)."""
    text = path.read_text(encoding="utf-8")
    text = _BLOCK_COMMENT.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    text = _TRAILING_COMMA.sub(r"\1", text)
    return json.loads(text)


def _feature(config: dict, needle: str) -> dict | None:
    for key, value in config.get("features", {}).items():
        if needle in key:
            return value if isinstance(value, dict) else {}
    return None


def test_devcontainer_json_is_valid_jsonc() -> None:
    assert DEVCONTAINER_JSON.is_file()
    _load_jsonc(DEVCONTAINER_JSON)


def test_python_312_feature_declared() -> None:
    python = _feature(_load_jsonc(DEVCONTAINER_JSON), "features/python")
    assert python is not None, "python feature is not declared"
    assert str(python.get("version")) == "3.12"


def test_docker_in_docker_feature_declared() -> None:
    config = _load_jsonc(DEVCONTAINER_JSON)
    assert _feature(config, "docker-in-docker") is not None


def test_common_utils_feature_declared() -> None:
    """common-utils provides the non-root user and passwordless sudo."""
    config = _load_jsonc(DEVCONTAINER_JSON)
    assert _feature(config, "common-utils") is not None


def test_locale_is_pinned() -> None:
    env = _load_jsonc(DEVCONTAINER_JSON).get("containerEnv", {})
    assert env.get("LANG") and env.get("LC_ALL")


def test_workspace_marked_git_safe_directory() -> None:
    command = _load_jsonc(DEVCONTAINER_JSON).get("postCreateCommand", "")
    assert "safe.directory /workspace" in command


def test_firewall_script_and_dockerfile_removed() -> None:
    assert not (DEVCONTAINER_DIR / "init-firewall.sh").exists()
    assert not (DEVCONTAINER_DIR / "Dockerfile").exists()


def test_devcontainer_is_free_of_personal_tooling_references() -> None:
    forbidden = re.compile(r"claude|anthropic", re.IGNORECASE)
    offenders = [
        path.name
        for path in DEVCONTAINER_DIR.rglob("*")
        if path.is_file() and forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"personal tooling references in: {offenders}"
