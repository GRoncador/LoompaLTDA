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
  qa_findings?: { prefix: string; severity: string }[]; // só a forma do achado (11.2)
  tasks_done: number;
  tasks_total: number;
  branch: string;
  pr_url: string | null;
  updated_at: string;
  activity?: StoryActivity | null;
  sprint_id?: string | null; // the sprint it is in, else the last one it went through (10.8)
  depends_on?: string[]; // cards it needs delivered first (ADR-0021)
  // o portão das dependências (ADR-0021 e 11.5): `unmerged` são as que ela já pode usar, porque
  // estão construídas e aprovadas, faltando só o seu aceite; `ready` marca a que está pronta
  merge_queue?: { position: number; total: number } | null; // approved, waiting for its merge (ADR-0029)
  waiting?: { gate: string; pending: string[]; founder: string[]; gone: string[]; chain: string[]; unmerged?: string[]; ready?: boolean; label: string } | null;
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
  detail?: string | null; // what the Loompa says it is doing, in its own words
  model?: string | null; // the model the last call on this story used
  thinking?: number | null; // tokens a streamed call has written so far
  stage?: string | null; // the stage the clock below counts in
  stage_since?: string | null; // when the story entered that stage (a retry in it keeps the clock)
  task_since?: string | null; // when the task in progress began
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
  images?: string[]; // screenshots of the page with a delivery (ADR-0030)
  questions?: Question[]; // answered together (plan 15.4)
  screens?: DesignScreen[]; // a design approval (ADR-0039): paths under .loompa/design/
}

export interface Question { id: string; text: string; why: string; options: Option[] }

/** One state of one screen: its static page and its pictures, as paths under `.loompa/design/`. */
export interface DesignScreen {
  title: string; screen?: string; state: string; alt?: string; route?: string;
  html?: string; desktop?: string; mobile?: string;
}

export interface DesignReference {
  id: string; kind: "image" | "link" | "figma" | "text"; title: string; note: string; url: string;
  story_id: string | null; file: string; created_at: string;
}

export interface DesignRound { round: number; assumptions: string[]; problems: string[]; screens: DesignScreen[] }

export interface DesignOverview {
  base: { tokens: Record<string, unknown>; board: string; desktop?: string; mobile?: string } | null;
  references: DesignReference[];
  pending: { message: Message; story_id: string; title: string; reason: string }[];
  stories: string[];
  gate: string;
  sees: boolean;
}

export interface StoryDesign {
  story_id: string;
  rounds: DesignRound[];
  approved: { round: number; alternative: string; screens: DesignScreen[] } | null;
  references: DesignReference[];
}

export type ConversationKind = "meeting" | "brainstorm" | "review";

export interface DraftItem {
  key: string; title: string; description: string; epic: string; priority: number;
  in_sprint: boolean; story_id: string | null; origin: string; note: string; unpin?: boolean;
  stage?: string; restart?: boolean; restart_reason?: string; // a card of the running sprint
  kind?: string; ideas?: string[]; amend?: string; // a brainstorm's split (ADR-0020)
  depends_on?: string[]; // keys of the cards it needs delivered first (ADR-0021)
}

/** A role's preliminary opinion in a brainstorm (ADR-0020). */
export interface Opinion {
  key: string; role: string; name: string; question: string; summary: string;
  attention: string[]; cost: string; benefit: string; counterpoints: string[]; sources: string[];
  asked_by: "master" | "founder"; failed: boolean; dismissed: boolean; at: string;
}

export interface Consultant { role: string; label: string }

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

export interface GroomProposal {
  ref: string; action: "retire" | "merge" | "split"; reason: string; into: string;
  parts: { title: string; description: string }[]; founder: boolean; held: string;
  accepted: boolean | null;
}

export interface CommitResult { created: string[]; existing: string[]; held: { key: string; title: string; reason: string }[]; skipped: string[]; sprint_id: string | null; joined?: string[]; withdrawn?: string[]; restarted?: string[]; amended?: string[] }

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
    // a brainstorm (ADR-0020): the first OK approves the direction, the second files the split
    direction?: string; ready?: boolean; consults?: Opinion[];
    approved?: { direction: string; ideas: string[]; reviewed: boolean; at: string } | null;
    split?: DraftItem[] | null;
    /** backlog cards that leave (cancelled) when the meeting is saved: id -> reason */
    retire?: Record<string, string>;
    /** ADR-0038: the Product Owner's grooming of the waiting backlog */
    grooming?: GroomProposal[];
    /** plan 10.10: the opening brainstorm of a greenfield factory with no code */
    kickoff?: Kickoff | null;
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
  consultants?: Consultant[]; // a brainstorm: who the founder can ask for an opinion
}

export interface Overview {
  factory: { slug: string; name: string; mode: string; language: string; engine: boolean; dry_run: boolean; kickoff?: boolean;
    engine_state?: EngineState; in_flight?: InFlight[] };
  columns: Column[];
  agents: Agent[];
  inbox: Message[];
  finance: { today_usd: number; period: BudgetPeriod; period_usd: number; cap_usd: number; fraction: number; warn: boolean; exhausted: boolean; downgrade: boolean; story_cap_usd?: number };
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
  /** BYOK: the OpenRouter ids this maker also serves with its own key */
  byok_models?: string[];
}

/** How an OpenRouter slot reaches a model its maker also serves (plan 9.6); "" = factory default. */
export type SlotRoute = "" | "auto" | "openrouter" | "direct";

export interface Candidate {
  provider: string;
  model: string;
  route?: SlotRoute;
  reasoning_effort?: string;
  /** plan 9.4: "fixed" runs every call at reasoning_effort (a slot filled from the AA suggestion) */
  effort_mode?: "" | "auto" | "fixed";
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
  /** plan 9.5: what this factory's Worker measured with the model */
  house?: { tasks: number; done_rate: number | null; enough: boolean; flagged: boolean; cost_per_done: number | null };
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

export type FactoryKind = "personal" | "commercial";
export type DesignGate = "auto" | "full" | "light" | "off";
export interface ImagesSettings { draft: string[]; final: string[]; final_ceiling_usd: number; vector_model: string; per_story: number; logo: "auto" | "on" | "off" }
export interface ImagePick { id: string; name: string; price: number; elo: number; edit_elo: number | null; value: number; references: number }
export interface ImageSuggestion { draft: ImagePick[]; final: ImagePick[]; fetched_at?: number; note: string; attribution: string }

export interface Settings {
  factory?: { name: string; kind: FactoryKind; kinds: { key: FactoryKind; label: string; hint: string }[] };
  design?: { gate: DesignGate; diff_threshold: number; figma_token_env: string; figma_key: { configured: boolean; label?: string }; vision: Candidate[]; images: ImagesSettings };
  providers: ProviderInfo[];
  models?: { tier1_ceiling: number; tier2_floor: number; clusters_enabled: boolean; default_route?: "auto" | "openrouter" };
  tiers: Record<string, Candidate[]>;
  matrix?: ModelMatrix;
  role_clusters?: Record<string, string>;
  roles: Record<string, string>;
  role_tasks?: RoleTaskInfo[];
  budget: { period: BudgetPeriod; cap_usd: number; warn_at_fraction: number; on_exceed: OnExceed; story_cap_usd: number };
  schedule: { max_parallel: number };
  tools: { tavily: ToolInfo; artificial_analysis?: ToolInfo };
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
  default_route?: "auto" | "openrouter";
  budget?: { period?: BudgetPeriod; cap_usd?: number; warn_at_fraction?: number; on_exceed?: OnExceed; story_cap_usd?: number };
  max_parallel?: number;
  tools?: Record<string, { enabled?: boolean; api_key?: string; clear_key?: boolean; scope?: "hub" | "factory" }>;
  factory_kind?: FactoryKind;
  design_gate?: DesignGate;
  vision?: Candidate[];
  figma_key?: string;
  clear_figma_key?: boolean;
  images?: Partial<ImagesSettings>;
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

/** A finding of the factory about itself (Fase 8.3), kept in the hub. */
export interface FactoryFinding {
  signature: string; signal: string; area: string; severity: "high" | "medium" | "low"; title: string; detail: string;
  status: "open" | "resolved" | "vanished"; provisional: number; hypothesis: string; fix: string; resolved_commit: string; resolved_at: string;
  hypothesis_at?: string; hypothesis_window?: string;
  first_seen: string; last_seen: string; seen_in: string; evidence: Record<string, unknown>[]; impact: Record<string, number>; stories: string[];
  factories: string[]; trend: { factory: string; sprint_id: string | null; at: string; seen: boolean }[];
}

export interface FactoryScan { id: number; factory: string; sprint_id: string | null; window_start: string; window_end: string; found: number; at: string }

/** Today's Kaizen findings about the product, once each (8.4). */
export interface ProductFinding { title: string; kind: string; detail: string; story_id: string | null; card: string | null; at: string }

/** Plan 9.1: what one key of the factory can call. */
export interface ProviderModels { name: string; label: string; ok: boolean; models: string[]; detail: string; reason: string; checked_at: number }
export interface ByokCheck { provider: string; openrouter_id: string; own_id: string; found: boolean | null }
export interface AvailableDTO { providers: ProviderModels[]; byok: ByokCheck[] }

export interface StackOption {
  languages: string[]; frameworks: string[]; package_managers: string[]; test_frameworks: string[];
  test_command: string; lint_command: string; summary: string; reason: string;
}
export interface Kickoff { vision: string; out_of_v1: string[]; stacks: StackOption[]; chosen: number | null; applied: boolean }

export interface ToolInfo { enabled: boolean; api_key_env: string; console_url: string; key: KeyStatus }

/** Plan 9.4 (revised 04/10): the suggestion from Artificial Analysis alone. */
export interface AAPick {
  aa_id: string; name: string; creator: string; effort_tag: string; effort: string; score: number;
  coverage: number; price: number | null; value?: number | null; speed?: number | null; ttft?: number | null;
  free_at: string[]; openrouter_id?: string | null;
  house?: { tasks: number; done_rate: number | null; enough: boolean; flagged: boolean } | null;
}
export interface AAProfile {
  profile: string; label: string; weights: Record<string, number>; mix: Record<string, number>;
  tier1: AAPick[]; tier2: AAPick[]; tier3: AAPick[]; floor: number; considered: number; partial: number; unpriced: AAPick[];
}
export interface AASuggestion {
  fetched_at: number | null; entries: number; ceiling: number; floor_share: number; attribution: string;
  price_note: string; profiles: AAProfile[]; key: boolean; stale: boolean;
}

/** One span of a story's trace: a phase, a task, a round of the tool loop, a model or tool call. */
export interface TraceSpan {
  id: string; depth: number; kind: string; name: string; start: string; ms: number;
  status: string; has_detail: boolean; error?: string;
  attrs?: Record<string, unknown>;
  attempts?: { model?: string; outcome?: string; error?: string; output_tokens?: number; cost_usd?: number }[];
}

export type TraceOutline =
  // nothing was written since the version the window sent: the answer is just the version
  | { version: string; unchanged: true }
  | {
      version: string; unchanged: false; spans: TraceSpan[]; truncated: boolean;
      totals: { calls: number; tools: number; cost_usd: number; cuts: number; model_ms: number; tool_ms: number };
    };
export type TraceRun = Extract<TraceOutline, { unchanged: false }>;

export interface TraceMessage { role: string; name?: string | null; content: string; tool_calls: { name: string; arguments: unknown }[] }

export interface TraceDetail {
  id: string; kind: string; name: string; start: string; ms: number; status: string; error?: string | null;
  attrs: Record<string, unknown>;
  messages: TraceMessage[]; response: TraceMessage | null; result: TraceMessage | null;
}

/** stopped · running · pausing (the model calls in flight are finishing) · paused */
export type EngineState = "stopped" | "running" | "pausing" | "paused";
export interface InFlight { story: string; minutes: number; doing: string }
