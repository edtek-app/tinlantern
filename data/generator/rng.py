"""Seed derivation for reproducible cohort generation.

Every random draw in the generator comes from a stream derived from the
root seed plus a *scope* (which learner, which course) and a *stream name*
(what the numbers are for). See ADR-0003.

Two properties this buys, neither of which a single shared stream has:

* **Order independence.** A learner's history depends on that learner's
  identity, not on how many learners were generated before them. Adding a
  121st learner leaves the first 120 untouched.
* **Stream independence.** Timestamps and scores draw from separate
  streams, so drawing one more timestamp cannot shift a single score.

The digest is ``hashlib.blake2b``, never the builtin ``hash()``: Python
salts string hashing per process, so a ``hash()``-derived seed would
produce a different cohort on every run of the same command.
"""

from __future__ import annotations

import hashlib
import random

#: Named draw purposes. Adding a stream means adding it here — a typo'd
#: name would silently create a *different* stream, so it fails loudly
#: instead. Same deliberate-choice mechanism as the packages manifest.
STREAMS: frozenset[str] = frozenset(
    {
        "archetypes",  # assigning learners to behavioural archetypes
        "timestamps",  # when a learner acts
        "scores",  # assessment results
        "outcomes",  # end-of-term ground truth
    }
)

# 8 bytes is ample entropy for seeding random.Random and keeps the derived
# seed small enough to log and eyeball.
_DIGEST_BYTES = 8

# Parts are joined with NUL, which cannot appear in a course key, a stream
# name, or a decimal integer — so ("ab", "c") and ("a", "bc") cannot
# collide into the same payload.
_SEPARATOR = b"\x00"


def derive_seed(root_seed: int, stream: str, *scope: int | str) -> int:
    """Derive a stable sub-seed from a root seed, a stream, and a scope.

    Args:
        root_seed: The cohort's configured seed.
        stream: What the numbers are for; must be one of ``STREAMS``.
        *scope: Identity of the thing being generated, e.g. a learner index
            and a course key.

    Returns:
        A seed stable across processes, machines, and cohort sizes.

    Raises:
        ValueError: If the stream is unknown or a scope part contains NUL.
    """
    if stream not in STREAMS:
        raise ValueError(f"unknown stream {stream!r}; known: {sorted(STREAMS)}")

    parts = [str(root_seed), stream, *(str(part) for part in scope)]
    if any("\x00" in part for part in parts):
        raise ValueError("seed scope parts must not contain NUL")

    payload = _SEPARATOR.join(part.encode("utf-8") for part in parts)
    digest = hashlib.blake2b(payload, digest_size=_DIGEST_BYTES).digest()
    return int.from_bytes(digest, "big")


def stream_rng(root_seed: int, stream: str, *scope: int | str) -> random.Random:
    """Return an independent RNG for one stream of one scope.

    Args:
        root_seed: The cohort's configured seed.
        stream: What the numbers are for; must be one of ``STREAMS``.
        *scope: Identity of the thing being generated.

    Returns:
        A ``random.Random`` seeded deterministically for that combination.
    """
    return random.Random(derive_seed(root_seed, stream, *scope))
