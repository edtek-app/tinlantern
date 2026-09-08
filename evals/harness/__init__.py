"""The M4 eval harness. Development tooling — never shipped (ADR-0002).

Two suites over one runner: the golden question set, and the advisor
summariser's fallback rate. Both run against `stub` in the gate, where
they test the HARNESS, and against the real provider through
`make evals`, where they test the model.
"""
