import type { EngagementPoint } from "../api-types";

// ENGAGEMENT over time. Not risk over time, and the caption says so.
//
// Only one feature window has ever been scored, so a risk line would be
// a single point or a line drawn through repeated scoring runs of the
// same window. The API refuses to serve one; this refuses to imply one.

const WIDTH = 480;
const HEIGHT = 170;
const FLOOR = 24;
const TOP = 14;

export function Trend({ points }: { points: EngagementPoint[] }) {
  const peak = Math.max(...points.map((point) => point.active_learners), 1);
  const step = points.length > 1 ? WIDTH / (points.length - 1) : 0;
  const plot = HEIGHT - FLOOR - TOP;
  const yOf = (value: number) => TOP + plot - (value / peak) * plot;

  const path = points
    .map((point, index) => {
      const x = index * step;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${yOf(point.active_learners).toFixed(1)}`;
    })
    .join(" ");

  const first = points[0];
  const last = points[points.length - 1];

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
        {/* Chrome as <line>: a test counts <circle> per point, and the
            baseline anchors the series to a zero a reader can see. */}
        <line x1={0} x2={WIDTH} y1={yOf(peak)} y2={yOf(peak)} className="gridline" />
        <line x1={0} x2={WIDTH} y1={yOf(0)} y2={yOf(0)} className="baseline" />
        <text x={-8} y={yOf(peak) + 3.5} textAnchor="end" className="axis">
          {peak}
        </text>
        <text x={-8} y={yOf(0) + 3.5} textAnchor="end" className="axis">
          0
        </text>

        <path d={path} className="trend" fill="none" />
        {points.map((point, index) => (
          <circle
            key={point.week_start}
            cx={index * step}
            cy={yOf(point.active_learners)}
            r={3}
          >
            <title>
              {point.week_start}: {point.active_learners} active
            </title>
          </circle>
        ))}

        {/* Selective direct labels — the ends, never a number on every
            point. */}
        {first && (
          <text x={0} y={HEIGHT - 8} textAnchor="start" className="axis">
            {first.week_start}
          </text>
        )}
        {last && points.length > 1 && (
          <text x={WIDTH} y={HEIGHT - 8} textAnchor="end" className="axis">
            {last.week_start}
          </text>
        )}
      </svg>
    </figure>
  );
}
