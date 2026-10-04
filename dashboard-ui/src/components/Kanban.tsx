import { useRef, useState } from "react";
import LoompaFigure from "./LoompaFigure";
import type { Agent, Column, ConversationKind, ConversationSummary, QuickStoryResult, SprintSummary, StoryActivity, StoryCard } from "../types";

const TONE: Record<string, string> = {
  BACKLOG: "border-slate-600", SPEC: "border-violet-500", DEV: "border-emerald-500", TEST: "border-sky-500", AWAITING_FOUNDER: "border-amber-500", DONE: "border-slate-500",
};

const KIND_LABEL: Record<string, string> = { bugfix: "correção", research: "pesquisa", feature: "funcionalidade" };

export default function Kanban({ onOpenSprint, columns, agents = [], sprint, nextSprint, conversations, live, now, storyCap = 0, engine = false, kickoff = false, onChat, onOpen, onCreate, onReorder, onUnpin }: {
  onOpenSprint: (id: string) => void; kickoff?: boolean;
  columns: Column[]; agents?: Agent[]; sprint: SprintSummary | null; nextSprint: { id: string; goal: string; story_ids: string[] } | null;
  conversations: ConversationSummary[]; live: Record<string, StoryActivity>; now: number;
  storyCap?: number; // o teto por história da aba Orçamento (0 = sem teto)
  engine?: boolean; // a esteira está rodando: só então um card pode estar em execução
  onChat: (kind: ConversationKind, resumeId?: string) => void; onOpen: (id: string) => void;
  onCreate: (title: string) => Promise<QuickStoryResult | null>; onReorder: (ids: string[], dragged: string | null) => Promise<void>; onUnpin: (id: string) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [reading, setReading] = useState(false); // the Product Owner is reading a quick story
  const [filed, setFiled] = useState<{ id: string; title: string; kind: string; rewritten: boolean } | null>(null);
  // drag-and-drop priority, backlog only: the preview order while dragging, sent on drop
  const [dragId, setDragId] = useState<string | null>(null);
  const [preview, setPreview] = useState<string[] | null>(null);
  // the mouse over a sprint tag, or over what blocks a card, puts those stories in bold: the
  // titles are plain otherwise, so the board has one weight and the pointer does the pointing
  const [hl, setHl] = useState<{ sprint?: string; ids?: string[] }>({});
  const dropped = useRef(false); // dragend fires after drop: only a cancelled drag resets
  const backlog = columns.find((c) => c.key === "BACKLOG")?.stories ?? [];
  const ordered = (c: Column) => {
    if (c.key === "BACKLOG") {
      if (!preview) return c.stories;
      const byId = new Map(c.stories.map((s) => [s.id, s]));
      return preview.map((id) => byId.get(id)).filter((s): s is StoryCard => !!s);
    }
    // a card a Loompa is working on right now goes to the top of its column: the first cards
    // are the ones running, then the ones next in line (11.2)
    return [...c.stories].sort((a, b) => Number(working(b, live, now, engine)) - Number(working(a, live, now, engine)));
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
          <button className={`${kickoff ? "btn-ghost opacity-50" : "btn-primary"} text-xs`} disabled={kickoff}
            title={kickoff ? "Comece pelo brainstorm de abertura: ele decide a visão, a primeira versão e a stack" : "O Master abre com o estado do projeto; o Product Owner propõe o sprint"}
            onClick={() => onChat("meeting")}>🗓 Reunião de Sprint</button>
          <button className={`${kickoff ? "btn-primary" : "btn-ghost"} text-xs`}
            title={kickoff ? "Abertura da fábrica: visão, primeira versão e stack" : "Discutir uma ideia com a fábrica"}
            onClick={() => onChat("brainstorm")}>{kickoff ? "💡 Brainstorm de abertura" : "💡 Brainstorm"}</button>
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
            <div className="scroll-thin flex-1 space-y-1.5 overflow-y-auto px-2 pb-2 pt-1">
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
                    <Card s={s} cap={storyCap} hl={hl} onHl={setHl} onOpen={onOpen} onUnpin={onUnpin} />
                  </div>
                ) : (
                  <Card key={s.id} s={s} cap={storyCap} hl={hl} onHl={setHl} running={working(s, live, now, engine)} agent={AT_WORK.has(s.stage) ? holder(agents, s, latest(s.activity, live[s.id])) : null} activity={AT_WORK.has(s.stage) ? latest(s.activity, live[s.id]) : null} now={now} onOpen={onOpen} />
                ),
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/** A card is being worked on when it sits in a working stage and the factory gave a sign of
 * life on it in the last five minutes. Those cards rise to the top and get the amber ring. */
function working(s: StoryCard, live: Record<string, StoryActivity>, now: number, engine: boolean): boolean {
  // the belt stopped: nothing is running, however recent the last sign of life was
  if (!engine) return false;
  if (!AT_WORK.has(s.stage) || s.waiting?.gate === "wait") return false;
  const a = latest(s.activity, live[s.id]);
  if (!a || a.stalled) return false;
  return (now - Date.parse(a.last_at)) / 1000 <= 300;
}

// one fixed glyph per kind, left of the id, like an issue-type icon (11.1)
const KIND_ICON: Record<string, { icon: string; label: string }> = {
  feature: { icon: "✨", label: "funcionalidade" }, bugfix: { icon: "🐞", label: "correção" }, research: { icon: "🔎", label: "pesquisa" },
};

const SEVERITY = ["low", "medium", "high"]; // worst last: the chip names the worst one
const FINDING: Record<string, string> = { SEC: "segurança", PERF: "desempenho", TEST: "testes", ARCH: "arquitetura" };

const BLOCKED_LABEL: Record<string, string> = { delivery: "revisar", question: "dúvida", waiver: "risco", dependency: "dependência saiu" };

// the size the Product Owner gave the card, always on the card: it says what to expect from it
const COMPLEXITY: Record<string, { text: string; cls: string; title: string }> = {
  SIMPLE: { text: "simples", cls: "bg-sky-900/50 text-sky-300", title: "História simples: caminho curto, sem muitas etapas" },
  STANDARD: { text: "padrão", cls: "bg-slate-800 text-slate-300", title: "História padrão: o fluxo completo da fábrica" },
  COMPLEX: { text: "complexa", cls: "bg-fuchsia-900/50 text-fuchsia-300", title: "História complexa: mais tarefas, mais revisão e mais custo" },
};

function Card({ s, agent, activity, now, running = false, cap = 0, hl = {}, onHl, onOpen, onUnpin }: { s: StoryCard; agent?: Agent | null; activity?: StoryActivity | null; now?: number; running?: boolean; cap?: number; hl?: { sprint?: string; ids?: string[] }; onHl?: (h: { sprint?: string; ids?: string[] }) => void; onOpen: (id: string) => void; onUnpin?: (id: string) => Promise<void> }) {
  const pinned = s.priority_pinned && s.stage === "BACKLOG";
  const kind = KIND_ICON[s.kind] ?? KIND_ICON.feature;
  const open = !["DONE", "CANCELLED"].includes(s.stage);
  // alerts: what needs a look, coloured and kept apart from the card's classification
  const alerts: { text: string; cls: string; title?: string }[] = [];
  if (s.blocked_reason) alerts.push({ text: BLOCKED_LABEL[s.blocked_reason] ?? "bloqueada", cls: "bg-amber-900/60 text-amber-200", title: "Esperando a sua resposta na Caixa de Entrada" });
  // the escalation ladder as one number: each failed attempt (Inspector FAIL, or a task the
  // Worker could not finish) is one try, and tier 1 is the stronger models it climbed to
  const failed = (s.attempts?.tier2 ?? 0) + (s.attempts?.tier1 ?? 0);
  if (failed > 0) {
    const t1 = s.current_tier === "tier1";
    alerts.push({
      text: `${failed + 1}ª tentativa${t1 ? " · T1" : ""}`,
      cls: !open ? "bg-slate-800 text-slate-400" : t1 ? "bg-red-900/60 text-red-200" : "bg-amber-900/60 text-amber-200",
      title: `${failed} ${failed === 1 ? "tentativa" : "tentativas"} sem sucesso${t1 ? ", agora com os modelos mais fortes (Tier 1)" : ""}. Depois de três, a história para e a decisão é sua.`,
    });
  }
  if (s.qa_verdict === "CONCERNS") {
    // the kind of the worst finding fits in four letters and says where to look
    const found = [...(s.qa_findings ?? [])].sort((a, b) => SEVERITY.indexOf(b.severity) - SEVERITY.indexOf(a.severity));
    const kinds = [...new Set(found.map((f) => f.prefix))];
    alerts.push({
      text: kinds.length ? `ressalvas · ${kinds[0]}` : "ressalvas",
      cls: open ? "bg-orange-900/50 text-orange-200" : "bg-slate-800 text-slate-400",
      title: `O Inspector aprovou com ressalvas${kinds.length ? ` (${kinds.map((k) => FINDING[k] ?? k).join(", ")})` : ""}: achados de severidade média ou baixa, que não impedem a entrega. Os médios viram oportunidades do Kaizen.`,
    });
  }
  // past the per-story cap the card stops and waits for the founder: the number says so first
  const overCap = cap > 0 && s.cost_usd >= cap;
  const stalled = !!activity?.stalled;
  // what holds the card: short, red and naming the story that has to land first
  // the gate (when the server computed one) knows which are still missing; without it, the
  // card's own dependencies are the best we have
  const blockers = !open ? [] : s.waiting ? [...new Set([...s.waiting.gone, ...s.waiting.pending, ...s.waiting.chain])] : s.depends_on ?? [];
  const size = COMPLEXITY[s.complexity] ?? COMPLEXITY.STANDARD;
  // the pointer answers by lifting the card a little, never by changing a title's weight: this
  // card under the mouse, or a story someone's sprint tag / blocker chip is pointing at
  const pointed = !!hl.sprint && hl.sprint === s.sprint_id ? "sprint" : hl.ids?.includes(s.id) ? "block" : "";
  return (
    <div className={`group relative rounded-md border p-2 text-xs transition-transform duration-200 ease-out hover:scale-[1.03] ${pointed ? "scale-[1.03]" : ""} ${running ? "border-brand bg-brand/[0.07] ring-1 ring-brand running-glow" : "border-line bg-panel hover:border-slate-500"}`}>
      <div className="flex items-center gap-1 text-[10px] text-slate-500">
        <span className="text-[12px] leading-none" title={kind.label}>{kind.icon}</span>
        <button className="hover:text-slate-300" onClick={() => onOpen(s.id)}>{s.id}</button>
        <span className={`chip px-1.5 py-0 text-[9px] ${size.cls}`} title={size.title}>{size.text}</span>
        {s.sprint_id && (
          <span className="cursor-default rounded bg-sky-950/70 px-1 text-sky-500/80 hover:text-sky-300" title={`Sprint ${s.sprint_id}: passe o mouse para destacar as histórias dele`}
            onMouseEnter={() => onHl?.({ sprint: s.sprint_id ?? undefined })} onMouseLeave={() => onHl?.({})}>{s.sprint_id}</span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {pinned && onUnpin && (
            <button className="text-[11px] opacity-80 hover:opacity-100" title="Posição fixada por você: o Product Owner não move este card. Clique para devolver a ordem a ele." onClick={() => onUnpin(s.id)}>📌</button>
          )}
          {s.cost_usd > 0 && (
            <span className={`tabular-nums ${overCap ? "font-semibold text-amber-400" : "text-slate-500"}`}
              title={overCap ? `US$ ${s.cost_usd.toFixed(2)} gastos: passou do teto de US$ ${cap.toFixed(2)} por história` : `US$ ${s.cost_usd.toFixed(2)} gastos nesta história`}>
              ${s.cost_usd.toFixed(2)}
            </span>
          )}
        </span>
      </div>
      <button className="w-full text-left" onClick={() => onOpen(s.id)}>
        <div className={`mt-0.5 font-normal leading-snug transition-colors ${pointed === "block" ? "text-red-300" : pointed === "sprint" ? "text-sky-300" : "text-slate-100"}`}>{s.title}</div>
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
        {blockers.length > 0 && (
          <div className="mt-1">
            <span className="chip bg-red-900/70 text-red-200" title={s.waiting?.label || "O plano desta história só é feito depois que estas forem entregues"}
              onMouseEnter={() => onHl?.({ ids: blockers })} onMouseLeave={() => onHl?.({})}>
              🔒 {blockers.join(", ")}
            </span>
          </div>
        )}
        {/* the stage tag keeps the bottom-left corner and the Loompa the bottom-right one,
            whatever happens: only the text between them wraps, and it is never cut */}
        {(AT_WORK.has(s.stage) || agent || stalled) && (
          <div className="mt-1.5 flex items-end gap-1 text-[10px]">
            <Stage stage={s.stage} since={activity?.stage_since} now={now} />
            <span className="min-w-0 flex-1 break-words text-slate-500" title={activity ? doing(activity).title : ""}>{activity ? doing(activity).text : ""}</span>
            {stalled ? (
              <span className="shrink-0 rounded bg-red-900/60 px-1 text-red-200" title="Nenhum sinal da fábrica há muito tempo: o Ops foi avisado">⏸</span>
            ) : agent ? <Avatar agent={agent} /> : null}
          </div>
        )}
      </button>
    </div>
  );
}

/** The Loompa holding the card. No life dot here: the card's amber border already says which
 * ones are being worked on right now (11.2). */
function Avatar({ agent }: { agent: Agent }) {
  return (
    <span className="flex shrink-0 items-center gap-1 text-[10px] text-slate-300" title={`${agent.name} está com este card${agent.detail ? ` · ${agent.detail}` : ""}`}>
      <LoompaFigure role={agent.role} />
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

// Who holds a card in each stage when nothing more specific is known.
const STAGE_OWNER: Record<string, { name: string; role: string }> = {
  SPEC: { name: "Spec Loompa", role: "product" }, PLAN: { name: "Architect Loompa", role: "architect" },
  DEV: { name: "Worker Loompa", role: "worker" }, TEST: { name: "Inspector Loompa", role: "inspector" },
  REVIEW: { name: "Deployer Loompa", role: "deployer" },
};
// back-office Loompas speak about a story without holding it
const BACK_OFFICE = new Set(["ops", "finance", "kaizen"]);

/** Who is working on this card: whoever spoke last on this story, else the stage's owner. The
 * `agents` table keeps one row per name, so two stories in DEV shared one "Worker Loompa" row
 * and only the last card to write it showed an avatar (2026-10-03). */
function holder(agents: Agent[], s: StoryCard, activity: StoryActivity | null): Agent | null {
  const owner = STAGE_OWNER[s.stage];
  if (!owner) return null;
  const spoke = activity?.last_agent ? agents.find((a) => a.name === activity.last_agent) : undefined;
  const who = spoke && !BACK_OFFICE.has(spoke.role) ? spoke : agents.find((a) => a.name === owner.name);
  const base = who ?? ({ name: owner.name, role: owner.role, model: "", room: "dev", cost_usd: 0, updated_at: "" } as unknown as Agent);
  // the row's detail is about this card only when the row is
  return { ...base, state: "WORKING", story_id: s.id, detail: base.story_id === s.id ? base.detail : "" } as Agent;
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

function since(iso: string | null | undefined, now: number): number | null {
  return iso ? Math.max(0, (now - Date.parse(iso)) / 1000) : null;
}

/** The stage, as the card's loudest small thing: in the Kanban a column can hold two stages
 * (SPEC/PLAN, TEST/REVIEW), so the tag says which one, and next to it how long the card has
 * been in it — the phase's clock, not the current call's. */
const STAGE_TAG: Record<string, { text: string; cls: string; title: string }> = {
  SPEC: { text: "spec", cls: "bg-violet-500/20 text-violet-300 ring-1 ring-violet-500/40", title: "Escrevendo a especificação" },
  PLAN: { text: "plano", cls: "bg-indigo-500/20 text-indigo-300 ring-1 ring-indigo-500/40", title: "Quebrando a história em tarefas" },
  DEV: { text: "dev", cls: "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40", title: "Escrevendo o código" },
  TEST: { text: "teste", cls: "bg-sky-500/20 text-sky-300 ring-1 ring-sky-500/40", title: "O Inspector está conferindo" },
  REVIEW: { text: "entrega", cls: "bg-teal-500/20 text-teal-300 ring-1 ring-teal-500/40", title: "O Deployer está entregando" },
};

function Stage({ stage, since: at, now }: { stage: string; since?: string | null; now?: number }) {
  const tag = STAGE_TAG[stage];
  if (!tag) return null;
  const inStage = now !== undefined ? since(at, now) : null;
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span className={`chip px-1.5 py-0 text-[9px] ${tag.cls}`} title={tag.title}>{tag.text}</span>
      {inStage !== null && <span className="tabular-nums text-slate-400" title={`Nesta etapa há ${ago(inStage)}`}>{ago(inStage)}</span>}
    </span>
  );
}

/** What the Loompa is doing right now, in its own words whenever it said them ("alinhando
 * critérios ao pedido de ajustes", "abrindo o produto no navegador"). The generic word for the
 * event type is only the last resort. No clocks here — the stage tag carries the time. */
function doing(a: StoryActivity): { text: string; title: string } {
  const target = a.last_target ? ` ${a.last_target.split("/").pop()}` : "";
  // a streamed call still writing: how much it has written so far (ADR-0016)
  const thinking = a.thinking ? `pensando · ${tokens(a.thinking)} tokens` : "";
  if (a.task) {
    // "T3 (6)": the task and the tool calls it has made so far, then the one in progress
    const now = thinking || (a.last_tool ? `${DOING[a.last_tool] ?? a.last_tool}${target}` : "");
    return {
      text: `T${a.task} (${a.calls ?? 0})${now ? ` · ${now}` : ""}`,
      title: `${a.task_text ? `T${a.task}: ${a.task_text}\n` : ""}(${a.calls ?? 0}) ferramentas usadas nesta tarefa`,
    };
  }
  // the Worker's own detail is "T3: texto da tarefa", already said by the branch above
  const said = a.detail && !/^T\d+:/.test(a.detail) ? a.detail : "";
  const text = said ? (thinking ? `${said} · ${thinking}` : said) : thinking || HAPPENING[a.last_event] || "trabalhando";
  return { text, title: text };
}
