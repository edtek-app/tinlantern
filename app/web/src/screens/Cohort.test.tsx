import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cohort } from "./Cohort";

const OVERVIEW = {
  model_version: "abc1234",
  window_close: "2026-02-23",
  threshold: 0.35,
  learners: 120,
  alerted: 38,
  distribution: [
    { label: "low", lower: 0, upper: 0.175, learners: 40 },
    { label: "approaching", lower: 0.175, upper: 0.35, learners: 42 },
    { label: "alerted", lower: 0.35, upper: 0.675, learners: 30 },
    { label: "high", lower: 0.675, upper: 1, learners: 8 },
  ],
};

function respond(status: number, body: unknown) {
  return Promise.resolve({
    ok: status < 400,
    status,
    statusText: "",
    json: () => Promise.resolve(body),
  } as Response);
}

describe("Cohort", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reports how many learners alerted, out of how many", async () => {
    vi.stubGlobal("fetch", (url: string) =>
      url.includes("engagement")
        ? respond(200, { engagement_trend: [] })
        : respond(200, OVERVIEW),
    );

    render(<Cohort />);

    expect(await screen.findByText("38")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
  });

  it("shows an empty state, never a zeroed chart, when nothing is scored", async () => {
    // The whole point of the API's 404. Four empty bars would read as
    // "the cohort is fine" when nothing has been measured at all.
    vi.stubGlobal("fetch", () => respond(404, { detail: "run make score" }));

    render(<Cohort />);

    expect(await screen.findByText(/No scored cohort yet/)).toBeInTheDocument();
    expect(screen.getByText("make score")).toBeInTheDocument();
    expect(document.querySelectorAll("rect")).toHaveLength(0);
  });

  it("does not render a trend when there is no activity", async () => {
    vi.stubGlobal("fetch", (url: string) =>
      url.includes("engagement")
        ? respond(200, { engagement_trend: [] })
        : respond(200, OVERVIEW),
    );

    render(<Cohort />);

    await waitFor(() =>
      expect(screen.getByText(/No recorded activity yet/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Active learners per week/)).toBeNull();
  });
});
