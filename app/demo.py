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
#:
#: The average-score question SPECIFIES PRECISION where the golden set's
#: `mean-score` does not, and that divergence is deliberate — do not
#: "fix" it by reconciling the two lists. Without it the model wrote
#: `avg(scaled_score)` unrounded and faithfully quoted the result:
#: "0.67029573637808931927". Asking for two decimal places puts the
#: `round()` in the model's OWN generated SQL, so the figure a reader
#: sees is exactly what the cited row contains and the numeric check
#: stays exact. Rounding afterwards would make the answer disagree with
#: its own citation, and instructing the model to round in the prompt
#: would be a request it could decline on some runs — intermittent
#: failure of exactly the rendered-versus-structured kind ADR-0008
#: names. The golden question stays as it is because its reference
#: query does the rounding instead.
DEMO_QUESTIONS: tuple[str, ...] = (
    "How many learners are currently flagged as at risk?",
    "What is the average scaled score across all graded work, to two decimal places?",
    "Which verb appears most often in the activity records?",
    "What is each learner's final grade for the course?",
)
