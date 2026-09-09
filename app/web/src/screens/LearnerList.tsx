import { useEffect, useState } from "react";

import type { LearnerRanking } from "../api-types";
import { api } from "../api";
import { EmptyState } from "../EmptyState";

// The way in. A distribution chart gives a director no identifier to
// drill into, so without this the detail endpoint is unreachable
// except by typing an opaque account id.

export function LearnerList({
  onSelect,
}: {
  onSelect: (identifier: string) => void;
}) {
  const [ranking, setRanking] = useState<LearnerRanking | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api
      .learners()
      .then((result) => {
        if (live) setRanking(result);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  if (loading) return <p>Loading…</p>;
  if (!ranking) return <EmptyState what="scored cohort" how="make score" />;

  const shown = ranking.learners.length;

  return (
    <section>
      <h2>Learners by risk</h2>
      {/* "Top 20 of 120", never a bare list — a list that silently
          truncates is how a director concludes nobody else needs
          attention. */}
      <p>
        {shown < ranking.total
          ? `Showing the ${shown} highest of ${ranking.total} scored learners.`
          : `All ${ranking.total} scored learners.`}
      </p>
      <ol className="learners">
        {ranking.learners.map((learner) => (
          <li key={learner.learner_identifier}>
            <button type="button" onClick={() => onSelect(learner.learner_identifier)}>
              <span>{learner.learner_identifier}</span>
              <span>{learner.risk.toFixed(2)}</span>
              {learner.alerted && <span className="flag">alerted</span>}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
