import { useState } from "react";

import type { Answer } from "../api-types";
import { api } from "../api";
import { Citations } from "../components/Citations";

// The wait is a plain spinner with an honest, unchanging message.
//
// STAGE INDICATION WAS WANTED and is not honestly available here. The
// endpoint is synchronous: one POST, one response, and the client
// observes nothing in between. The pipeline knows it moved from
// planning to running to reading; this component cannot.
//
// Rejected, and recorded so neither is reconsidered as a convenience:
//
//   * A TIMED ANIMATION ticking through the three stages. It looks like
//     telemetry and is fiction — the same objection as a progress bar,
//     worse because it is more convincing.
//   * SPLITTING the endpoint into plan-then-answer so the client drives
//     the stages. That would have the browser hold and return generated
//     SQL, which means the API accepts SQL from a browser. The
//     read-only role bounds the damage; it does not change that the
//     threat model becomes a different one.
//
// M6 has to revisit this endpoint anyway — API Gateway's 29-second
// ceiling makes the synchronous version untenable in deployment — and
// streaming is what makes real stage indication possible. It is the
// first thing that version should add.
//
// The range below is MEASURED, not invented: eight questions end to end
// against the real provider, 5.0-11.7 seconds, median ~7.
const WAITING = "Working — this usually takes 5 to 12 seconds.";

export function Ask() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asking, setAsking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || asking) return;

    setAsking(true);
    setFailure(null);
    // One question, one answer. No history: at seven seconds a question
    // nobody asks rapid-fire, and a transcript is state to hold or
    // store for no demonstrated need. Revisit if anyone comparing two
    // answers asks for it.
    setAnswer(null);
    try {
      setAnswer(await api.ask(question));
    } catch (error) {
      setFailure(
        error instanceof Error ? error.message : "The question could not be sent.",
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <section>
      <h2>Ask</h2>
      <form onSubmit={submit}>
        <label htmlFor="question">A question about this cohort</label>
        <input
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="How many learners are flagged as at risk?"
        />
        <button type="submit" disabled={asking || !question.trim()}>
          Ask
        </button>
      </form>

      {asking && <p className="waiting">{WAITING}</p>}
      {failure && <p className="failure">{failure}</p>}

      {answer && !asking && (
        <article>
          {/* A refusal is the system working, so it reads as an answer
              rather than an error. */}
          <p className={answer.answered ? "answer" : "refusal"}>{answer.text}</p>
          {!answer.answered && answer.problems.length > 0 && (
            <ul className="problems">
              {answer.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          <Citations answer={answer} />
        </article>
      )}
    </section>
  );
}
