import type { FactoryRef, Message, Overview, ProbeResult, Settings, SettingsPatch } from "./types";

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
  overview: (slug: string) => req<Overview>(`/api/factories/${slug}/overview`),
  engine: (slug: string, action: "start" | "stop") => req<{ engine: boolean }>(`/api/factories/${slug}/engine/${action}`, { method: "POST" }),
  inbox: (slug: string, status = "pending") => req<Message[]>(`/api/factories/${slug}/inbox?status=${status}`),
  reply: (slug: string, id: string, body: { option_key?: string | null; text?: string | null; decisions?: Record<string, string> }) =>
    req<{ stage: string | null }>(`/api/factories/${slug}/inbox/${id}/reply`, { method: "POST", body: JSON.stringify(body) }),
  archive: (slug: string, id: string) => req(`/api/factories/${slug}/inbox/${id}/archive`, { method: "POST" }),
  meeting: (slug: string, goals: string, run: boolean) =>
    req<{ stories: { id: string; title: string }[]; clarifications: string[] }>(`/api/factories/${slug}/meeting`, { method: "POST", body: JSON.stringify({ goals, run }) }),
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
