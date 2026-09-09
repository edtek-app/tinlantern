import { useEffect, useState } from "react";

import type { LearnerDetail, LearnerSummary } from "../api-types";
import { api } from "../api";
import { Drivers } from "../charts/Drivers";
import { EmptyState } from "../EmptyState";

// Why a summary is the deterministic template, in words rather than a
// code. `fallback_reason` is an enum for machines; a person deciding
// what to do about a student needs a sentence.
const WHY: Record<string, string> = {
  "verification-failed":
    "generated from the stored figures, because the model's answer could " +
    "not be verified against them",
  "provider-refused":
    "generated from the stored figures, because the model declined to " +
    "write one",
  "provider-unavailable":
    "generated from the stored figures, because the model was unavailable",
};

function Provenance({ summary }: { summary: LearnerSummary }) {
  if (summary.from_model) return null;

  // Visible in the panel, never a tooltip. A template summary and a
  // model summary read alike, which is exactly why the difference has
  // to reach whoever is acting on it.
  const why =
    (summary.fallback_reason && WHY[summary.fallback_reason]) ??
    "generated from the stored figures without the language model";

  return <p className="provenance">This summary was {why}.</p>;
}

export function Learner({ identifier }: { identifier: string }) {
  const [detail, setDetail] = useState<LearnerDetail | null>(null);
  const [summary, setSummary] = useState<LearnerSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([api.learner(identifier), api.summary(identifier)])
      .then(([one, its]) => {
        if (!live) return;
        setDetail(one);
        setSummary(its);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [identifier]);

  if (loading) return <p>Loading…</p>;
  if (!detail) return <EmptyState what="score for this learner" how="make score" />;

  return (
    <section>
      <h2>{detail.learner_identifier}</h2>
      <p>
        Risk <strong>{detail.risk.toFixed(2)}</strong>,{" "}
        {detail.alerted ? "above" : "below"} the{" "}
        {detail.threshold.toFixed(2)} alert threshold, for the window closing{" "}
        {detail.window_close}.
      </p>
      <p className="provenance">
        Model <code>{detail.model_version}</code>.
      </p>

      <Drivers drivers={detail.drivers} caveat={detail.caveat} />

      <h3>Advisor summary</h3>
      {summary ? (
        <>
          <Provenance summary={summary} />
          <p className="summary">{summary.summary}</p>
        </>
      ) : (
        <EmptyState what="summary for this learner" how="make score" />
      )}
    </section>
  );
}
