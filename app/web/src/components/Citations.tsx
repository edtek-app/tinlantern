import type { Answer } from "../api-types";

// The cited rows and the executed SQL, verbatim.
//
// NOT a synthesized "sources" list. Column shape varies between runs —
// ADR-0008 measured nine of twelve golden questions producing a
// different statement across five runs while their answers stayed
// identical — so a panel built around an expected column set would
// flicker for questions whose answer never changed, and the cause would
// look like the model.
//
// Rendered on refusals too, whenever a query actually ran. "Here is
// what I ran and why I didn't trust it" is a more useful artifact than
// a bare refusal, and the busier panel is the right trade.

// `citations` is typed Record<string, unknown> because a cited row's
// columns are chosen per run (ADR-0008), and the type generator emits
// `unknown` rather than `any` precisely so a consumer has to narrow
// here instead of skipping the check. This is that narrowing.
function asRow(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function Row({ label, row }: { label: string; row: Record<string, unknown> }) {
  const keys = Object.keys(row);
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>
        {keys.map((key) => (
          <span key={key} className="cell">
            <b>{key}</b>: {String(row[key])}
          </span>
        ))}
      </td>
    </tr>
  );
}

export function Citations({ answer }: { answer: Answer }) {
  const labels = Object.keys(answer.citations);
  if (!answer.sql && labels.length === 0) return null;

  return (
    <details className="citations" open>
      <summary>How this was answered</summary>

      {answer.sql && (
        <>
          <h4>Query run</h4>
          <pre>
            <code>{answer.sql}</code>
          </pre>
        </>
      )}

      {labels.length > 0 && (
        <>
          <h4>Rows cited</h4>
          <table>
            <tbody>
              {labels.map((label) => (
                <Row key={label} label={label} row={asRow(answer.citations[label])} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </details>
  );
}
