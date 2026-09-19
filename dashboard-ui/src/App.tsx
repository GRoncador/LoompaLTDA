import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { useSocket } from "./useSocket";
import type { ConversationKind, FactoryRef, LoompaEvent, Message, Overview } from "./types";
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

export default function App() {
  const [factories, setFactories] = useState<FactoryRef[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [events, setEvents] = useState<LoompaEvent[]>([]);
  const [chat, setChat] = useState<{ kind: ConversationKind; resumeId?: string } | null>(null);
  const [newFactoryOpen, setNewFactoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentName, setAgentName] = useState<string | null>(null);
  const [storyId, setStoryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const onEvent = useCallback((e: LoompaEvent) => {
    setEvents((prev) => [...prev.slice(-199), e]);
    if (["story.stage", "story.created", "inbox.new", "inbox.answered", "agent.state", "llm.call", "story.merged", "engine.started", "engine.stopped", "kaizen.learning", "story.promoted", "scheduler.paused", "settings.updated", "sprint.started", "sprint.done", "finding.decided", "inbox.decided", "conversation.opened", "conversation.turn", "conversation.committed", "conversation.discarded", "backlog.status", "backlog.priority"].includes(e.type)) {
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
  const startSprint = async () => {
    if (!slug) return;
    try { await api.startSprint(slug); setError(null); } catch (e) { setError(String(e)); }
    refresh();
  };

  const pendingCount = useMemo(() => overview?.inbox.filter((m) => m.kind === "decision" || m.kind === "blocked" || m.kind === "delivery").length ?? 0, [overview]);

  return (
    <div className="flex h-screen flex-col">
      <Header
        factories={factories} slug={slug} overview={overview} connected={connected} pending={pendingCount}
        onSwitch={switchFactory} onNewFactory={() => setNewFactoryOpen(true)} onChat={(kind, resumeId) => setChat({ kind, resumeId })} onToggleEngine={toggleEngine} onSettings={() => setSettingsOpen(true)}
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
          <Inbox messages={overview?.inbox ?? []} finance={overview?.finance ?? null} kaizen={overview?.kaizen_today ?? 0} onReply={reply} onArchive={async (m) => { if (slug) { await api.archive(slug, m.id); refresh(); } }} onOpenStory={setStoryId} />
        </section>
        <section className="card flex min-h-[260px] flex-col overflow-hidden lg:col-span-2">
          <Kanban columns={overview?.columns ?? []} sprint={overview?.sprint ?? null} onStartSprint={startSprint} onOpen={setStoryId} onPromote={async (id) => { if (slug) { await api.promote(slug, id); refresh(); } }} onCreate={async (title) => { if (slug) { await api.createStory(slug, title, ""); refresh(); } }} />
        </section>
      </main>
      <EventTicker events={events} />
      {chat && slug && <ChatModal key={chat.resumeId ?? chat.kind} slug={slug} kind={chat.kind} resumeId={chat.resumeId} onClose={() => { setChat(null); refresh(); }} />}
      {settingsOpen && slug && <SettingsModal slug={slug} onClose={() => { setSettingsOpen(false); refresh(); }} />}
      {newFactoryOpen && <NewFactoryModal onClose={async (created) => { setNewFactoryOpen(false); await loadFactories(); if (created) setSlug(created); }} />}
      {agentName && slug && <AgentDrawer slug={slug} name={agentName} onClose={() => setAgentName(null)} onOpenStory={setStoryId} />}
      {storyId && slug && <StoryDrawer slug={slug} id={storyId} onClose={() => setStoryId(null)} />}
    </div>
  );
}
