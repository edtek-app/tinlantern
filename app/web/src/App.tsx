import { useState } from "react";

import { Cohort } from "./screens/Cohort";
import { Learner } from "./screens/Learner";
import { LearnerList } from "./screens/LearnerList";

// No router. COST, stated because it is the first thing a user will
// ask for: no deep-linking to a learner, so a director cannot send a
// colleague a link to one student. Revisit at sharable links or a
// fourth view.
export function App() {
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <main>
      <header>
        <h1>TinLantern</h1>
        <p>Early-alert learning analytics.</p>
      </header>

      {selected ? (
        <>
          <button type="button" onClick={() => setSelected(null)}>
            ← Back to cohort
          </button>
          <Learner identifier={selected} />
        </>
      ) : (
        <>
          <Cohort />
          <LearnerList onSelect={setSelected} />
        </>
      )}
    </main>
  );
}
