"""What the demo can answer.

Product data, not tooling. The question set lives here rather than in
`tools/` because the API tells a visitor which questions are available,
and `tools/` is development-only — it never ships (ADR-0002's packaging
manifest), so an endpoint importing it works locally and fails in a
deployed runtime.

`tools/record_demo_responses.py` imports this list; the dependency runs
that way and not the other.
"""

from __future__ import annotations

#: The questions the demo has recorded answers for.
#:
#: Deliberately includes one the system must REFUSE. A demo that only
#: shows successes teaches nothing about the edge, and refusing well is
#: the strongest result M4 measured — 6/6 across five runs.
DEMO_QUESTIONS: tuple[str, ...] = (
    "How many learners are currently flagged as at risk?",
    "What is the average scaled score across all graded work?",
    "Which verb appears most often in the activity records?",
    "What is each learner's final grade for the course?",
)
