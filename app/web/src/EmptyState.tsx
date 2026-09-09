// The shared empty state.
//
// Absence is not zero. The API returns 404 for an unscored cohort, an
// unscored learner and a missing summary precisely so a client cannot
// render "nothing measured" as "measured, and fine" — a distribution of
// four empty bars reads as a healthy cohort.
//
// Shared, so the three screens cannot drift into disagreeing about what
// missing looks like.

export function EmptyState({
  what,
  how,
}: {
  what: string;
  how: string;
}) {
  return (
    <section className="empty">
      <h2>No {what} yet</h2>
      <p>
        This is missing data, not an empty result — nothing has been
        measured, which is different from measuring nothing.
      </p>
      <p>
        Run <code>{how}</code>.
      </p>
    </section>
  );
}
