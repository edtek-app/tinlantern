import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Answer } from "../api-types";
import { Citations } from "./Citations";

const ANSWERED: Answer = {
  answered: true,
  text: "38 of 120 learners alerted.",
  refusal_reason: null,
  citations: { "row:0": { alerted_learners: 38, scored_learners: 120 } },
  sql: "SELECT count(*) FROM warehouse.risk_score WHERE alerted",
  problems: [],
};

describe("Citations", () => {
  it("shows the executed SQL verbatim", () => {
    render(<Citations answer={ANSWERED} />);

    expect(screen.getByText(/SELECT count\(\*\)/)).toBeInTheDocument();
  });

  it("renders whatever columns the row actually has", () => {
    // Column shape varies between runs (ADR-0008). A panel expecting a
    // fixed set would flicker for questions whose answer never changed,
    // and the cause would look like the model.
    const other: Answer = {
      ...ANSWERED,
      citations: { "row:0": { verb: "experienced", n: 57752 } },
    };

    render(<Citations answer={other} />);

    expect(screen.getByText("verb")).toBeInTheDocument();
    expect(screen.getByText(/experienced/)).toBeInTheDocument();
    expect(screen.getByText(/57752/)).toBeInTheDocument();
  });

  it("shows the query on a refusal, when one ran", () => {
    // "Here is what I ran and why I didn't trust it" beats a bare
    // refusal.
    const withheld: Answer = {
      ...ANSWERED,
      answered: false,
      text: "I can't answer that from this data.",
      refusal_reason: "The answer could not be verified.",
      problems: ["claim 0 contains 91, which is not in the row it cites"],
    };

    render(<Citations answer={withheld} />);

    expect(screen.getByText(/SELECT count\(\*\)/)).toBeInTheDocument();
    expect(screen.getByText(/alerted_learners/)).toBeInTheDocument();
  });

  it("renders nothing when no query ran", () => {
    const refusedEarly: Answer = {
      answered: false,
      text: "I can't answer that from this data.",
      refusal_reason: "The warehouse holds no attendance data.",
      citations: {},
      sql: null,
      problems: [],
    };

    const { container } = render(<Citations answer={refusedEarly} />);

    expect(container).toBeEmptyDOMElement();
  });
});
