import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EngagementPoint } from "../api-types";
import { Trend } from "./Trend";

const POINTS: EngagementPoint[] = [
  { week_start: "2026-02-02", active_learners: 100, events: 900 },
  { week_start: "2026-02-09", active_learners: 80, events: 700 },
  { week_start: "2026-02-16", active_learners: 40, events: 300 },
];

describe("Trend", () => {
  it("plots a point per week", () => {
    const { container } = render(<Trend points={POINTS} />);

    expect(container.querySelectorAll("circle")).toHaveLength(POINTS.length);
    expect(container.querySelector("path")?.getAttribute("d")).toMatch(/^M/);
  });

  it("labels itself as engagement, never as risk", () => {
    // Only one feature window has ever been scored, so no risk trend
    // exists to draw. The API refuses to serve one; this must not imply
    // one, and a caption edited in six months would be how it starts to.
    const { container } = render(<Trend points={POINTS} />);

    expect(screen.getByText(/Active learners per week/)).toBeInTheDocument();
    expect(screen.getByText(/engagement, not risk/)).toBeInTheDocument();
    expect(container.textContent?.toLowerCase()).not.toContain("risk score");
  });

  it("falls as engagement falls", () => {
    // A trend that renders but ignores its data is a decoration. Higher
    // engagement must sit higher on the chart — lower y, since SVG's
    // origin is top-left.
    const { container } = render(<Trend points={POINTS} />);
    const ys = [...container.querySelectorAll("circle")].map((node) =>
      Number(node.getAttribute("cy")),
    );

    expect(ys).toHaveLength(POINTS.length);
    for (let index = 1; index < ys.length; index += 1) {
      expect(ys[index - 1]!).toBeLessThan(ys[index]!);
    }
  });

  it("survives a single week without dividing by zero", () => {
    const { container } = render(<Trend points={POINTS.slice(0, 1)} />);

    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelector("path")?.getAttribute("d")).not.toContain(
      "NaN",
    );
  });
});
