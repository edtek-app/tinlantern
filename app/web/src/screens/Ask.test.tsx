import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Ask } from "./Ask";

const ANSWER = {
  answered: true,
  text: "38 of 120 learners are flagged.",
  refusal_reason: null,
  citations: { "row:0": { alerted_learners: 38 } },
  sql: "SELECT count(*) FROM warehouse.risk_score WHERE alerted",
  problems: [],
};

function ask(text = "How many are flagged?") {
  render(<Ask />);
  fireEvent.change(screen.getByLabelText(/A question about this cohort/), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

function respond(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

describe("Ask", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says how long the wait usually is, from measured latency", async () => {
    // Honest and unchanging. Stage indication is not available under a
    // synchronous endpoint, and a timed animation of stages the client
    // cannot observe would be telemetry-shaped fiction.
    let release: (value: Response) => void = () => {};
    vi.stubGlobal("fetch", () => new Promise<Response>((r) => (release = r)));

    ask();

    expect(await screen.findByText(/usually takes 5 to 12 seconds/)).toBeVisible();
    release({ ok: true, status: 200, json: () => Promise.resolve(ANSWER) } as Response);
    await waitFor(() => expect(screen.queryByText(/usually takes/)).toBeNull());
  });

  it("shows the answer with its query and cited rows", async () => {
    vi.stubGlobal("fetch", () => respond(ANSWER));

    ask();

    expect(await screen.findByText(/38 of 120 learners are flagged/)).toBeVisible();
    expect(screen.getByText(/SELECT count\(\*\)/)).toBeInTheDocument();
    expect(screen.getByText("alerted_learners")).toBeInTheDocument();
  });

  it("presents a refusal as an answer, not an error", async () => {
    // A refusal is the system working. Rendering it as a failure would
    // tell a director they asked wrongly.
    vi.stubGlobal("fetch", () =>
      respond({
        answered: false,
        text: "I can't answer that from this data. No attendance is held.",
        refusal_reason: "No attendance is held.",
        citations: {},
        sql: null,
        problems: [],
      }),
    );

    ask("How does attendance correlate with risk?");

    expect(await screen.findByText(/No attendance is held/)).toBeVisible();
    expect(screen.queryByText(/could not be sent/)).toBeNull();
  });

  it("shows what verification objected to when an answer is withheld", async () => {
    vi.stubGlobal("fetch", () =>
      respond({
        answered: false,
        text: "I can't answer that from this data.",
        refusal_reason: "The answer could not be verified.",
        citations: { "row:0": { learners: 4 } },
        sql: "SELECT count(*) FROM warehouse.dim_student",
        problems: ["claim 0 contains 91, which is not in the row it cites"],
      }),
    );

    ask();

    expect(await screen.findByText(/contains 91/)).toBeVisible();
    expect(screen.getByText(/SELECT count/)).toBeInTheDocument();
  });

  it("surfaces a provider failure rather than swallowing it", async () => {
    vi.stubGlobal("fetch", () => respond({ detail: "unreachable" }, 503));

    ask();

    await waitFor(() =>
      expect(screen.getByText(/503|unreachable/)).toBeInTheDocument(),
    );
  });

  it("will not send an empty question", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<Ask />);
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
