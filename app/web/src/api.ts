// The API client.
//
// Types come from ./api-types, which is GENERATED from the Pydantic
// response models and drift-tested in CI. Nothing here restates a shape
// the server owns — a hand-copied interface is a convention that drifts,
// and the whole point of the generator is that it cannot.

import type {
  Answer,
  CohortOverview,
  EngagementTrend,
  LearnerDetail,
  LearnerSummary,
} from "./api-types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

// 404 means MISSING, not empty — an unscored cohort is not a cohort of
// zero learners, and an absent summary is not a blank one. Callers get
// `null` and must decide what to render, rather than being handed an
// empty object that looks like data.
async function get<T>(path: string): Promise<T | null> {
  const response = await fetch(path);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response));
  }
  return (await response.json()) as T;
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    // A non-JSON error body is still an error; fall through.
  }
  return response.statusText;
}

export const api = {
  cohort: () => get<CohortOverview>("/api/cohort"),
  engagement: () => get<EngagementTrend>("/api/engagement"),
  learner: (id: string) => get<LearnerDetail>(`/api/learners/${id}`),
  summary: (id: string) => get<LearnerSummary>(`/api/learners/${id}/summary`),

  // A refusal is a 200 with `answered: false` — the system working, not
  // an error. 502 and 503 are faults and throw.
  ask: async (question: string): Promise<Answer> => {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!response.ok) {
      throw new ApiError(response.status, await detailOf(response));
    }
    return (await response.json()) as Answer;
  },
};
