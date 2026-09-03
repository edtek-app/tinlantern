"""Development-only tooling: the synthetic data generator.

Importable from the repository root, but deliberately absent from the
``packages`` list in ``pyproject.toml``. That list is the deployment
manifest — if it is listed, it ships to the M6 runtime. A synthetic data
generator has no business in a production Lambda.

Invoke via ``make seed``, which runs from the repository root.
"""
