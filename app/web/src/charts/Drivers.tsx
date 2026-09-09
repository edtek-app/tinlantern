import type { Driver } from "../api-types";

// Horizontal bars, ABSOLUTE values, and no total anywhere.
//
// Contributions are counterfactual — each is the change in risk if that
// one feature were typical for the cohort — and they neither sum to the
// score nor stay independent of one another. A pie chart or a stacked
// bar would assert the opposite by construction, and so would prose
// like "the top three explain 78%". Neither appears here, and a test
// fails if a percent-of-total ever does.
//
// The bars are ordered by size because that IS the useful ranking. The
// honest limitation, recorded in ADR-0008: a reader may still take the
// longest bar as "most of the risk", and the caveat text beside it is
// the strongest mitigation available. Text is a weak defence; it is
// also the only one.

const WIDTH = 420;
const ROW = 28;

export function Drivers({
  drivers,
  caveat,
}: {
  drivers: Driver[];
  caveat: string;
}) {
  const widest = Math.max(...drivers.map((d) => Math.abs(d.contribution)), 0.01);

  return (
    <figure>
      <figcaption>What is unusual about this learner</figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${drivers.length * ROW + 8}`}
        role="img"
        aria-label={`${drivers.length} risk drivers, largest first`}
      >
        <line
          x1={180}
          x2={180}
          y1={0}
          y2={drivers.length * ROW}
          className="baseline"
        />
        {drivers.map((driver, index) => {
          const length = (Math.abs(driver.contribution) / widest) * (WIDTH - 220);
          return (
            <g key={driver.feature} transform={`translate(0, ${index * ROW})`}>
              <text x={0} y={ROW - 12} className="driver-name">
                {driver.feature}
              </text>
              <rect x={180} y={ROW - 24} width={length} height={14} className="bar" />
              <text x={180 + length + 6} y={ROW - 12} className="value">
                {driver.contribution.toFixed(2)}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="caveat">{caveat}</p>
    </figure>
  );
}
