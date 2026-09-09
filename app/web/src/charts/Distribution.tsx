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
const HEIGHT = 190;
const GAP = 8;
const FLOOR = 26; // room for the band labels beneath the baseline
const TOP = 22; // room for the value above the tallest bar

// Gridlines are <line>, not <rect>. A test counts one <rect> per bin and
// asserts every height is zero on an empty cohort; drawing chrome as
// rects would break both, and <line> is the correct SVG anyway.
const GRID_STEPS = 4;

export function Distribution({
  bins,
  threshold,
}: {
  bins: Bin[];
  threshold: number;
}) {
  const tallest = Math.max(...bins.map((bin) => bin.learners), 1);
  const barWidth = (WIDTH - GAP * (bins.length - 1)) / bins.length;
  const plot = HEIGHT - FLOOR - TOP;
  const gridlines = Array.from({ length: GRID_STEPS + 1 }, (_, index) => ({
    value: Math.round((tallest / GRID_STEPS) * index),
    y: TOP + plot - (plot / GRID_STEPS) * index,
  }));

  return (
    <figure>
      <figcaption>
        Risk distribution <small>— learners per band</small>
      </figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Risk distribution across ${bins.length} bands`}
      >
        {/* Gridlines carry the comparison; the numbers do not. Every
            bar is directly labelled with its count, so a y-axis scale
            would print each figure twice — and on a four-bar chart the
            duplicate reads as two different quantities. Caught by a
            test finding "42" twice. */}
        {gridlines.map((line) => (
          <line
            key={line.y}
            x1={0}
            x2={WIDTH}
            y1={line.y}
            y2={line.y}
            className={line.value === 0 ? "baseline" : "gridline"}
          />
        ))}

        {bins.map((bin, index) => {
          const height = (bin.learners / tallest) * plot;
          const alerted = bin.lower >= threshold;
          return (
            <g key={bin.label} transform={`translate(${index * (barWidth + GAP)}, 0)`}>
              <rect
                y={TOP + plot - height}
                width={barWidth}
                height={height}
                className={alerted ? "bar bar-alerted" : "bar"}
              />
              <text x={barWidth / 2} y={HEIGHT - 10} textAnchor="middle">
                {bin.label}
              </text>
              <text
                x={barWidth / 2}
                y={TOP + plot - height - 7}
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
