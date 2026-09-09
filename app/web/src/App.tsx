import { useState } from "react";

import { Cohort } from "./screens/Cohort";

// No router. Three views and a selected learner is a useState, and
// react-router earns its place when URLs must be shareable.
//
// COST, stated: no deep-linking to a learner — a director cannot send a
// colleague a link to one student, which is the first thing they will
// ask for. Revisit triggers: sharable links, or a fourth view.
type View = "cohort";

export function App() {
  const [view] = useState<View>("cohort");

  return (
    <main>
      <header>
        <h1>TinLantern</h1>
        <p>Early-alert learning analytics.</p>
      </header>
      {view === "cohort" && <Cohort />}
    </main>
  );
}
