import { useRef, useState } from "react";
import type { Column, SprintSummary, StoryActivity, StoryCard } from "../types";

const TONE: Record<string, string> = {
  BACKLOG: "border-slate-600", SPEC: "border-violet-500", DEV: "border-emerald-500", TEST: "border-sky-500", AWAITING_FOUNDER: "border-amber-500", DONE: "border-slate-500",
};

export default function Kanban({ columns, sprint, live, now, onStartSprint, onOpen, onPromote, onCreate, onReorder }: { columns: Column[]; sprint: SprintSummary | null; live: Record<string, StoryActivity>; now: number; onStartSprint: () => Promise<void>; onOpen: (id: string) => void; onPromote: (id: string) => void; onCreate: (title: string) => Promise<void>; onReorder: (ids: string[]) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [starting, setStarting] = useState(false);
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
    const ids = preview;
    dropped.current = true;
    setDragId(null);
    if (ids && ids.join() !== backlog.map((s) => s.id).join()) await onReorder(ids);
    setPreview(null);
  };
  // the founder's own cards wait for a sprint; Kaizen findings wait for an explicit yes
  const waiting = (columns.find((c) => c.key === "BACKLOG")?.stories ?? []).filter((s) => s.origin !== "kaizen").length;
  return (
    <>
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold">📋 Kanban de Fluxo de Valor</h2>
          {sprint && (
            <span className="chip bg-sky-900/50 text-sky-200" title={sprint.goal || undefined}>
              {sprint.id} · {sprint.status === "open" ? "em planejamento" : "rodando"} · {sprint.progress.done}/{sprint.progress.total}
              {sprint.progress.waiting > 0 ? ` · ${sprint.progress.waiting} aguardando você` : ""}
            </span>
          )}
          {waiting > 0 && (
            <button className="btn-primary text-xs" disabled={starting} onClick={async () => { setStarting(true); try { await onStartSprint(); } finally { setStarting(false); } }}>
              ▶ Iniciar sprint ({waiting})
            </button>
          )}
        </div>
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (title.trim()) { await onCreate(title.trim()); setTitle(""); } }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Nova história rápida…" className="w-64 rounded-md border border-line bg-ink px-2 py-1 text-xs" />
          <button className="btn-ghost text-xs" type="submit">+ Backlog</button>
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
                    <Card s={s} onOpen={onOpen} onPromote={onPromote} />
                  </div>
                ) : (
                  <Card key={s.id} s={s} activity={AT_WORK.has(s.stage) ? latest(s.activity, live[s.id]) : null} now={now} onOpen={onOpen} onPromote={onPromote} />
                ),
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Card({ s, activity, now, onOpen, onPromote }: { s: StoryCard; activity?: StoryActivity | null; now?: number; onOpen: (id: string) => void; onPromote: (id: string) => void }) {
  const kaizen = s.origin === "kaizen" && s.stage === "BACKLOG";
  return (
    <div className="rounded-md border border-line bg-panel p-2 text-xs hover:border-slate-500">
      <button className="w-full text-left" onClick={() => onOpen(s.id)}>
        <div className="flex items-center justify-between text-[10px] text-slate-500">
          <span>{s.id}</span>
          <span>{s.cost_usd > 0 ? `$${s.cost_usd.toFixed(2)}` : ""}</span>
        </div>
        <div className="mt-0.5 font-medium leading-snug text-slate-100">{s.title}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          {s.epic && <span className="chip bg-slate-800 text-slate-400">{s.epic}</span>}
          {s.kind && s.kind !== "feature" && <span className="chip bg-fuchsia-900/50 text-fuchsia-200">{s.kind}</span>}
          {s.complexity && s.complexity !== "STANDARD" && <span className="chip bg-slate-800 text-slate-400">{s.complexity.toLowerCase()}</span>}
          {s.phase && s.stage !== "AWAITING_FOUNDER" && s.stage !== "DONE" && <span className="chip bg-slate-800 text-slate-400" title={s.route.join(" → ")}>{s.phase.replace("_", " ")}</span>}
          {s.current_tier === "tier1" && <span className="chip bg-blue-900/60 text-blue-200">tier 1</span>}
          {s.qa_verdict === "CONCERNS" && <span className="chip bg-orange-900/50 text-orange-200">ressalvas</span>}
          {s.blocked_reason && <span className="chip bg-amber-900/60 text-amber-200">{s.blocked_reason === "delivery" ? "revisar" : s.blocked_reason === "question" ? "dúvida" : s.blocked_reason === "waiver" ? "risco" : "bloqueada"}</span>}
          {s.tasks_total > 0 && <span className="chip bg-slate-800 text-slate-400">{s.tasks_done}/{s.tasks_total}</span>}
        </div>
        {activity && now !== undefined && <Activity a={activity} now={now} />}
      </button>
      {kaizen && <button className="mt-1 w-full rounded bg-lime-900/50 py-0.5 text-[10px] text-lime-200 hover:bg-lime-800/60" onClick={() => onPromote(s.id)}>💡 executar agora (fora do sprint)</button>}
    </div>
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
