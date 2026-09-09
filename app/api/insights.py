"""The LLM-backed endpoints: a learner's summary, and Q&A.

**Synchronous, for M5.** Q&A is two sequential model calls. **Measured
end to end against the real provider, eight questions: 5.0-11.7 seconds,
median ~7.** An earlier figure of "10-25 seconds" appears in some
commit messages; it was an estimate that was never measured, and this
range replaces it.

    REVISIT TRIGGER: API Gateway's hard integration timeout is 29
    SECONDS. The measured range leaves real headroom, but the sample is
    eight questions on one cohort and says nothing about the tail — a
    slow plan on a harder question can still cross it. M6 either moves
    this to submit-then-poll, or streams, or runs somewhere without that
    ceiling. Whoever hits a 504 in M6 should find this note before they
    start debugging the model.

    **Streaming is also what makes honest stage indication possible.**
    Under this synchronous endpoint the client observes nothing between
    request and response, so the UI shows a plain spinner rather than
    pretending to know which stage is running.

**A `MalformedResponse` is retried exactly once, and the retry is
recorded.** The eval harness saw 0 in 90 question-runs — enough to call
it rare, far too thin to size a policy against — so a user-facing
question should not die on a transient, and production is where the real
rate comes from. Every retry lands in `warehouse.llm_call`, because a
retry rate nobody can see is the "looks healthy while broken" pattern.

**Transport failures are not retried.** ADR-0008 draws that boundary: a
429 or a 5xx is a retryable outage whose policy belongs to whoever owns
the request budget, and absorbing one here would disguise a broken
provider as a slow one. They surface as 503.

Status codes, kept distinguishable on purpose:

- **200 with `answered: false`** — a refusal. The system working. A 4xx
  would blame the caller for a reasonable question the warehouse cannot
  answer.
- **502** — malformed after the retry. The provider gave us something
  unusable.
- **503** — transport. The provider could not be reached.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.api import schemas
from app.api.schemas import Question
from app.dashboard.queries import learner_detail
from app.dashboard.summaries import read
from app.dashboard.telemetry import Outcome, record, timed
from app.db import transaction
from app.llm.client import (
    MalformedResponse,
    ModelRefused,
    Provider,
    build_client,
    is_transport_failure,
)
from app.llm.qa import ask

router = APIRouter(prefix="/api", tags=["insights"])

#: One retry, then surface it. Two would be a policy nobody measured.
MAX_ATTEMPTS = 2


def _provider() -> Provider:
    """The configured provider. Overridden in tests by dependency override."""
    return build_client()


@router.get("/learners/{identifier}/summary", response_model=schemas.LearnerSummary)
def summary(identifier: str) -> dict:
    """The stored advisor summary for a learner.

    Reads only. The scoring job writes summaries (`make score`), so a
    page view never calls a model — and a missing summary is reported
    as missing rather than generated inline, because absence and
    emptiness read identically on screen and only one is true.
    """
    with transaction() as connection:
        detail = learner_detail(connection, identifier)
        if detail is None:
            raise HTTPException(
                status_code=404, detail=f"no current score for {identifier!r}."
            )
        stored = read(connection, identifier, detail.model_version)

    if stored is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no summary generated for {identifier!r} under model "
                f"{detail.model_version}. Summaries are written by the "
                "scoring job — run `make score`. This is a missing "
                "summary, not an empty one."
            ),
        )
    return asdict(stored)


@router.post("/ask", response_model=schemas.Answer)
def ask_question(body: Question) -> dict:
    """Answer a question from the warehouse, with citations.

    Raises:
        HTTPException: 502 if the provider's response could not be
            parsed after one retry; 503 if the provider was
            unreachable. A refusal is not an error — it returns 200
            with ``answered`` false and what is missing.
    """
    provider = _provider()
    model = getattr(provider, "model", provider.name)
    last: MalformedResponse | None = None
    unreachable: BaseException | None = None

    for attempt in range(MAX_ATTEMPTS):
        # Every path out of this block leaves it NORMALLY, so the
        # telemetry row commits. Raising inside would roll back the
        # record of the very failure being reported.
        with transaction() as connection, timed() as timer:
            try:
                answer = ask(body.question, connection, provider)
            except MalformedResponse as broken:
                last = broken
                record(
                    connection,
                    operation="qa",
                    outcome=(
                        Outcome.RETRIED
                        if attempt < MAX_ATTEMPTS - 1
                        else Outcome.MALFORMED
                    ),
                    provider=provider.name,
                    model=model,
                    latency_ms=timer.elapsed_ms,
                    detail=str(broken),
                )
                answer = None
            except ModelRefused as declined:
                # Specific before broad: ModelRefused is an Exception, and
                # a broad handler placed first would swallow a refusal and
                # serve it as a fault.
                record(
                    connection,
                    operation="qa",
                    outcome=Outcome.REFUSED,
                    provider=provider.name,
                    model=model,
                    latency_ms=timer.elapsed_ms,
                    detail=str(declined),
                )
                return {
                    "answered": False,
                    "text": "I can't answer that. The model declined.",
                    "refusal_reason": str(declined),
                    "citations": {},
                    "sql": None,
                    "problems": [],
                }
            except Exception as failure:
                # Broad, then immediately narrowed: `is_transport_failure`
                # classifies inside app/llm so this module never imports
                # the SDK (ADR-0001). Anything else re-raises unchanged —
                # a bug must not be served as a 503.
                if not is_transport_failure(failure):
                    raise
                record(
                    connection,
                    operation="qa",
                    outcome=Outcome.TRANSPORT_ERROR,
                    provider=provider.name,
                    model=model,
                    latency_ms=timer.elapsed_ms,
                    detail=str(failure),
                )
                unreachable = failure
                answer = None
            else:
                record(
                    connection,
                    operation="qa",
                    outcome=Outcome.OK if answer.answered else Outcome.REFUSED,
                    provider=provider.name,
                    model=model,
                    latency_ms=timer.elapsed_ms,
                    detail=answer.refusal_reason,
                )
                return {
                    "answered": answer.answered,
                    "text": answer.text,
                    "refusal_reason": answer.refusal_reason,
                    # The rows a claim cited, and the statement that
                    # produced them. A citation panel renders THESE —
                    # never an expected column set, which varies run to
                    # run while the answer does not (ADR-0008).
                    "citations": answer.citations,
                    "sql": answer.query.sql if answer.query else None,
                    "problems": list(answer.problems),
                }

        if unreachable is not None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "the language model provider could not be reached. Not "
                    "retried here on purpose: a retryable outage absorbed at "
                    "this layer looks like a slow feature rather than a "
                    "broken provider (ADR-0008)."
                ),
            ) from unreachable

    raise HTTPException(
        status_code=502,
        detail=f"the model's response could not be parsed, after one retry. {last}",
    )
