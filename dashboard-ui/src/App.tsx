import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, BackendOfflineError } from "./api";
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
import FactoryModal from "./components/FactoryModal";
import DesignModal from "./components/DesignModal";
import ProductModal from "./components/ProductModal";
import GrowthModal from "./components/GrowthModal";
import { Icon } from "./components/Icon";

export default function App() {
  const [factories, setFactories] = useState<FactoryRef[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [events, setEvents] = useState<LoompaEvent[]>([]);
  const [ready, setReady] = useState<{ id: string; kind?: ConversationKind; failed: boolean } | null>(null);
  const [chat, setChat] = useState<{ kind: ConversationKind; resumeId?: string; text?: string } | null>(null);
  const [newFactoryOpen, setNewFactoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [financeOpen, setFinanceOpen] = useState(false);
  const [factoryOpen, setFactoryOpen] = useState(false); // the factory's self-diagnosis (8.4)
  const [design, setDesign] = useState<{ story?: string | null } | null>(null); // the design gate (ADR-0039)
  const [productOpen2, setProductOpen2] = useState(false); // phases and epics (plan 16)
  const [growthOpen, setGrowthOpen] = useState(false); // marketing drafts (plan 17)
  // the Sprints tab (10.8): open, and on which sprint ("" = the current one)
  const [sprintsAt, setSprintsAt] = useState<string | null>(null);
  const [agentName, setAgentName] = useState<string | null>(null);
  const [storyId, setStoryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  // a request that never got an answer means the backend is gone; anything else is shown as-is
  const fail = (e: unknown) => {
    if (e instanceof BackendOfflineError) setOffline(true);
    else setError(String(e));
  };
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
    } catch (e) { fail(e); }
  }, []);

  const refresh = useCallback(async () => {
    if (!slug) return;
    try { setOverview(await api.overview(slug)); setError(null); setOffline(false); } catch (e) { fail(e); }
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
    if ((e.type === "meeting.proposal_ready" || e.type === "conversation.ready") && e.payload?.conversation_id) {
      const id = String(e.payload.conversation_id);
      const kind = (e.payload.kind as ConversationKind) ?? "meeting";
      setReady({ id, kind, failed: !!e.payload.failed });
    }
    if (["meeting.proposal_ready", "conversation.ready", "story.stage", "story.created", "inbox.new", "inbox.answered", "agent.state", "llm.call", "story.merged", "engine.started", "engine.stopped", "engine.pausing", "engine.paused", "engine.resumed", "story.paused", "scheduler.dispatch", "kaizen.learning", "story.promoted", "scheduler.paused", "settings.updated", "sprint.started", "sprint.done", "sprint.report", "finding.decided", "inbox.decided", "conversation.opened", "conversation.turn", "conversation.committed", "conversation.discarded", "backlog.status", "backlog.priority", "backlog.pinned", "backlog.reranked", "sprint.proposed", "sprint.planned", "sprint.adjusted", "sprint.cancelled", "meeting.mode", "story.withdrawn", "story.restarted", "story.stalled"].includes(e.type)) {
      refresh();
    }
  }, [refresh]);
  const connected = useSocket(slug, onEvent);

  const switchFactory = async (s: string) => { setSlug(s); await api.activate(s).catch(() => {}); };
  const engineAction = async (action: "start" | "pause" | "stop") => {
    if (!slug) return;
    await api.engine(slug, action);
    refresh();
  };
  const reply = async (m: Message, option_key: string | null, text: string | null, decisions: Record<string, string> = {}, answers: Record<string, string> = {}) => {
    if (!slug) return;
    await api.reply(slug, m.id, { option_key, text, decisions, answers });
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
    } catch (e) { fail(e); return null; } finally { refresh(); }
  };

  const resumeConversation = useCallback((id: string) => {
    const found = overview?.conversations.find((c) => c.id === id);
    const kind = found?.kind ?? "meeting";
    setChat({ kind, resumeId: id });
  }, [overview]);


  return (
    <div className="flex h-screen flex-col">
      {offline && (
        <div role="alert" className="flex items-center justify-center gap-2 bg-red-900/60 px-4 py-2 text-center text-sm text-red-100">
          <Icon name="radio" className="h-4 w-4 shrink-0" />
          <span>Sem conexão com o terminal. Tentando reconectar…</span>
        </div>
      )}
      <Header
        factories={factories} slug={slug} overview={overview} connected={connected}
        onSwitch={switchFactory} onNewFactory={() => setNewFactoryOpen(true)} onEngine={engineAction} onSettings={() => setSettingsOpen(true)} onFinance={() => setFinanceOpen(true)} onSprints={() => setSprintsAt("")} onFactory={() => setFactoryOpen(true)} onDesign={() => setDesign({})} onGrowth={overview?.factory.kind === "commercial" ? () => setGrowthOpen(true) : undefined}
        designPending={(overview?.inbox ?? []).filter((m) => (m.screens?.length ?? 0) > 0 || (m.questions?.length ?? 0) > 0).length}
      />
      {ready && (
        <div className="flex items-center gap-3 bg-sky-900/60 px-4 py-2 text-sm text-sky-100">
          <span>
            {ready.kind === "brainstorm"
              ? (ready.failed ? "O Master Loompa não conseguiu concluir a reflexão do brainstorm." : "O brainstorm está pronto para conferir ideias e pareceres.")
              : (ready.failed ? "O Product Owner não conseguiu montar a sugestão do próximo sprint." : "A sugestão do Product Owner para o próximo sprint está pronta.")}
          </span>
          <button className="btn-primary px-2 py-0.5 text-xs" onClick={() => { resumeConversation(ready.id); setReady(null); }}>
            {ready.kind === "brainstorm" ? <><Icon name="lightbulb" /> Abrir o brainstorm</> : <><Icon name="calendar" /> Abrir a reunião</>}
          </button>
          <button className="ml-auto text-xs text-sky-300 hover:text-white" onClick={() => setReady(null)}><Icon name="x" /></button>
        </div>
      )}
      {error && <div className="bg-red-900/60 px-4 py-2 text-sm text-red-100">{error}</div>}
      <main className="grid flex-1 grid-cols-1 gap-3 overflow-hidden p-3 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
        <section className="card flex min-h-[320px] flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold"><Icon name="building" className="h-4 w-4 text-slate-400" />Escritório</h2>
          </div>
          <Office agents={overview?.agents ?? []} onSelect={setAgentName} />
        </section>
        <section className="card flex min-h-[320px] flex-col overflow-hidden">
          <Inbox messages={overview?.inbox ?? []} onReply={reply} onArchive={async (m) => { if (slug) { await api.archive(slug, m.id); refresh(); } }} onOpenStory={setStoryId} onOpenSprint={setSprintsAt} onOpenMeeting={resumeConversation} />
        </section>
        <section className="card flex min-h-[260px] flex-col overflow-hidden lg:col-span-2">
          <Kanban
            onOpenSprint={setSprintsAt}
            columns={overview?.columns ?? []} agents={overview?.factory.engine ? overview.agents : []} sprint={overview?.sprint ?? null} nextSprint={overview?.next_sprint ?? null} conversations={overview?.conversations ?? []} live={live} now={now} storyCap={overview?.finance.story_cap_usd ?? 0} engine={!!overview?.factory.engine}
            kickoff={!!overview?.factory.kickoff}
            epics={overview?.epics ?? {}} phase={overview?.phase ?? null} onProduct={() => setProductOpen2(true)}
            onChat={(kind, resumeId, text) => setChat({ kind, resumeId, text })} onOpen={setStoryId}
            onCreate={createStory}
            onReorder={async (ids, dragged) => { if (slug) { try { await api.reorderBacklog(slug, ids, dragged); } catch (e) { fail(e); } refresh(); } }}
            onUnpin={async (id) => { if (slug) { try { await api.unpin(slug, id); } catch (e) { fail(e); } refresh(); } }}
          />
        </section>
      </main>
      <EventTicker events={events} agents={overview?.agents ?? []} factory={overview?.factory.name ?? ""} />
      {chat && slug && <ChatModal key={chat.resumeId ?? chat.kind} slug={slug} kind={chat.kind} resumeId={chat.resumeId} initialText={chat.text} kickoff={!!overview?.factory.kickoff} commercial={overview?.factory.kind === "commercial"} onBrainstorm={(text) => setChat({ kind: "brainstorm", text })} onClose={() => { setChat(null); refresh(); }} />}
      {financeOpen && slug && <FinanceModal slug={slug} onClose={() => setFinanceOpen(false)} />}
      {factoryOpen && slug && <FactoryModal slug={slug} onClose={() => setFactoryOpen(false)} onOpenSprint={(id) => { setFactoryOpen(false); setSprintsAt(id); }} onOpenStory={setStoryId} />}
      {growthOpen && slug && <GrowthModal slug={slug} onClose={() => { setGrowthOpen(false); refresh(); }} />}
      {productOpen2 && slug && <ProductModal slug={slug} onBrainstorm={(text) => { setProductOpen2(false); setChat({ kind: "brainstorm", text }); }} onClose={() => { setProductOpen2(false); refresh(); }} />}
      {design && slug && <DesignModal slug={slug} story={design.story} onReply={reply} onOpenStory={setStoryId} onClose={() => { setDesign(null); refresh(); }} />}
      {sprintsAt !== null && slug && <SprintsModal slug={slug} initial={sprintsAt || null} onClose={() => setSprintsAt(null)} onOpenStory={setStoryId} />}
      {settingsOpen && slug && <SettingsModal slug={slug} onClose={() => { setSettingsOpen(false); refresh(); }} />}
      {newFactoryOpen && <NewFactoryModal onClose={async (created) => { setNewFactoryOpen(false); await loadFactories(); if (created) setSlug(created); }} />}
      {agentName && slug && <AgentDrawer slug={slug} name={agentName} onClose={() => setAgentName(null)} onOpenStory={setStoryId} />}
      {storyId && slug && <StoryDrawer slug={slug} id={storyId} onClose={() => setStoryId(null)} onOpenSprint={(id) => { setStoryId(null); setSprintsAt(id); }} onOpenDesign={(id) => { setStoryId(null); setDesign({ story: id }); }} />}
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
    Object.assign(next, { task: Number(p.task), task_text: String(p.text ?? ""), origin: String(p.origin ?? ""), calls: 0, last_tool: null, last_target: null, task_since: next.last_at });
  } else if (e.type === "worker.task_finished") {
    Object.assign(next, { task: null, task_text: null, calls: null, last_tool: null, last_target: null, task_since: null });
  } else if (e.type === "story.stage" && p.stage && p.stage !== next.stage) {
    // the engine repeats the stage after every node: only a new one restarts the clock
    Object.assign(next, { stage: String(p.stage), stage_since: next.last_at });
  } else if (e.type === "agent.state") {
    // the Loompa's own words for what it is doing; it goes quiet when the agent stops
    next.detail = ["WORKING", "TESTING"].includes(String(p.state)) ? String(p.detail ?? "") || null : null;
  } else if (e.type === "tool.call") {
    Object.assign(next, { calls: (next.calls ?? 0) + 1, last_tool: String(p.tool ?? ""), last_target: String(p.path ?? p.query ?? "") || null });
  }
  if (e.type === "llm.call" && p.model) next.model = String(p.model);
  next.thinking = e.type === "llm.progress" ? Number(p.tokens ?? 0) : null;
  return next;
}
