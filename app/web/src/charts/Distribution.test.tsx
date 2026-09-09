import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Bin } from "../api-types";
import { Distribution } from "./Distribution";

const BINS: Bin[] = [
  { label: "low", lower: 0, upper: 0.175, learners: 40 },
  { label: "approaching", lower: 0.175, upper: 0.35, learners: 42 },
  { label: "alerted", lower: 0.35, upper: 0.675, learners: 30 },
  { label: "high", lower: 0.675, upper: 1, learners: 8 },
];

describe("Distribution", () => {
  it("renders a bar for every bin", () => {
    const { container } = render(<Distribution bins={BINS} threshold={0.35} />);

    expect(container.querySelectorAll("rect")).toHaveLength(BINS.length);
  });

  it("shows each bin's learner count, not just its shape", () => {
    render(<Distribution bins={BINS} threshold={0.35} />);

    // A chart whose bars are the right height and whose numbers are
    // absent is a picture, not a report.
    for (const bin of BINS) {
      expect(screen.getByText(String(bin.learners))).toBeInTheDocument();
    }
  });

  it("marks the bins at or above the threshold as alerted", () => {
    const { container } = render(<Distribution bins={BINS} threshold={0.35} />);

    // The two bins whose lower edge is >= 0.35. If this ever disagrees
    // with the server's `alerted` count, the chart and the flag are
    // telling a director different stories about the same learner.
    expect(container.querySelectorAll(".bar-alerted")).toHaveLength(2);
  });

  it("does not divide by zero when every bin is empty", () => {
    const empty = BINS.map((bin) => ({ ...bin, learners: 0 }));

    const { container } = render(<Distribution bins={empty} threshold={0.35} />);

    for (const rect of container.querySelectorAll("rect")) {
      expect(Number(rect.getAttribute("height"))).toBe(0);
    }
  });
});
