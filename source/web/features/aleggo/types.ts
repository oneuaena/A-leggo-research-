export type MasteryLevel = "insufficient" | "weak" | "developing" | "mastered";

export interface MasteryState {
  concept_id: string;
  score: number;
  confidence: number;
  level: MasteryLevel;
  evidence_sufficient: boolean;
  assessed_count: number;
  evidence_kinds: string[];
  source_count: number;
  message: string;
}

export interface WeakPoint {
  concept_id: string;
  error_tag: string;
  error_count: number;
  last_error: string;
  mastery: MasteryState;
}

export interface ReviewItem {
  concept_id: string;
  course_id: string;
  due_at: string;
  interval_days: number;
  last_result: string;
}

export interface LearningEvidence {
  id: string;
  concept_id: string;
  course_id: string;
  kind: string;
  correct: boolean | null;
  error_tag: string;
  occurred_at: string;
}

export interface MethodCard {
  id: string;
  problem_type: string;
  key_insight: string;
  common_methods: string[];
  key_steps: string[];
  common_errors: string[];
  next_first_reaction: string;
  status: "candidate" | "confirmed";
  concept_id: string;
}

export interface Companion {
  id: string;
  name: string;
  description: string;
  active: boolean;
}

export interface CompanionMemory {
  id: string;
  companion_id: string;
  kind: string;
  content: string;
  status: "pending" | "confirmed";
}

export interface Style {
  id: string;
  name: string;
  instructions: string;
  active: boolean;
}

export interface IdentityState {
  styles: Style[];
  companions: Companion[];
  memories: CompanionMemory[];
}

export interface RegisteredSkill {
  skill_name: string;
  kind: "prompt" | "workflow" | "tool" | "agent";
  scope: string;
  version: string;
  enabled: boolean;
  entrypoint: string;
  requires: Record<string, unknown>;
  available: boolean;
  missing_dependencies: string[];
}

export interface AleggoOverview {
  product: string;
  version: string;
  due_reviews: ReviewItem[];
  due_review_count: number;
  weak_points: WeakPoint[];
  mastery: MasteryState[];
  recent_evidence: LearningEvidence[];
  method_card_count: number;
  note_count: number;
}

export interface AgentConnectionView {
  app_id: string;
  has_key: boolean;
  masked_key: string;
  base_url: string;
  knowledge_mode: "default" | "specified";
  pipeline_ids: string[];
  enabled: boolean;
  ready: boolean;
}

export interface AgentConnectionPayload {
  app_id: string;
  api_key: string;
  base_url: string;
  knowledge_mode: "default" | "specified";
  pipeline_ids: string[];
  enabled: boolean;
}

export interface AgentProbeResult {
  ok: boolean;
  message: string;
  latency_ms: number;
  request_id?: string;
}

export interface AgentCompletionResult {
  text: string;
  session_id: string;
  request_id: string;
}
