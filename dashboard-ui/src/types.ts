export type AgentState = "IDLE" | "WORKING" | "TESTING" | "BLOCKED";

export interface Agent {
  name: string;
  role: string;
  state: AgentState;
  story_id: string | null;
  model: string;
  detail: string;
  room: "dev" | "meeting" | "qa" | "lounge";
  cost_usd: number;
  updated_at: string;
}

export interface StoryCard {
  id: string;
  title: string;
  epic: string;
  stage: string;
  column: string;
  priority: number;
  origin: string;
  cost_usd: number;
  blocked_reason: string | null;
  blocked_message_id: string | null;
  current_tier: string;
  kind: string;
  complexity: string;
  phase: string;
  route: string[];
  qa_verdict: string | null;
  attempts: { tier2: number; tier1: number };
  tasks_done: number;
  tasks_total: number;
  branch: string;
  pr_url: string | null;
  updated_at: string;
}

export interface Column { key: string; label: string; stories: StoryCard[] }

export interface Option { key: string; label: string; description: string; recommended: boolean }

export interface Decision { id: string; title: string; context: string; options: Option[]; chosen: string | null }

export interface SprintSummary {
  id: string;
  status: "open" | "running" | "closed";
  goal: string;
  story_ids: string[];
  progress: { total: number; done: number; cancelled: number; waiting: number };
}

export interface Message {
  id: string;
  factory: string;
  story_id: string | null;
  kind: "decision" | "blocked" | "delivery" | "info" | "finance" | "kaizen";
  status: "pending" | "answered" | "archived";
  sender: string;
  title: string;
  context: string;
  impact: string;
  options: Option[];
  decisions: Decision[];
  allow_free_text: boolean;
  created_at: string;
  answer?: { option_key: string | null; text: string | null; answered_at: string } | null;
}

export type ConversationKind = "meeting" | "brainstorm";

export interface DraftItem {
  key: string; title: string; description: string; epic: string; priority: number;
  in_sprint: boolean; story_id: string | null; origin: string; note: string;
}

export interface Turn { who: "founder" | "agent"; name: string; text: string; changes: string[]; at: string }

export interface CommitResult { created: string[]; existing: string[]; held: { key: string; title: string; reason: string }[]; skipped: string[]; sprint_id: string | null }

export interface Conversation {
  id: string;
  kind: ConversationKind;
  status: "open" | "committed" | "discarded";
  title: string;
  turns: Turn[];
  draft: { goal: string; items: DraftItem[] };
  limits: string[];
  result: Partial<CommitResult>;
}

export interface ConversationSummary { id: string; kind: ConversationKind; status: string; title: string; turns: number; cards: number; in_sprint: number; updated_at: string }

export interface ChatReply {
  conversation: Conversation;
  turn?: { reply: string; changes: string[]; ignored: string[]; questions: string[]; failed: boolean } | null;
  report?: { changes: string[]; ignored: string[] };
  result?: CommitResult;
}

export interface Overview {
  factory: { slug: string; name: string; mode: string; language: string; engine: boolean; dry_run: boolean };
  columns: Column[];
  agents: Agent[];
  inbox: Message[];
  finance: { today_usd: number; month_usd: number; cap_usd: number; fraction: number; warn: boolean; exhausted: boolean };
  kaizen_today: number;
  sprint: SprintSummary | null;
  conversations: ConversationSummary[];
  last_event_id: number;
}

export interface FactoryRef { slug: string; name: string; path: string; engine: boolean; exists: boolean }

export interface LoompaEvent {
  id?: number;
  type: string;
  factory?: string;
  story_id?: string | null;
  agent?: string;
  payload?: Record<string, unknown>;
}

export interface KeyStatus { env: string; configured: boolean; label: string; source: string | null }

export interface ProviderInfo {
  name: string; label: string; kind: string; base_url: string; api_key_env: string; console_url: string;
  needs_key: boolean; key: KeyStatus; used_by: string[];
}

export interface Candidate {
  provider: string;
  model: string;
  temperature?: number | null;
  max_output_tokens?: number | null;
  score?: number | null;
  quality?: number | null;
  price?: number | null;
  vendor?: string;
}

export interface ModelPick {
  id: string;
  name: string;
  vendor?: string;
  quality: number;
  price: number;
  coding?: number | null;
  agentic?: number | null;
  intelligence?: number | null;
  score?: number | null;
  cost_benefit?: number | null;
}

export interface ClusterTiers {
  tier1: ModelPick[];
  tier2: ModelPick[];
  tier3: ModelPick[];
}

export interface ModelProposalDTO {
  tiers: Record<string, Candidate[]>;
  base: Record<string, Candidate[]>;
  pricing: Record<string, { input: number; output: number; cached_input: number }>;
  added: string[];
  removed: string[];
  gone: string[];
  expiring: string[];
  repriced: string[];
  summary: Record<string, ModelPick[]>;
  clusters?: {
    strategy?: ClusterTiers;
    engineering?: ClusterTiers;
    routine?: ClusterTiers;
  };
  all_models?: ModelPick[];
  considered: number;
  eligible: number;
  excluded: Record<string, number>;
  mode: string;
  changed: boolean;
}

export interface PresetInfo { key: string; label: string; description: string; providers: string[]; optional_providers: string[]; tiers: Record<string, Candidate[]> }

export type ClusterName = "strategy" | "engineering" | "routine";
export type TierName = "tier1" | "tier2" | "tier3";
export type ModelMatrix = Record<string, Record<string, Candidate[]>>;

export interface Settings {
  preset: string;
  presets: PresetInfo[];
  providers: ProviderInfo[];
  models?: { preset: string; tier1_ceiling: number; tier2_floor: number };
  tiers: Record<string, Candidate[]>;
  matrix?: ModelMatrix;
  role_clusters?: Record<string, string>;
  roles: Record<string, string>;
  budget: { monthly_cap_usd: number; warn_at_fraction: number; hard_stop: boolean };
  schedule: { max_parallel: number };
  tools: { tavily: { enabled: boolean; api_key_env: string; console_url: string; key: KeyStatus } };
  secrets_files: { hub: string; factory: string };
}

export interface SettingsPatch {
  preset?: string;
  providers?: Record<string, { kind?: string; base_url?: string; api_key_env?: string; label?: string; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
  remove_providers?: string[];
  tiers?: Record<string, Candidate[]>;
  matrix?: ModelMatrix;
  roles?: Record<string, string>;
  tier1_ceiling?: number;
  tier2_floor?: number;
  budget?: { monthly_cap_usd?: number; warn_at_fraction?: number; hard_stop?: boolean };
  max_parallel?: number;
  tools?: Record<string, { enabled?: boolean; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
}

export interface ProbeResult { name: string; ok: boolean; detail: string; model: string; latency_ms: number }
