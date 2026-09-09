// GENERATED FILE — do not edit.
//
// Produced from the Pydantic response models in app/api/schemas.py by
// tools/generate_api_types.py. A test regenerates this and fails if it
// differs, so editing it by hand will not survive the gate.
//
// Regenerate with:  python -m tools.generate_api_types

export interface Bin {
  label: string;
  lower: number;
  upper: number;
  learners: number;
}

export interface CohortOverview {
  model_version: string;
  window_close: string;
  threshold: number;
  learners: number;
  alerted: number;
  distribution: Bin[];
}

export interface EngagementPoint {
  week_start: string;
  active_learners: number;
  events: number;
}

export interface EngagementTrend {
  engagement_trend: EngagementPoint[];
}

export interface RankedLearner {
  learner_identifier: string;
  risk: number;
  alerted: boolean;
}

export interface LearnerRanking {
  model_version: string;
  learners: RankedLearner[];
  total: number;
}

export interface Driver {
  feature: string;
  contribution: number;
}

export interface LearnerDetail {
  learner_identifier: string;
  risk: number;
  alerted: boolean;
  threshold: number;
  window_close: string;
  model_version: string;
  drivers: Driver[];
  caveat: string;
  additive: boolean;
}

export interface LearnerSummary {
  learner_identifier: string;
  window_close: string;
  model_version: string;
  summary: string;
  from_model: boolean;
  fallback_reason: string | null;
  synthetic: boolean;
}

export interface Question {
  question: string;
}

export interface Answer {
  answered: boolean;
  text: string;
  refusal_reason: string | null;
  citations: Record<string, unknown>;
  sql: string | null;
  problems: string[];
}
