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
  allow_free_text: boolean;
  created_at: string;
  answer?: { option_key: string | null; text: string | null; answered_at: string } | null;
}

export interface Overview {
  factory: { slug: string; name: string; mode: string; language: string; engine: boolean; dry_run: boolean };
  columns: Column[];
  agents: Agent[];
  inbox: Message[];
  finance: { today_usd: number; month_usd: number; cap_usd: number; fraction: number; warn: boolean; exhausted: boolean };
  kaizen_today: number;
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

export interface Candidate { provider: string; model: string; temperature?: number | null; max_output_tokens?: number | null }

export interface PresetInfo { key: string; label: string; description: string; providers: string[]; optional_providers: string[]; tiers: Record<string, Candidate[]> }

export interface Settings {
  preset: string;
  presets: PresetInfo[];
  providers: ProviderInfo[];
  tiers: Record<string, Candidate[]>;
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
  roles?: Record<string, string>;
  budget?: { monthly_cap_usd?: number; warn_at_fraction?: number; hard_stop?: boolean };
  max_parallel?: number;
  tools?: Record<string, { enabled?: boolean; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
}

export interface ProbeResult { name: string; ok: boolean; detail: string; model: string; latency_ms: number }
