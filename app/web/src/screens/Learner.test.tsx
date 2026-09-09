import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Learner } from "./Learner";

const DETAIL = {
  learner_identifier: "s-00417",
  risk: 0.82,
  alerted: true,
  threshold: 0.35,
  window_close: "2026-02-23",
  model_version: "abc1234",
  drivers: [
    { feature: "failures", contribution: 0.31 },
    { feature: "mean_score_in_window", contribution: 0.18 },
  ],
  caveat: "They do NOT sum to the risk score and they interact.",
  additive: false,
};

function respond(status: number, body: unknown) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

function serve(summary: unknown, summaryStatus = 200) {
  vi.stubGlobal("fetch", (url: string) =>
    url.endsWith("/summary")
      ? respond(summaryStatus, summary)
      : respond(200, DETAIL),
  );
}

const MODEL_SUMMARY = {
  learner_identifier: "s-00417",
  window_close: "2026-02-23",
  model_version: "abc1234",
  summary: "Risk is 0.82, above the alert threshold.",
  from_model: true,
  fallback_reason: null,
  synthetic: false,
};

describe("Learner", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the score, the threshold it is judged against, and the drivers", async () => {
    serve(MODEL_SUMMARY);

    render(<Learner identifier="s-00417" />);

    expect(await screen.findByText("0.82")).toBeInTheDocument();
    expect(screen.getByText("failures")).toBeInTheDocument();
    expect(screen.getByText(/do NOT sum to the risk score/)).toBeVisible();
  });

  it("says in plain language why a fallback summary is one", async () => {
    // fallback_reason is an enum for machines. Someone deciding what to
    // do about a student needs a sentence, and needs it in the panel
    // rather than on hover.
    serve({
      ...MODEL_SUMMARY,
      from_model: false,
      fallback_reason: "provider-unavailable",
    });

    render(<Learner identifier="s-00417" />);

    expect(
      await screen.findByText(/because the model was unavailable/),
    ).toBeVisible();
    expect(screen.queryByText(/provider-unavailable/)).toBeNull();
  });

  it("says nothing about provenance when the model wrote it", async () => {
    // The marking must mean something. If it appeared on every summary
    // a reader would stop seeing it.
    serve(MODEL_SUMMARY);

    render(<Learner identifier="s-00417" />);

    expect(await screen.findByText(/above the alert threshold/)).toBeInTheDocument();
    expect(screen.queryByText(/This summary was/)).toBeNull();
  });

  it("falls back to a general sentence for an unrecognised reason", async () => {
    // A new reason added server-side must not render as a blank or a
    // raw code while the frontend catches up.
    serve({
      ...MODEL_SUMMARY,
      from_model: false,
      fallback_reason: "something-new",
    });

    render(<Learner identifier="s-00417" />);

    expect(
      await screen.findByText(/without the language model/),
    ).toBeVisible();
  });

  it("reports a missing summary as missing, not as a blank panel", async () => {
    serve({ detail: "run make score" }, 404);

    render(<Learner identifier="s-00417" />);

    expect(
      await screen.findByText(/No summary for this learner yet/),
    ).toBeInTheDocument();
  });
});
