import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearnerList } from "./LearnerList";

function respond(status: number, body: unknown) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

const RANKED = {
  model_version: "abc1234",
  learners: [
    { learner_identifier: "s-00001", risk: 0.91, alerted: true },
    { learner_identifier: "s-00002", risk: 0.42, alerted: true },
    { learner_identifier: "s-00003", risk: 0.11, alerted: false },
  ],
  total: 3,
};

describe("LearnerList", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("lists learners in the order the API ranked them", async () => {
    vi.stubGlobal("fetch", () => respond(200, RANKED));

    render(<LearnerList onSelect={() => {}} />);

    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]!.textContent).toContain("s-00001");
    expect(items[2]!.textContent).toContain("s-00003");
  });

  it("says how many of the cohort it is showing when truncated", async () => {
    // A list that silently truncates is how a director concludes
    // nobody else needs attention.
    vi.stubGlobal("fetch", () => respond(200, { ...RANKED, total: 120 }));

    render(<LearnerList onSelect={() => {}} />);

    expect(
      await screen.findByText(/Showing the 3 highest of 120/),
    ).toBeInTheDocument();
  });

  it("does not claim truncation when it is showing everyone", async () => {
    vi.stubGlobal("fetch", () => respond(200, RANKED));

    render(<LearnerList onSelect={() => {}} />);

    expect(await screen.findByText(/All 3 scored learners/)).toBeInTheDocument();
    expect(screen.queryByText(/Showing the/)).toBeNull();
  });

  it("shows an empty state when nothing is scored", async () => {
    vi.stubGlobal("fetch", () => respond(404, { detail: "run make score" }));

    render(<LearnerList onSelect={() => {}} />);

    expect(await screen.findByText(/No scored cohort yet/)).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });
});
