import type { Bin } from "../api-types";

// Plain SVG. Two charts is below the threshold where a charting library
// earns the supply-chain surface it adds to every deployed bundle, and a
// demo whose dependencies fit in a sentence is worth more than a nicer
// axis. Revisit trigger: a third or fourth chart type.
//
// The bins arrive pre-computed — their middle edge IS the alert
// threshold (app/dashboard/queries.py), so this renders a shape the
// server decided rather than binning anything itself. A frontend
// choosing its own edges would draw a chart that disagreed with the
// alert flag on the same learner.

const WIDTH = 480;
const HEIGHT = 180;
const GAP = 8;

export function Distribution({
  bins,
  threshold,
}: {
  bins: Bin[];
  threshold: number;
}) {
  const tallest = Math.max(...bins.map((bin) => bin.learners), 1);
  const barWidth = (WIDTH - GAP * (bins.length - 1)) / bins.length;

  return (
    <figure>
      <figcaption>Risk distribution</figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Risk distribution across ${bins.length} bands`}
      >
        {bins.map((bin, index) => {
          const height = (bin.learners / tallest) * (HEIGHT - 40);
          const alerted = bin.lower >= threshold;
          return (
            <g key={bin.label} transform={`translate(${index * (barWidth + GAP)}, 0)`}>
              <rect
                y={HEIGHT - 20 - height}
                width={barWidth}
                height={height}
                className={alerted ? "bar bar-alerted" : "bar"}
              />
              <text x={barWidth / 2} y={HEIGHT - 6} textAnchor="middle">
                {bin.label}
              </text>
              <text
                x={barWidth / 2}
                y={HEIGHT - 26 - height}
                textAnchor="middle"
                className="value"
              >
                {bin.learners}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
