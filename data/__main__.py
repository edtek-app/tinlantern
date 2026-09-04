"""``python -m data`` is not a command; the tools are named.

Kept so a mistyped invocation says something useful instead of failing
with an import error.
"""

import sys

print(
    "data is a package of development tools, not a command. Try:\n"
    "  python -m data.generator   # generate a synthetic cohort (make seed)\n"
    "  python -m data.ingest      # load one into a running API (make ingest)",
    file=sys.stderr,
)
raise SystemExit(2)
