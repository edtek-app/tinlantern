import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Driver } from "../api-types";
import { Drivers } from "./Drivers";

const CAVEAT =
  "Contributions are counterfactual: each is the change in risk if that " +
  "one feature were typical for the cohort. They do NOT sum to the risk " +
  "score and they interact.";

const DRIVERS: Driver[] = [
  { feature: "failures", contribution: 0.31 },
  { feature: "mean_score_in_window", contribution: 0.18 },
  { feature: "active_days", contribution: 0.09 },
];

describe("Drivers", () => {
  it("shows each driver's absolute contribution", () => {
    render(<Drivers drivers={DRIVERS} caveat={CAVEAT} />);

    expect(screen.getByText("failures")).toBeInTheDocument();
    expect(screen.getByText("0.31")).toBeInTheDocument();
    expect(screen.getByText("0.18")).toBeInTheDocument();
  });

  it("never presents the drivers as a decomposition", () => {
    // The M4 constraint, at the place it is easiest to break. A pie
    // chart, a stacked bar, a percentage, or a "top three explain X%"
    // line all assert that the parts sum to a whole. They do not: the
    // contributions are counterfactual and they interact.
    const { container } = render(<Drivers drivers={DRIVERS} caveat={CAVEAT} />);
    const text = container.textContent ?? "";

    expect(text).not.toMatch(/%/);
    expect(text.toLowerCase()).not.toMatch(/\btotal\b/);
    expect(text.toLowerCase()).not.toMatch(/explains?\b/);
    expect(container.querySelectorAll("circle")).toHaveLength(0);

    // 0.31 + 0.18 + 0.09 = 0.58. If that ever appears, something has
    // started summing them.
    expect(text).not.toContain("0.58");
  });

  it("renders the caveat inline, not in a tooltip", () => {
    // A reader deciding what to do about a student must meet it while
    // reading, not on hover — and never on a device without hover.
    render(<Drivers drivers={DRIVERS} caveat={CAVEAT} />);

    expect(screen.getByText(/do NOT sum to the risk score/)).toBeVisible();
  });

  it("sizes bars relative to the largest driver", () => {
    const { container } = render(<Drivers drivers={DRIVERS} caveat={CAVEAT} />);
    const widths = [...container.querySelectorAll("rect")].map((node) =>
      Number(node.getAttribute("width")),
    );

    expect(widths).toHaveLength(DRIVERS.length);
    for (let index = 1; index < widths.length; index += 1) {
      expect(widths[index - 1]!).toBeGreaterThan(widths[index]!);
    }
  });

  it("does not divide by zero when every contribution is zero", () => {
    const flat = DRIVERS.map((driver) => ({ ...driver, contribution: 0 }));

    const { container } = render(<Drivers drivers={flat} caveat={CAVEAT} />);

    for (const rect of container.querySelectorAll("rect")) {
      expect(Number(rect.getAttribute("width"))).toBe(0);
    }
  });
});
