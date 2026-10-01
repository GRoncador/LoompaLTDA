import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { useSocket } from "./useSocket";
import type { ConversationKind, FactoryRef, LoompaEvent, Message, Overview, QuickStoryResult, StoryActivity } from "./types";
import Header from "./components/Header";
import Office from "./components/Office";
import Inbox from "./components/Inbox";
import Kanban from "./components/Kanban";
import ChatModal from "./components/ChatModal";
import AgentDrawer from "./components/AgentDrawer";
import StoryDrawer from "./components/StoryDrawer";
import NewFactoryModal from "./components/NewFactoryModal";
import EventTicker from "./components/EventTicker";
import SettingsModal from "./components/SettingsModal";
import FinanceModal from "./components/FinanceModal";
import SprintsModal from "./components/SprintsModal";

export default function App() {
  const [factories, setFactories] = useState<FactoryRef[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [events, setEvents] = useState<LoompaEvent[]>([]);
  const [chat, setChat] = useState<{ kind: ConversationKind; resumeId?: string; text?: string } | null>(null);
  const [newFactoryOpen, setNewFactoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [financeOpen, setFinanceOpen] = useState(false);
  // the Sprints tab (10.8): open, and on which sprint ("" = the current one)
  const [sprintsAt, setSprintsAt] = useState<string | null>(null);
  const [agentName, setAgentName] = useState<string | null>(null);
  const [storyId, setStoryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // live activity per story, advanced by events between overview refreshes (Fase 8.5)
  const [live, setLive] = useState<Record<string, StoryActivity>>({});
  const [now, setNow] = useState(() => Date.now());
  const overviewRef = useRef<Overview | null>(null);
  overviewRef.current = overview;

  const loadFactories = useCallback(async () => {
    try {
      const r = await api.factories();
      setFactories(r.factories);
      setSlug((s) => s ?? r.active ?? r.factories[0]?.slug ?? null);
    } catch (e) { setError(String(e)); }
  }, []);

  const refresh = useCallback(async () => {
    if (!slug) return;
    try { setOverview(await api.overview(slug)); setError(null); } catch (e) { setError(String(e)); }
  }, [slug]);

  useEffect(() => { loadFactories(); }, [loadFactories]);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { const t = setInterval(refresh, 15000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);
  useEffect(() => { setLive({}); }, [slug]);

  const onEvent = useCallback((e: LoompaEvent) => {
    setEvents((prev) => [...prev.slice(-199), e]);
    const sid = e.story_id;
    if (sid && e.type !== "scheduler.dispatch") {
      setLive((prev) => ({ ...prev, [sid]: advance(prev[sid] ?? snapshot(overviewRef.current, sid), e) }));
      setNow(Date.now());
    }
    if (["story.stage", "story.created", "inbox.new", "inbox.answered", "agent.state", "llm.call", "story.merged", "engine.started", "engine.stopped", "kaizen.learning", "story.promoted", "scheduler.paused", "settings.updated", "sprint.started", "sprint.done", "sprint.report", "finding.decided", "inbox.decided", "conversation.opened", "conversation.turn", "conversation.committed", "conversation.discarded", "backlog.status", "backlog.priority", "backlog.pinned", "backlog.reranked", "sprint.proposed", "sprint.planned", "sprint.adjusted", "sprint.cancelled", "meeting.mode", "story.withdrawn", "story.restarted", "story.stalled"].includes(e.type)) {
      refresh();
    }
  }, [refresh]);
  const connected = useSocket(slug, onEvent);

  const switchFactory = async (s: string) => { setSlug(s); await api.activate(s).catch(() => {}); };
  const toggleEngine = async () => {
    if (!slug || !overview) return;
    await api.engine(slug, overview.factory.engine ? "stop" : "start");
    refresh();
  };
  const reply = async (m: Message, option_key: string | null, text: string | null, decisions: Record<string, string> = {}) => {
    if (!slug) return;
    await api.reply(slug, m.id, { option_key, text, decisions });
    refresh();
  };
  // the quick story: the Product Owner files it, or opens a review chat explaining why not
  const createStory = async (title: string): Promise<QuickStoryResult | null> => {
    if (!slug) return null;
    try {
      const r = await api.createStory(slug, title, "");
      if (r.status === "refused" && r.conversation) setChat({ kind: "review", resumeId: r.conversation.id });
      setError(null);
      return r;
    } catch (e) { setError(String(e)); return null; } finally { refresh(); }
  };

  const pendingCount = useMemo(() => overview?.inbox.filter((m) => m.kind === "decision" || m.kind === "blocked" || m.kind === "delivery").length ?? 0, [overview]);

  return (
    <div className="flex h-screen flex-col">
      <Header
        factories={factories} slug={slug} overview={overview} connected={connected} pending={pendingCount}
        onSwitch={switchFactory} onNewFactory={() => setNewFactoryOpen(true)} onToggleEngine={toggleEngine} onSettings={() => setSettingsOpen(true)} onFinance={() => setFinanceOpen(true)} onSprints={() => setSprintsAt("")}
      />
      {error && <div className="bg-red-900/60 px-4 py-2 text-sm text-red-100">{error}</div>}
      <main className="grid flex-1 grid-cols-1 gap-3 overflow-hidden p-3 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
        <section className="card flex min-h-[320px] flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <h2 className="text-sm font-semibold">🏢 Escritório Virtual</h2>
            <span className="text-xs text-slate-400">clique em um Loompa para telemetria e custos</span>
          </div>
          <Office agents={overview?.agents ?? []} onSelect={setAgentName} />
        </section>
        <section className="card flex min-h-[320px] flex-col overflow-hidden">
          <Inbox messages={overview?.inbox ?? []} finance={overview?.finance ?? null} kaizen={overview?.kaizen_today ?? 0} onReply={reply} onArchive={async (m) => { if (slug) { await api.archive(slug, m.id); refresh(); } }} onOpenStory={setStoryId} onOpenSprint={setSprintsAt} />
        </section>
        <section className="card flex min-h-[260px] flex-col overflow-hidden lg:col-span-2">
          <Kanban
            onOpenSprint={setSprintsAt}
            columns={overview?.columns ?? []} sprint={overview?.sprint ?? null} nextSprint={overview?.next_sprint ?? null} conversations={overview?.conversations ?? []} live={live} now={now}
            onChat={(kind, resumeId) => setChat({ kind, resumeId })} onOpen={setStoryId}
            onCreate={createStory}
            onReorder={async (ids, dragged) => { if (slug) { try { await api.reorderBacklog(slug, ids, dragged); } catch (e) { setError(String(e)); } refresh(); } }}
            onUnpin={async (id) => { if (slug) { try { await api.unpin(slug, id); } catch (e) { setError(String(e)); } refresh(); } }}
          />
        </section>
      </main>
      <EventTicker events={events} />
      {chat && slug && <ChatModal key={chat.resumeId ?? chat.kind} slug={slug} kind={chat.kind} resumeId={chat.resumeId} initialText={chat.text} onBrainstorm={(text) => setChat({ kind: "brainstorm", text })} onClose={() => { setChat(null); refresh(); }} />}
      {financeOpen && slug && <FinanceModal slug={slug} onClose={() => setFinanceOpen(false)} />}
      {sprintsAt !== null && slug && <SprintsModal slug={slug} initial={sprintsAt || null} onClose={() => setSprintsAt(null)} onOpenStory={setStoryId} />}
      {settingsOpen && slug && <SettingsModal slug={slug} onClose={() => { setSettingsOpen(false); refresh(); }} />}
      {newFactoryOpen && <NewFactoryModal onClose={async (created) => { setNewFactoryOpen(false); await loadFactories(); if (created) setSlug(created); }} />}
      {agentName && slug && <AgentDrawer slug={slug} name={agentName} onClose={() => setAgentName(null)} onOpenStory={setStoryId} />}
      {storyId && slug && <StoryDrawer slug={slug} id={storyId} onClose={() => setStoryId(null)} onOpenSprint={(id) => { setStoryId(null); setSprintsAt(id); }} />}
    </div>
  );
}

function snapshot(ov: Overview | null, sid: string): StoryActivity | undefined {
  for (const c of ov?.columns ?? []) {
    const s = c.stories.find((x) => x.id === sid);
    if (s) return s.activity ?? undefined;
  }
  return undefined;
}

/** One event moves a story's live line: a new task resets the count, a tool call adds one. */
function advance(base: StoryActivity | undefined, e: LoompaEvent): StoryActivity {
  const p = e.payload ?? {};
  if (e.type === "story.stalled") return { ...(base ?? { last_event: e.type, last_agent: "", last_at: new Date().toISOString() }), stalled: true };
  const next: StoryActivity = { ...(base ?? {}), last_event: e.type, last_agent: e.agent ?? "", last_at: new Date().toISOString(), stalled: false };
  if (e.type === "worker.task_started") {
    Object.assign(next, { task: Number(p.task), task_text: String(p.text ?? ""), origin: String(p.origin ?? ""), calls: 0, last_tool: null, last_target: null });
  } else if (e.type === "worker.task_finished") {
    Object.assign(next, { task: null, task_text: null, calls: null, last_tool: null, last_target: null });
  } else if (e.type === "tool.call") {
    Object.assign(next, { calls: (next.calls ?? 0) + 1, last_tool: String(p.tool ?? ""), last_target: String(p.path ?? p.query ?? "") || null });
  }
  next.thinking = e.type === "llm.progress" ? Number(p.tokens ?? 0) : null;
  return next;
}
