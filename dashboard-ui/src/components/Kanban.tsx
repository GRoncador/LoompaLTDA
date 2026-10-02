import { useRef, useState } from "react";
import LoompaFigure from "./LoompaFigure";
import type { Agent, Column, ConversationKind, ConversationSummary, QuickStoryResult, SprintSummary, StoryActivity, StoryCard } from "../types";

const TONE: Record<string, string> = {
  BACKLOG: "border-slate-600", SPEC: "border-violet-500", DEV: "border-emerald-500", TEST: "border-sky-500", AWAITING_FOUNDER: "border-amber-500", DONE: "border-slate-500",
};

const KIND_LABEL: Record<string, string> = { bugfix: "correção", research: "pesquisa", feature: "funcionalidade" };

export default function Kanban({ onOpenSprint, columns, agents = [], sprint, nextSprint, conversations, live, now, onChat, onOpen, onCreate, onReorder, onUnpin }: {
  onOpenSprint: (id: string) => void;
  columns: Column[]; agents?: Agent[]; sprint: SprintSummary | null; nextSprint: { id: string; goal: string; story_ids: string[] } | null;
  conversations: ConversationSummary[]; live: Record<string, StoryActivity>; now: number;
  onChat: (kind: ConversationKind, resumeId?: string) => void; onOpen: (id: string) => void;
  onCreate: (title: string) => Promise<QuickStoryResult | null>; onReorder: (ids: string[], dragged: string | null) => Promise<void>; onUnpin: (id: string) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [reading, setReading] = useState(false); // the Product Owner is reading a quick story
  const [filed, setFiled] = useState<{ id: string; title: string; kind: string; rewritten: boolean } | null>(null);
  // drag-and-drop priority, backlog only: the preview order while dragging, sent on drop
  const [dragId, setDragId] = useState<string | null>(null);
  const [preview, setPreview] = useState<string[] | null>(null);
  const dropped = useRef(false); // dragend fires after drop: only a cancelled drag resets
  const backlog = columns.find((c) => c.key === "BACKLOG")?.stories ?? [];
  const ordered = (c: Column) => {
    if (c.key !== "BACKLOG" || !preview) return c.stories;
    const byId = new Map(c.stories.map((s) => [s.id, s]));
    return preview.map((id) => byId.get(id)).filter((s): s is StoryCard => !!s);
  };
  const dragOver = (overId: string) => {
    if (!dragId || dragId === overId) return;
    const ids = (preview ?? backlog.map((s) => s.id)).filter((id) => id !== dragId);
    ids.splice(ids.indexOf(overId), 0, dragId);
    setPreview(ids);
  };
  const drop = async () => {
    const ids = preview, moved = dragId;
    dropped.current = true;
    setDragId(null);
    if (ids && ids.join() !== backlog.map((s) => s.id).join()) await onReorder(ids, moved);
    setPreview(null);
  };
  // the founder's quick story goes to the Product Owner first: filed as it reads, or a review chat
  const create = async () => {
    const t = title.trim();
    if (!t || reading) return;
    setReading(true); setFiled(null);
    try {
      const r = await onCreate(t);
      if (!r) return;
      setTitle("");
      if (r.status === "created") {
        const rewritten = !!r.story && r.story.title !== t;
        setFiled({ id: r.id, title: r.story?.title ?? t, kind: r.story?.kind ?? "feature", rewritten });
      }
    } finally { setReading(false); }
  };
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">📋 Kanban de Fluxo de Valor</h2>
          {sprint && (
            <button className="chip bg-sky-900/50 text-sky-200 hover:bg-sky-800/60" title={`${sprint.goal ? `${sprint.goal} · ` : ""}abrir na aba Sprints`} onClick={() => onOpenSprint(sprint.id)}>
              {sprint.id} · {sprint.status === "open" ? "em planejamento" : "rodando"} · {sprint.progress.done}/{sprint.progress.total}
              {sprint.progress.waiting > 0 ? ` · ${sprint.progress.waiting} aguardando você` : ""}
            </button>
          )}
          {/* one sprint runs at a time; the next one can be assembled and waits (ADR-0018) */}
          {sprint?.status === "running" && nextSprint && (
            <span className="chip bg-slate-800 text-slate-300" title={`Montado numa reunião; inicia por uma nova reunião quando o ${sprint.id} terminar${nextSprint.goal ? ` · meta: ${nextSprint.goal}` : ""}`}>
              próximo: {nextSprint.id} · {nextSprint.story_ids.length} {nextSprint.story_ids.length === 1 ? "card" : "cards"}
            </span>
          )}
          {/* a sprint starts only from a meeting, after the Product Owner's proposal (ADR-0017) */}
          <button className="btn-primary text-xs" title="O Master abre com o estado do projeto; o Product Owner propõe o sprint" onClick={() => onChat("meeting")}>🗓 Reunião de Sprint</button>
          <button className="btn-ghost text-xs" title="Discutir uma ideia com a fábrica" onClick={() => onChat("brainstorm")}>💡 Brainstorm</button>
          {conversations.length > 0 && (
            <details className="relative">
              <summary className="btn-ghost cursor-pointer list-none text-xs" title="Conversas em aberto">💬 {conversations.length}</summary>
              <ul className="card absolute left-0 z-30 mt-1 w-72 space-y-1 p-2 text-xs">
                {conversations.map((c) => (
                  <li key={c.id}>
                    <button className="w-full rounded px-2 py-1 text-left hover:bg-line" onClick={(e) => { (e.currentTarget.closest("details") as HTMLDetailsElement).open = false; onChat(c.kind, c.id); }}>
                      <span className="text-slate-500">{c.id} · {c.kind === "meeting" ? "reunião" : c.kind === "review" ? "revisão do PO" : "brainstorm"}</span>
                      <div className="truncate text-slate-100">{c.title || "(sem título)"}</div>
                      <div className="text-[10px] text-slate-500">{c.kind === "review" ? "pedido aguardando você" : `${c.cards} cards`} · {c.turns} mensagens</div>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); create(); }}>
          {filed && (
            <button type="button" className="max-w-[18rem] truncate text-[11px] text-emerald-300 hover:underline" title="O Product Owner gravou o card; clique para ver" onClick={() => { onOpen(filed.id); setFiled(null); }}>
              ✔ {filed.id} · {filed.title} · {KIND_LABEL[filed.kind] ?? filed.kind}{filed.rewritten ? " (reescrita pelo PO)" : ""}
            </button>
          )}
          <input value={title} disabled={reading} onChange={(e) => setTitle(e.target.value)} placeholder={reading ? "O Product Owner está lendo…" : "Nova história rápida…"} className="w-64 rounded-md border border-line bg-ink px-2 py-1 text-xs disabled:opacity-60" />
          <button className="btn-ghost text-xs" type="submit" disabled={reading || !title.trim()} title="O Product Owner lê o pedido, classifica e grava, ou explica por que não">{reading ? "lendo…" : "+ Backlog"}</button>
        </form>
      </div>
      <div className="scroll-thin grid flex-1 grid-cols-6 gap-2 overflow-x-auto p-3">
        {columns.map((c) => (
          <div key={c.key} className={`flex min-w-[150px] flex-col rounded-md border-t-4 bg-ink/50 ${TONE[c.key] ?? ""}`}>
            <div className="flex items-center justify-between px-2 py-1 text-xs font-semibold text-slate-300">
              <span>{c.label}</span><span className="text-slate-500">{c.stories.length}</span>
            </div>
            <div className="scroll-thin flex-1 space-y-1.5 overflow-y-auto px-2 pb-2">
              {ordered(c).map((s) =>
                c.key === "BACKLOG" ? (
                  <div
                    key={s.id}
                    draggable
                    title="Arraste para mudar a prioridade"
                    className={`cursor-grab ${dragId === s.id ? "opacity-40" : ""}`}
                    onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; dropped.current = false; setDragId(s.id); }}
                    onDragOver={(e) => { e.preventDefault(); dragOver(s.id); }}
                    onDrop={(e) => { e.preventDefault(); drop(); }}
                    onDragEnd={() => { if (!dropped.current) { setDragId(null); setPreview(null); } }}
                  >
                    <Card s={s} onOpen={onOpen} onUnpin={onUnpin} />
                  </div>
                ) : (
                  <Card key={s.id} s={s} agent={AT_WORK.has(s.stage) ? holder(agents, s.id) : null} activity={AT_WORK.has(s.stage) ? latest(s.activity, live[s.id]) : null} now={now} onOpen={onOpen} />
                ),
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

// one fixed glyph per kind, left of the id, like an issue-type icon (11.1)
const KIND_ICON: Record<string, { icon: string; label: string }> = {
  feature: { icon: "✨", label: "funcionalidade" }, bugfix: { icon: "🐞", label: "correção" }, research: { icon: "🔎", label: "pesquisa" },
};

const BLOCKED_LABEL: Record<string, string> = { delivery: "revisar", question: "dúvida", waiver: "risco", dependency: "dependência saiu" };

function Card({ s, agent, activity, now, onOpen, onUnpin }: { s: StoryCard; agent?: Agent | null; activity?: StoryActivity | null; now?: number; onOpen: (id: string) => void; onUnpin?: (id: string) => Promise<void> }) {
  const pinned = s.priority_pinned && s.stage === "BACKLOG";
  const kind = KIND_ICON[s.kind] ?? KIND_ICON.feature;
  const open = !["DONE", "CANCELLED"].includes(s.stage);
  // alerts: what needs a look, coloured and kept apart from the card's classification
  const alerts: { text: string; cls: string; title?: string }[] = [];
  if (s.blocked_reason) alerts.push({ text: BLOCKED_LABEL[s.blocked_reason] ?? "bloqueada", cls: "bg-amber-900/60 text-amber-200", title: "Esperando a sua resposta na Caixa de Entrada" });
  if (s.qa_verdict === "CONCERNS") alerts.push({ text: "ressalvas", cls: "bg-orange-900/50 text-orange-200", title: "O Inspector aprovou com ressalvas" });
  if (s.current_tier === "tier1" && open) alerts.push({ text: "escalada", cls: "bg-blue-900/60 text-blue-200", title: "Passou para os modelos mais fortes depois de tentativas sem sucesso" });
  // classification: quiet text, read when you look for it
  const meta = [
    s.phase && !["BACKLOG", "AWAITING_FOUNDER", "DONE"].includes(s.stage) ? s.phase.replace("_", " ") : "",
    s.epic, s.complexity && s.complexity !== "STANDARD" ? s.complexity.toLowerCase() : "",
    s.cost_usd > 0 ? `$${s.cost_usd.toFixed(2)}` : "",
  ].filter(Boolean);
  const stalled = !!activity?.stalled;
  const deps = open && !s.waiting && !!s.depends_on?.length;
  return (
    <div className="relative rounded-md border border-line bg-panel p-2 text-xs hover:border-slate-500">
      <div className="flex items-center gap-1 text-[10px] text-slate-500">
        <span className="text-[12px] leading-none" title={kind.label}>{kind.icon}</span>
        <button className="hover:text-slate-300" onClick={() => onOpen(s.id)}>{s.id}</button>
        <span className="ml-auto flex items-center gap-1">
          {pinned && onUnpin && (
            <button className="text-[11px] opacity-80 hover:opacity-100" title="Posição fixada por você: o Product Owner não move este card. Clique para devolver a ordem a ele." onClick={() => onUnpin(s.id)}>📌</button>
          )}
          {s.sprint_id && <span className="rounded bg-sky-900/40 px-1 text-sky-300" title={`Sprint ${s.sprint_id}`}>{s.sprint_id}</span>}
        </span>
      </div>
      <button className="w-full text-left" onClick={() => onOpen(s.id)}>
        <div className="mt-0.5 font-medium leading-snug text-slate-100">{s.title}</div>
        {s.tasks_total > 0 && (
          <div className="mt-1.5 flex items-center gap-1.5" title={`${s.tasks_done} de ${s.tasks_total} tarefas do plano concluídas`}>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, (100 * s.tasks_done) / s.tasks_total)}%` }} />
            </div>
            <span className="text-[10px] tabular-nums text-slate-400">{s.tasks_done}/{s.tasks_total}</span>
          </div>
        )}
        {alerts.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {alerts.map((a) => <span key={a.text} className={`chip ${a.cls}`} title={a.title}>{a.text}</span>)}
          </div>
        )}
        {s.waiting?.label && (
          <div className="mt-1 text-[10px] text-indigo-300" title="Esperando sem precisar de você: retoma sozinha quando a cadeia estiver pronta">⏳ {s.waiting.label}</div>
        )}
        {activity && now !== undefined && <Activity a={activity} now={now} />}
        {(meta.length > 0 || deps || agent || stalled) && (
          <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
            <span className="min-w-0 flex-1 truncate">
              {meta.join(" · ")}
              {deps && <span title="O plano desta história só é feito depois que estas forem entregues">{meta.length ? " · " : ""}↳ depois de {s.depends_on!.join(", ")}</span>}
            </span>
            {stalled ? (
              <span className="shrink-0 rounded bg-red-900/60 px-1 text-red-200" title="Nenhum sinal da fábrica há muito tempo: o Ops foi avisado">⏸</span>
            ) : agent ? <Avatar agent={agent} /> : null}
          </div>
        )}
      </button>
    </div>
  );
}

/** The Loompa holding the card now, pulsing while it works. */
function Avatar({ agent }: { agent: Agent }) {
  return (
    <span className="flex shrink-0 items-center gap-1 text-[10px] text-slate-300" title={`${agent.name} está com este card${agent.detail ? ` · ${agent.detail}` : ""}`}>
      <LoompaFigure role={agent.role} busy />
      {agent.name.replace(/ Loompa$/, "")}
    </span>
  );
}

// stages in which a story is being worked on: only those show a live line
const AT_WORK = new Set(["SPEC", "PLAN", "DEV", "TEST", "REVIEW"]);

// What each tool means for someone who never opened a terminal.
const DOING: Record<string, string> = {
  read_file: "lendo", list_dir: "olhando pastas", search: "buscando", find_symbol: "procurando",
  write_file: "escrevendo", edit_file: "editando", apply_patch: "editando", delete_file: "apagando",
  run_tests: "rodando os testes", run_lint: "conferindo o estilo", fix_lint: "arrumando o estilo",
};
const HAPPENING: Record<string, string> = {
  "llm.call": "pensando", "tool.call": "trabalhando", "agent.state": "trabalhando", "story.stage": "mudou de etapa",
  "inspector.tests": "testou", "inspector.verdict": "revisou", "worktree.commit": "salvou uma versão",
  "worker.task_started": "começou uma tarefa", "worker.task_finished": "terminou uma tarefa", "llm.cut": "resposta cortada, tentando de novo",
  "llm.fallthrough": "trocando de modelo", "scheduler.dispatch": "começando",
};

/** Who is working on this card right now: an agent whose current story it is, not idle. */
function holder(agents: Agent[], id: string): Agent | null {
  return agents.find((a) => a.story_id === id && a.state !== "IDLE") ?? null;
}

/** The newer of the server's snapshot and what live events said since. */
function latest(a?: StoryActivity | null, b?: StoryActivity): StoryActivity | null {
  if (!a) return b ?? null;
  if (!b) return a;
  return Date.parse(b.last_at) >= Date.parse(a.last_at) ? b : a;
}

function tokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

function ago(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)}h${String(Math.floor((seconds % 3600) / 60)).padStart(2, "0")}`;
}

function Activity({ a, now }: { a: StoryActivity; now: number }) {
  const silent = Math.max(0, (now - Date.parse(a.last_at)) / 1000);
  const quiet = silent > 300; // five minutes without a word: worth a look, not yet an alarm
  const target = a.last_target ? ` ${a.last_target.split("/").pop()}` : "";
  // a streamed call still writing: how much it has written so far (ADR-0016)
  const thinking = a.thinking ? `pensando · ${tokens(a.thinking)} tokens` : "";
  const what = a.task
    ? `T${a.task} · ${a.calls ?? 0} ${a.calls === 1 ? "passo" : "passos"}${thinking ? ` · ${thinking}` : a.last_tool ? ` · ${DOING[a.last_tool] ?? a.last_tool}${target}` : ""}`
    : `${a.last_agent || "fábrica"} · ${thinking || (HAPPENING[a.last_event] ?? "trabalhando")}`;
  return (
    <div className="mt-1 flex items-center gap-1 text-[10px]" title={a.task_text ? `T${a.task}: ${a.task_text}` : undefined}>
      {a.stalled ? (
        <span className="chip shrink-0 whitespace-nowrap bg-red-900/60 text-red-200">parada há {ago(silent)}</span>
      ) : quiet ? (
        <span className="chip shrink-0 whitespace-nowrap bg-amber-900/50 text-amber-200">quieta há {ago(silent)}</span>
      ) : (
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-emerald-400" />
      )}
      {/* how long ago comes first: it is what tells a slow story from a stuck one */}
      <span className="truncate text-slate-400">{!a.stalled && !quiet ? `há ${ago(silent)} · ` : ""}{what}</span>
    </div>
  );
}
