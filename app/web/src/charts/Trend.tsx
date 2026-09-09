import type { EngagementPoint } from "../api-types";

// ENGAGEMENT over time. Not risk over time, and the caption says so.
//
// Only one feature window has ever been scored, so a risk line would be
// a single point or a line drawn through repeated scoring runs of the
// same window. The API refuses to serve one; this refuses to imply one.

const WIDTH = 480;
const HEIGHT = 160;

export function Trend({ points }: { points: EngagementPoint[] }) {
  const peak = Math.max(...points.map((point) => point.active_learners), 1);
  const step = points.length > 1 ? WIDTH / (points.length - 1) : 0;

  const path = points
    .map((point, index) => {
      const x = index * step;
      const y = HEIGHT - 20 - (point.active_learners / peak) * (HEIGHT - 40);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <figure>
      <figcaption>
        Active learners per week
        <small> — engagement, not risk</small>
      </figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Weekly active learners across ${points.length} weeks`}
      >
        <path d={path} className="trend" fill="none" />
        {points.map((point, index) => (
          <circle
            key={point.week_start}
            cx={index * step}
            cy={HEIGHT - 20 - (point.active_learners / peak) * (HEIGHT - 40)}
            r={3}
          >
            <title>
              {point.week_start}: {point.active_learners} active
            </title>
          </circle>
        ))}
      </svg>
    </figure>
  );
}
