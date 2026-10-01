import type { FactoryFinding, FactoryScan, ProductFinding } from "./types";
import type { ChatReply, SprintListItem, SprintReport, ConversationKind, FactoryRef, Message, ModelProposalDTO, Overview, ProbeResult, QuickStoryResult, Settings, SettingsPatch } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const api = {
  factories: () => req<{ active: string | null; factories: FactoryRef[]; dry_run: boolean }>("/api/factories"),
  activate: (slug: string) => req(`/api/factories/${slug}/activate`, { method: "POST" }),
  addFactory: (body: { path: string; name?: string; stack?: string; mission?: string; keys?: Record<string, string>; secrets_scope?: "hub" | "factory" }) =>
    req<{ slug: string; mode: string; report: string }>("/api/factories", { method: "POST", body: JSON.stringify(body) }),
  settings: (slug: string) => req<Settings>(`/api/factories/${slug}/settings`),
  updateSettings: (slug: string, patch: SettingsPatch) =>
    req<{ changes: string[]; settings: Settings }>(`/api/factories/${slug}/settings`, { method: "PUT", body: JSON.stringify(patch) }),
  testProvider: (slug: string, name: string, model?: string) =>
    req<ProbeResult>(`/api/factories/${slug}/settings/providers/${encodeURIComponent(name)}/test`, { method: "POST", body: JSON.stringify({ model: model ?? null }) }),
  /** One minimal call on one cell of the matrix: does this model really answer on this provider? */
  testModel: (slug: string, provider: string, model: string) =>
    req<ProbeResult>(`/api/factories/${slug}/models/test`, { method: "POST", body: JSON.stringify({ provider, model }) }),
  modelsCatalog: (slug: string) => req<ModelProposalDTO>(`/api/factories/${slug}/models/catalog`),
  previewModelSync: (slug: string, body?: { tier1_ceiling?: number; tier2_floor?: number; force_refresh?: boolean }) =>
    req<ModelProposalDTO>(`/api/factories/${slug}/models/preview-sync`, { method: "POST", body: JSON.stringify(body || {}) }),
  applyModelSync: (slug: string, toInbox: boolean = false, tier1Ceiling?: number) =>
    req<{ applied: boolean; to_inbox: boolean; message?: string; message_id?: string; added?: string[]; removed?: string[]; settings?: Settings }>(
      `/api/factories/${slug}/models/apply-sync`,
      { method: "POST", body: JSON.stringify({ to_inbox: toInbox, tier1_ceiling: tier1Ceiling }) }
    ),
  overview: (slug: string) => req<Overview>(`/api/factories/${slug}/overview`),
  engine: (slug: string, action: "start" | "stop") => req<{ engine: boolean }>(`/api/factories/${slug}/engine/${action}`, { method: "POST" }),
  inbox: (slug: string, status = "pending") => req<Message[]>(`/api/factories/${slug}/inbox?status=${status}`),
  reply: (slug: string, id: string, body: { option_key?: string | null; text?: string | null; decisions?: Record<string, string> }) =>
    req<{ stage: string | null }>(`/api/factories/${slug}/inbox/${id}/reply`, { method: "POST", body: JSON.stringify(body) }),
  archive: (slug: string, id: string) => req(`/api/factories/${slug}/inbox/${id}/archive`, { method: "POST" }),
  meeting: (slug: string, goals: string) =>
    req<{ stories: { id: string; title: string }[]; clarifications: string[] }>(`/api/factories/${slug}/meeting`, { method: "POST", body: JSON.stringify({ goals }) }),
  conversation: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}`),
  openConversation: (slug: string, kind: ConversationKind, text: string) =>
    req<ChatReply>(`/api/factories/${slug}/conversations`, { method: "POST", body: JSON.stringify({ kind, text }) }),
  say: (slug: string, id: string, text: string) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  editDraft: (slug: string, id: string, ops: Record<string, unknown>[], split = false) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/draft`, { method: "POST", body: JSON.stringify({ ops, split }) }),
  /** A brainstorm (ADR-0020): ask a role for a preliminary opinion; approve the direction (1st OK); go back. */
  consult: (slug: string, id: string, role: string, question: string) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/consult`, { method: "POST", body: JSON.stringify({ role, question }) }),
  approve: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}/approve`, { method: "POST" }),
  reopen: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}/reopen`, { method: "POST" }),
  commit: (slug: string, id: string, body: { start_sprint?: boolean; plan_next?: boolean; goal?: string; run?: boolean; force?: boolean }) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/commit`, { method: "POST", body: JSON.stringify(body) }),
  /** With a sprint running: adjust it ("current") or pre-assemble the next ("next"), ADR-0018. */
  chooseMode: (slug: string, id: string, mode: "current" | "next") =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/mode`, { method: "POST", body: JSON.stringify({ mode }) }),
  /** The Product Owner's sprint proposal: "Começar Sprint" waits for one (ADR-0017). */
  propose: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}/propose`, { method: "POST" }),
  discard: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}/discard`, { method: "POST" }),
  story: (slug: string, id: string) => req<any>(`/api/factories/${slug}/stories/${id}`),
  /** The factory's self-diagnosis (8.3/8.4): findings live in the hub, for every factory. */
  factoryHealth: (status: "open" | "all" = "open") => req<{ findings: FactoryFinding[]; scans: FactoryScan[] }>(`/api/factory-health?status=${status}`),
  resolveFinding: (signature: string, commit: string) =>
    req<{ status: string }>(`/api/factory-health/${encodeURIComponent(signature)}/resolve`, { method: "POST", body: JSON.stringify({ commit }) }),
  reopenFinding: (signature: string) => req<{ status: string }>(`/api/factory-health/${encodeURIComponent(signature)}/reopen`, { method: "POST" }),
  scanFactory: (slug: string, sprint_id: string | null = null) =>
    req<{ found: number; new: string[]; back: string[]; confirmed: string[] }>(`/api/factories/${slug}/factory-health/scan`, { method: "POST", body: JSON.stringify({ sprint_id }) }),
  productFindings: (slug: string) => req<ProductFinding[]>(`/api/factories/${slug}/product-findings`),
  /** A quick story: the Product Owner files it or opens a review conversation (ADR-0017). */
  createStory: (slug: string, title: string, description: string) =>
    req<QuickStoryResult>(`/api/factories/${slug}/stories`, { method: "POST", body: JSON.stringify({ title, description }) }),
  unpin: (slug: string, id: string) => req(`/api/factories/${slug}/stories/${id}/unpin`, { method: "POST" }),
  storyDiff: (slug: string, id: string) =>
    req<{ source: "branch" | "merged" | "none"; ref: string; stat: string; diff: string }>(`/api/factories/${slug}/stories/${id}/diff`),
  /** The founder's drag-and-drop order for the backlog; applied by the Product Owner. The
   *  dragged card stays pinned where it was dropped. */
  reorderBacklog: (slug: string, story_ids: string[], dragged: string | null) =>
    req<{ order: string[] }>(`/api/factories/${slug}/backlog/order`, { method: "POST", body: JSON.stringify({ story_ids, dragged }) }),
  agent: (slug: string, name: string) => req<any>(`/api/factories/${slug}/agents/${encodeURIComponent(name)}`),
  finance: (slug: string) => req<any>(`/api/factories/${slug}/finance`),
  sprints: (slug: string) => req<SprintListItem[]>(`/api/factories/${slug}/sprints`),
  /** The sprint's report (8.2): saved once it ended, measured as of now while it runs. */
  sprintReport: (slug: string, id: string) => req<SprintReport>(`/api/factories/${slug}/sprints/${id}/report`),
  report: (slug: string) => req<Message>(`/api/factories/${slug}/report`, { method: "POST" }),
  transcribe: async (slug: string, blob: Blob) => {
    const fd = new FormData();
    fd.append("audio", blob, "meeting.webm");
    const r = await fetch(`/api/factories/${slug}/transcribe`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    return (await r.json()) as { text: string };
  },
};
