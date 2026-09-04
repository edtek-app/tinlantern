"""Risk modelling: features, models, scoring, and their evaluation.

`ml.src` is production code and ships. `ml.evaluation` reads the
ground-truth sidecar and deliberately does NOT ship — the packaging
manifest keeps label-reading code out of the deployed runtime
mechanically, rather than relying on nobody importing it (ADR-0002).
"""
