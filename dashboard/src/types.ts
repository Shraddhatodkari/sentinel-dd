export type Band = "LOW" | "MEDIUM" | "HIGH";

export interface Session {
  access_token: string;
  expires_at: number;
  username: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  tenant: string;
}

export interface CaseSummary {
  case_id: string;
  entity_query: string;
  resolved_name: string | null;
  status: string;
  risk_band: Band | null;
  risk_score: number | null;
  policy_outcome: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error: string | null;
}

export interface EvidenceItem {
  id: string;
  kind: string;
  title: string;
  content: string;
  content_truncated: boolean;
  structured: Record<string, unknown>;
  locator: string;
  source_id: string;
  source_url: string;
  document_id: string;
  retrieved_at: string;
  confidence: number;
}

export interface SignalItem {
  id: number;
  signal_type: string;
  label: string;
  value: number;
  severity: string;
  confidence: number;
  evidence_ids: string[];
  derived_by: string;
  detail: Record<string, unknown>;
}

export interface Factor {
  id: string;
  name: string;
  value: unknown;
  points: number;
  cap: number;
  rules: string[];
  evidence_ids: string[];
  data_available: boolean;
}

export interface Review {
  id: string;
  case_id: string;
  tier: string;
  required_role: string;
  status: string;
  requested_by: string;
  requested_at: string;
  reasons: { rule_id: string; description: string }[];
  decided_by: string | null;
  decided_role: string | null;
  decided_at: string | null;
  comment: string | null;
  entity?: string;
  risk_band?: Band | null;
  risk_score?: number | null;
  can_decide?: boolean;
}

export interface CaseEvent {
  node: string;
  status: string;
  started_at: string;
  duration_ms: number;
  detail: Record<string, unknown>;
  error: string | null;
}

export interface GraphNode {
  id: string;
  kind?: string;
  subject?: boolean;
  sanctioned?: boolean;
  pep?: boolean;
  evidence_ids?: string[];
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  evidence_id?: string;
  basis?: string;
}

export interface CaseDetail extends CaseSummary {
  question: string;
  identifier_hint: Record<string, unknown>;
  report: string;
  report_grounding: number | null;
  attempts: number;
  started_at: string | null;
  summary: {
    coverage?: Record<string, string>;
    resolution?: Record<string, unknown>;
    graph?: Record<string, unknown>;
    details?: Record<string, Record<string, unknown>>;
    peer?: Record<string, unknown>;
    errors?: string[];
  };
  events: CaseEvent[];
  evidence: EvidenceItem[];
  signals: SignalItem[];
  assessment: null | {
    scorecard_version: string;
    score: number;
    band: Band;
    confidence: number;
    factors: Factor[];
    features: Record<string, unknown>;
    model_versions: Record<string, string | null>;
    created_at: string;
  };
  policy: null | {
    policy_version: string;
    outcome: string;
    required_role: string | null;
    rules: { rule_id: string; description: string; outcome: string; evidence_ids: string[] }[];
    created_at: string;
  };
  review: Review | null;
  llm_usage: { calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
  graph: Record<string, unknown> | null;
  graph_ui: { nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean } | null;
  graph_facts: string[] | null;
  research: { question: string; answer: string; citations: string[]; grounding_score: number }[];
  awaiting_human: boolean;
}

export interface SourceStatus {
  source_id: string;
  name: string;
  kind: string;
  authority: string;
  license: string;
  enabled: boolean;
  url: string;
  freshness_sla_h: number | null;
  last_retrieved_at: string | null;
  age_hours: number | null;
  stale: boolean;
  current_records: number;
  last_run: null | { status: string; started_at: string; unchanged: boolean; records_loaded: number; error: string | null };
}

export interface AuditRecord {
  seq: number;
  ts: string;
  tenant: string;
  actor: string;
  actor_roles: string[];
  action: string;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  request_id: string;
  prev_hash: string;
  hash: string;
}
