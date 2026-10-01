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
  priority_pinned: boolean; // the founder dragged it there: the Product Owner's ranking leaves it
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
  activity?: StoryActivity | null;
  sprint_id?: string | null; // the sprint it is in, else the last one it went through (10.8)
}

/** What a story at work is doing now (server snapshot, then advanced by live events). */
export interface StoryActivity {
  last_event: string;
  last_agent: string;
  last_at: string;
  stalled: boolean;
  task?: number | null;
  task_text?: string | null;
  origin?: string | null;
  calls?: number | null;
  last_tool?: string | null;
  last_target?: string | null;
  thinking?: number | null; // tokens a streamed call has written so far
}

export interface Column { key: string; label: string; stories: StoryCard[] }

export interface Option { key: string; label: string; description: string; recommended: boolean }

export interface Decision { id: string; title: string; context: string; options: Option[]; chosen: string | null }

export interface SprintSummary {
  id: string;
  status: "open" | "running" | "closed" | "cancelled";
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
  sprint_id?: string | null; // a note about a sprint opens its report (8.2)
}

export type ConversationKind = "meeting" | "brainstorm" | "review";

export interface DraftItem {
  key: string; title: string; description: string; epic: string; priority: number;
  in_sprint: boolean; story_id: string | null; origin: string; note: string; unpin?: boolean;
  stage?: string; restart?: boolean; restart_reason?: string; // a card of the running sprint
}

/** What a Sprint Meeting is about (ADR-0018); null until the founder chooses, with a sprint running. */
export type MeetingMode = "current" | "next";

export interface SprintContext {
  running: (SprintSummary & { story_ids: string[] }) | null;
  planned: { id: string; goal: string; story_ids: string[] } | null;
}

/** The Product Owner's reading of a request before it becomes a card (ADR-0017). */
export interface Triage {
  admit: boolean;
  reason_code: "" | "duplicate" | "contradicts" | "vague" | "too_big" | "other";
  reason: string;
  duplicate_of: string;
  title: string;
  description: string;
  kind: string;
  epic: string;
  after: string | null;
  reviewed: boolean;
}

export interface Proposal { keys: string[]; reviewed: boolean; at: string }

export type QuickStoryResult =
  | { status: "created"; id: string; story: StoryCard | null; triage: Triage | null }
  | { status: "refused"; id: null; triage: Triage | null; conversation: Conversation | null };

export interface Turn { who: "founder" | "agent"; name: string; text: string; changes: string[]; at: string }

export interface CommitResult { created: string[]; existing: string[]; held: { key: string; title: string; reason: string }[]; skipped: string[]; sprint_id: string | null; joined?: string[]; withdrawn?: string[]; restarted?: string[] }

export interface Conversation {
  id: string;
  kind: ConversationKind;
  status: "open" | "committed" | "discarded";
  mode: MeetingMode | null;
  title: string;
  turns: Turn[];
  draft: {
    goal: string; items: DraftItem[]; proposal?: Proposal | null; review?: Triage | null;
    sprint_id?: string; members?: string[]; cancel_sprint?: boolean; cancel_reason?: string;
  };
  limits: string[];
  result: Partial<CommitResult>;
}

export interface ConversationSummary { id: string; kind: ConversationKind; mode?: MeetingMode | null; status: string; title: string; turns: number; cards: number; in_sprint: number; updated_at: string }

export interface ChatReply {
  conversation: Conversation;
  sprints?: SprintContext;
  turn?: { reply: string; changes: string[]; ignored: string[]; questions: string[]; failed: boolean } | null;
  report?: { changes: string[]; ignored: string[] };
  result?: CommitResult;
}

export interface Overview {
  factory: { slug: string; name: string; mode: string; language: string; engine: boolean; dry_run: boolean };
  columns: Column[];
  agents: Agent[];
  inbox: Message[];
  finance: { today_usd: number; period: BudgetPeriod; period_usd: number; cap_usd: number; fraction: number; warn: boolean; exhausted: boolean; downgrade: boolean };
  kaizen_today: number;
  sprint: SprintSummary | null;
  next_sprint: { id: string; goal: string; story_ids: string[] } | null; // assembled, waiting (ADR-0018)
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
  models_url: string; needs_key: boolean; recommended: boolean; key: KeyStatus;
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
  quality: number | null;
  price: number;
  free?: boolean;
  /** a `~vendor/x-latest` id: never recommended, offered only in the full catalogue */
  alias?: boolean;
  alias_target?: string;
  context?: number;
  created?: string | null;
  coding?: number | null;
  agentic?: number | null;
  intelligence?: number | null;
  score?: number | null;
  cost_benefit?: number | null;
  /** score of every cluster, so a filter can change which number is shown without a round trip */
  scores?: Partial<Record<ClusterName, number | null>>;
  cost_benefits?: Partial<Record<ClusterName, number | null>>;
  /** only on the full catalogue list: whether the ranking would consider it, and why not */
  eligible?: boolean;
  excluded?: string;
}

export interface ClusterTiers {
  tier1: ModelPick[];
  tier2: ModelPick[];
  tier3: ModelPick[];
}

export interface RoleTaskInfo { key: string; role: string; label: string; hint: string; tier: string }

export type BudgetPeriod = "weekly" | "monthly";
export type OnExceed = "pause" | "tier3";

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
  clusters?: Partial<Record<ClusterName, ClusterTiers>>;
  all_models?: ModelPick[];
  /** false when OpenRouter has no key here: there is no catalogue to read */
  openrouter?: boolean;
  error?: string;
  price_warnings?: string[];
  considered: number;
  eligible: number;
  excluded: Record<string, number>;
  mode: string;
  changed: boolean;
}

export type ClusterName = "strategy" | "engineering" | "routine" | "general";
export type TierName = "tier1" | "tier2" | "tier3";
export type ModelMatrix = Record<string, Record<string, Candidate[]>>;

export interface Settings {
  providers: ProviderInfo[];
  models?: { tier1_ceiling: number; tier2_floor: number; clusters_enabled: boolean };
  tiers: Record<string, Candidate[]>;
  matrix?: ModelMatrix;
  role_clusters?: Record<string, string>;
  roles: Record<string, string>;
  role_tasks?: RoleTaskInfo[];
  budget: { period: BudgetPeriod; cap_usd: number; warn_at_fraction: number; on_exceed: OnExceed };
  schedule: { max_parallel: number };
  tools: { tavily: { enabled: boolean; api_key_env: string; console_url: string; key: KeyStatus } };
  secrets_files: { hub: string; factory: string };
}

export interface SettingsPatch {
  providers?: Record<string, { kind?: string; base_url?: string; api_key_env?: string; label?: string; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
  remove_providers?: string[];
  tiers?: Record<string, Candidate[]>;
  matrix?: ModelMatrix;
  roles?: Record<string, string>;
  role_tasks?: Record<string, string>;
  clusters_enabled?: boolean;
  tier1_ceiling?: number;
  tier2_floor?: number;
  budget?: { period?: BudgetPeriod; cap_usd?: number; warn_at_fraction?: number; on_exceed?: OnExceed };
  max_parallel?: number;
  tools?: Record<string, { enabled?: boolean; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
}

export interface ProbeResult { name: string; ok: boolean; detail: string; model: string; latency_ms: number }

// ---------------------------------------------------------------- sprint report (8.2, 10.8)

export interface SprintTotals {
  stories: number; planned: number; joined: number; delivered: number; cancelled: number; withdrawn: number; waiting: number; working: number;
  duration_s: number; wall_s: number; model_s: number; founder_wait_s: number;
  calls: number; input_tokens: number; output_tokens: number; cost_usd: number; factory_cost_usd: number; cost_per_delivered_usd: number | null;
  retries: number; escalations: number; replans: number; restarts: number; recoveries: number; stalls: number; blocks: number;
  founder_answers: number; deliveries: number; changes_asked: number; review_rounds: number; rework: number;
  tasks_done: number | null; cuts: number | null; // null: the sprint ran before the trace ("não medido")
}

export interface SprintStoryReport {
  id: string; title: string; origin: string; kind: string; complexity: string; planned: boolean; joined_how: string | null;
  stage: string; result: "delivered" | "cancelled" | "withdrawn" | "waiting" | "working";
  started_at: string | null; finished_at: string | null; wall_s: number; model_s: number; stage_s: Record<string, number>; founder_wait_s: number;
  calls: number; input_tokens: number; output_tokens: number; cost_usd: number;
  retries: number; escalations: number; replans: number; restarts: number; recoveries: number; stalls: number;
  blocks: Record<string, number>; founder_answers: number; deliveries: number; changes_asked: number; review_rounds: number; verdicts: string[]; spec_rejections: number;
  tasks: { done: number; runs: number; unfinished: number; seconds: number | null; by_origin: Record<string, number> | null } | null;
  cuts: number | null; unfinished_tasks: number | null;
}

export interface SprintReport {
  sprint: { id: string; goal: string; status: SprintSummary["status"]; created_at: string; started_at: string | null; closed_at: string | null };
  generated_at: string;
  measured: { traced: boolean; reported_cost_calls: number; calls: number };
  totals: SprintTotals;
  stories: SprintStoryReport[];
  unplanned: { joined: { id: string; title: string; how: string }[]; tasks_added: Record<string, number> | null; extra_review_rounds: number; spec_rejections: number; replans: number; restarts: number };
  later: { cards: { id: string; title: string; origin: string; stage: string }[]; findings: Record<string, number>; withdrawn: { id: string; title: string }[]; duplicates: number };
  stage_time: Record<string, number>;
  timeline: { at: string; BACKLOG: number; SPEC: number; DEV: number; TEST: number; AWAITING_FOUNDER: number; DONE: number }[];
  previous: { id: string; goal: string; totals: SprintTotals } | null;
  summary: string; saved: boolean; markdown: string;
}

export interface SprintListItem extends SprintSummary {
  created_at: string; started_at: string | null; closed_at: string | null;
  totals: SprintTotals | null;
}
