import type { ChatReply, ConversationKind, FactoryRef, Message, ModelProposalDTO, Overview, ProbeResult, Settings, SettingsPatch } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const api = {
  factories: () => req<{ active: string | null; factories: FactoryRef[]; dry_run: boolean }>("/api/factories"),
  activate: (slug: string) => req(`/api/factories/${slug}/activate`, { method: "POST" }),
  addFactory: (body: { path: string; name?: string; stack?: string; mission?: string; preset?: string; keys?: Record<string, string>; secrets_scope?: "hub" | "factory" }) =>
    req<{ slug: string; mode: string; report: string }>("/api/factories", { method: "POST", body: JSON.stringify(body) }),
  settings: (slug: string) => req<Settings>(`/api/factories/${slug}/settings`),
  updateSettings: (slug: string, patch: SettingsPatch) =>
    req<{ changes: string[]; settings: Settings }>(`/api/factories/${slug}/settings`, { method: "PUT", body: JSON.stringify(patch) }),
  testProvider: (slug: string, name: string, model?: string) =>
    req<ProbeResult>(`/api/factories/${slug}/settings/providers/${encodeURIComponent(name)}/test`, { method: "POST", body: JSON.stringify({ model: model ?? null }) }),
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
  meeting: (slug: string, goals: string, run: boolean) =>
    req<{ stories: { id: string; title: string }[]; clarifications: string[] }>(`/api/factories/${slug}/meeting`, { method: "POST", body: JSON.stringify({ goals, run }) }),
  conversation: (slug: string, id: string) => req<{ conversation: ChatReply["conversation"] }>(`/api/factories/${slug}/conversations/${id}`),
  openConversation: (slug: string, kind: ConversationKind, text: string) =>
    req<ChatReply>(`/api/factories/${slug}/conversations`, { method: "POST", body: JSON.stringify({ kind, text }) }),
  say: (slug: string, id: string, text: string) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  editDraft: (slug: string, id: string, ops: Record<string, unknown>[]) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/draft`, { method: "POST", body: JSON.stringify({ ops }) }),
  commit: (slug: string, id: string, body: { start_sprint?: boolean; goal?: string; run?: boolean }) =>
    req<ChatReply>(`/api/factories/${slug}/conversations/${id}/commit`, { method: "POST", body: JSON.stringify(body) }),
  discard: (slug: string, id: string) => req<ChatReply>(`/api/factories/${slug}/conversations/${id}/discard`, { method: "POST" }),
  startSprint: (slug: string, story_ids: string[] = [], goal = "") =>
    req<{ id: string; story_ids: string[] }>(`/api/factories/${slug}/sprints/start`, { method: "POST", body: JSON.stringify({ story_ids, goal, run: true }) }),
  story: (slug: string, id: string) => req<any>(`/api/factories/${slug}/stories/${id}`),
  createStory: (slug: string, title: string, description: string) =>
    req<{ id: string }>(`/api/factories/${slug}/stories`, { method: "POST", body: JSON.stringify({ title, description }) }),
  promote: (slug: string, id: string) => req(`/api/factories/${slug}/stories/${id}/promote`, { method: "POST" }),
  agent: (slug: string, name: string) => req<any>(`/api/factories/${slug}/agents/${encodeURIComponent(name)}`),
  finance: (slug: string) => req<any>(`/api/factories/${slug}/finance`),
  report: (slug: string) => req<Message>(`/api/factories/${slug}/report`, { method: "POST" }),
  transcribe: async (slug: string, blob: Blob) => {
    const fd = new FormData();
    fd.append("audio", blob, "meeting.webm");
    const r = await fetch(`/api/factories/${slug}/transcribe`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    return (await r.json()) as { text: string };
  },
};
