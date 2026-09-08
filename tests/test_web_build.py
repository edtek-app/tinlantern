"""The frontend's contract with the gate.

M5's gate clause says "frontend builds", and a gate that does not build
it is not checking it. `make gate-m5` runs `npm run build` before the
Python suite; these tests guard the things that would make that build
meaningless rather than merely green.

They do NOT run npm — the gate already does, and a pytest that shelled
out to a second build would double the slowest step to assert what the
first one proved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.m5

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def package_json() -> dict:
    return json.loads((WEB / "package.json").read_text(encoding="utf-8"))


def test_every_dependency_is_pinned_exactly() -> None:
    """A floating range makes the gate a different check every day.

    `^19.2.0` builds today and may not tomorrow, and the failure would
    arrive attached to whatever commit happened to be pushed when
    upstream published. Exact pins make an upgrade a visible diff.
    """
    manifest = package_json()
    floating = {
        name: spec
        for section in ("dependencies", "devDependencies")
        for name, spec in manifest.get(section, {}).items()
        if not re.fullmatch(r"\d+\.\d+\.\d+", spec)
    }

    assert not floating, (
        f"unpinned frontend dependencies: {floating}. Pin the exact "
        "version — a gate that builds against a moving target is not a "
        "gate, and the break lands on whoever pushed next."
    )


def test_the_lockfile_is_tracked() -> None:
    """`npm ci` needs it, and transitive pins live only there.

    Pinning direct dependencies is not enough: without the lockfile the
    transitive tree floats, which is most of the tree.
    """
    assert (WEB / "package-lock.json").is_file(), (
        "package-lock.json is missing or ignored. `npm ci` requires it, "
        "and direct pins say nothing about transitive versions."
    )


def test_the_build_type_checks_before_bundling() -> None:
    """vite bundles happily through type errors; tsc does not.

    A build script that only ran vite would report success on code the
    compiler rejects, which is the same shape as a green gate that
    skipped its suite.
    """
    build = package_json()["scripts"]["build"]

    assert "tsc --noEmit" in build, (
        "the build no longer type-checks. vite strips types without "
        "checking them, so `vite build` alone reports success on code "
        "tsc rejects."
    )
    assert build.index("tsc") < build.index("vite"), "type-check first"


# A guard that api.ts declares no interfaces of its own was written and
# REMOVED. It scanned the file for the word "interface" and failed on its
# own explanatory comment — the same defect as the source scan that once
# tripped over its own docstring, and the reason the standing rule says
# guards assert behaviour, never source text.
#
# There is no cheap behavioural form: a parallel interface beside the
# generated one compiles fine, so nothing observable changes. The
# contract itself is guarded by `test_the_committed_types_match_the
# _models`; a duplicate definition is left to review, and this note is
# here so its absence is a decision rather than an oversight.
