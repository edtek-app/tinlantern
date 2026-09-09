import { useEffect, useState } from "react";

import type { CohortOverview, EngagementTrend } from "../api-types";
import { api } from "../api";
import { Distribution } from "../charts/Distribution";
import { Trend } from "../charts/Trend";
import { EmptyState } from "../EmptyState";

export function Cohort() {
  const [overview, setOverview] = useState<CohortOverview | null>(null);
  const [trend, setTrend] = useState<EngagementTrend | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    Promise.all([api.cohort(), api.engagement()])
      .then(([cohort, engagement]) => {
        if (!live) return;
        setOverview(cohort);
        setTrend(engagement);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  if (loading) return <p>Loading…</p>;

  // null means the API returned 404: nothing has been scored. Rendering
  // a zeroed chart here would say "the cohort is fine", which is the
  // misreading the 404 exists to prevent.
  if (!overview) {
    return <EmptyState what="scored cohort" how="make score" />;
  }

  const share = Math.round((overview.alerted / overview.learners) * 100);

  return (
    <section>
      <h2>Cohort</h2>
      <p>
        <strong>{overview.alerted}</strong> of{" "}
        <strong>{overview.learners}</strong> learners are above the{" "}
        {overview.threshold.toFixed(2)} alert threshold ({share}%), for the
        window closing {overview.window_close}.
      </p>
      <p className="provenance">
        Model <code>{overview.model_version}</code>.
      </p>

      <Distribution bins={overview.distribution} threshold={overview.threshold} />

      {trend && trend.engagement_trend.length > 0 ? (
        <Trend points={trend.engagement_trend} />
      ) : (
        <EmptyState what="recorded activity" how="make ingest && make etl" />
      )}
    </section>
  );
}
