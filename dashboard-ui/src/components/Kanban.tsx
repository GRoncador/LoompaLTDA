import { useRef, useState } from "react";
import LoompaFigure from "./LoompaFigure";
import type { Agent, Column, ConversationKind, ConversationSummary, QuickStoryResult, SprintSummary, StoryActivity, StoryCard } from "../types";
import { Icon, type IconName } from "./Icon";

const TONE: Record<string, string> = {
  BACKLOG: "border-slate-600", SPEC: "border-violet-500", DEV: "border-emerald-500", TEST: "border-sky-500", AWAITING_FOUNDER: "border-amber-500", DONE: "border-slate-500",
};

// a dependency releases the card for work once it is delivered, or once the Inspector passed its
// tests and it waits only for the merge (the same condition the gate calls "unmerged")
const TEST_PASSED_STAGES = new Set(["TEST", "REVIEW", "AWAITING_FOUNDER", "DONE"]);
export function released(d: StoryCard | undefined): boolean {
  if (!d) return false;
  if (d.stage === "DONE") return true;
  return TEST_PASSED_STAGES.has(d.stage) && (d.qa_verdict === "PASS" || d.qa_verdict === "CONCERNS");
}

const KIND_LABEL: Record<string, string> = { bugfix: "correção", research: "pesquisa", feature: "funcionalidade" };

export default function Kanban({ onOpenSprint, columns, agents = [], sprint, nextSprint, conversations, live, now, storyCap = 0, engine = false, kickoff = false, epics = {}, phase = null, onProduct, onChat, onOpen, onCreate, onReorder, onUnpin }: {
  onOpenSprint: (id: string) => void; kickoff?: boolean;
  epics?: Record<string, string>; // product epics by id (plan 16.7)
  phase?: { name: string; percent: number; cards: number } | null; onProduct?: () => void;
  columns: Column[]; agents?: Agent[]; sprint: SprintSummary | null; nextSprint: { id: string; goal: string; story_ids: string[] } | null;
  conversations: ConversationSummary[]; live: Record<string, StoryActivity>; now: number;
  storyCap?: number; // o teto por história da aba Orçamento (0 = sem teto)
  engine?: boolean; // a esteira está rodando: só então um card pode estar em execução
  onChat: (kind: ConversationKind, resumeId?: string, text?: string) => void; onOpen: (id: string) => void;
  onCreate: (title: string) => Promise<QuickStoryResult | null>; onReorder: (ids: string[], dragged: string | null) => Promise<void>; onUnpin: (id: string) => Promise<void>;
}) {
  // every card by id, so a dependency tag can read what its dependency has reached
  const byId = new Map(columns.flatMap((c) => c.stories).map((s) => [s.id, s] as const));
  const [title, setTitle] = useState("");
  const [reading, setReading] = useState(false); // the Product Owner is reading a quick story
  const [filed, setFiled] = useState<{ id: string; title: string; kind: string; rewritten: boolean } | null>(null);
  // drag-and-drop priority, backlog only: the preview order while dragging, sent on drop
  const [dragId, setDragId] = useState<string | null>(null);
  const [preview, setPreview] = useState<string[] | null>(null);
  // the mouse over a sprint tag, or over what blocks a card, puts those stories in bold: the
  // titles are plain otherwise, so the board has one weight and the pointer does the pointing
  const [hl, setHl] = useState<{ sprint?: string; ids?: string[] }>({});
  const [epicFilter, setEpicFilter] = useState(""); // only one product epic's cards (16.7)
  const [showArchived, setShowArchived] = useState(false); // Concluído: the cards a later sprint archived
  const dropped = useRef(false); // dragend fires after drop: only a cancelled drag resets
  const backlog = columns.find((c) => c.key === "BACKLOG")?.stories ?? [];
  const ordered = (c: Column) => {
    if (c.key === "BACKLOG") {
      if (!preview) return c.stories;
      const byId = new Map(c.stories.map((s) => [s.id, s]));
      return preview.map((id) => byId.get(id)).filter((s): s is StoryCard => !!s);
    }
    // a card a Loompa is working on right now goes to the top of its column: the first cards
    // are the ones running, then the ones next in line (11.2). Archived cards go last (Concluído)
    return [...c.stories].sort((a, b) => Number(!!a.archived) - Number(!!b.archived) || Number(working(b, live, now, engine)) - Number(working(a, live, now, engine)));
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
          <h2 className="flex items-center gap-1.5 text-sm font-semibold"><Icon name="clipboard" className="h-4 w-4 text-slate-400" />Kanban</h2>
          {phase && (
            <button className="chip bg-violet-900/50 text-violet-200 hover:bg-violet-800/60" title="Fase do produto e o quanto dos cards dos épicos dela já foi entregue" onClick={onProduct}>
              {phase.name}{phase.cards > 0 ? `: ${phase.percent}%` : ""}
            </button>
          )}
          {Object.keys(epics).length > 0 && (
            <select className="rounded-md border border-line bg-ink px-1 py-0.5 text-xs" value={epicFilter} onChange={(e) => setEpicFilter(e.target.value)} title="Mostrar só os cards de um épico">
              <option value="">todos os épicos</option>
              {Object.entries(epics).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          )}
          {sprint && (
            <button className="chip bg-sky-900/50 text-sky-200 hover:bg-sky-800/60" title={`${sprint.goal ? `${sprint.goal} · ` : ""}abrir na aba Sprints`} onClick={() => onOpenSprint(sprint.id)}>
              {sprint.id} · {sprint.status === "open" ? "em planejamento" : "rodando"} · {sprint.progress.done}/{sprint.progress.total}
              {sprint.progress.waiting > 0 ? ` · ${sprint.progress.waiting} aguardando decisão` : ""}
            </button>
          )}
          {/* one sprint runs at a time; the next one can be assembled and waits (ADR-0018) */}
          {sprint?.status === "running" && nextSprint && (
            <span className="chip bg-slate-800 text-slate-300" title={`Montado numa reunião; inicia por uma nova reunião quando o ${sprint.id} terminar${nextSprint.goal ? ` · meta: ${nextSprint.goal}` : ""}`}>
              próximo: {nextSprint.id} · {nextSprint.story_ids.length} {nextSprint.story_ids.length === 1 ? "card" : "cards"}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); create(); }}>
            {filed && (
              <button type="button" className="max-w-[18rem] truncate text-[11px] text-emerald-300 hover:underline" title="O Product Owner gravou o card; clique para ver" onClick={() => { onOpen(filed.id); setFiled(null); }}>
                <Icon name="check" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />{filed.id} · {filed.title} · {KIND_LABEL[filed.kind] ?? filed.kind}{filed.rewritten ? " (reescrita pelo PO)" : ""}
              </button>
            )}
            <input value={title} disabled={reading} onChange={(e) => setTitle(e.target.value)} placeholder={reading ? "O Product Owner está lendo…" : "Nova história rápida…"} className="w-64 rounded-md border border-line bg-ink px-2 py-1 text-xs disabled:opacity-60" />
            <button className="btn-ghost text-xs" type="submit" disabled={reading || !title.trim()} title="O Product Owner lê o pedido, classifica e grava, ou explica por que não">{reading ? "lendo…" : "+ Backlog"}</button>
          </form>
          {conversations.length > 0 && (
            <details className="relative">
              <summary className="btn-ghost cursor-pointer list-none text-xs" title="Conversas em aberto"><Icon name="message" className="mr-1 inline h-3 w-3 align-[-1px]" />{conversations.length}</summary>
              <ul className="card absolute right-0 z-30 mt-1 w-72 space-y-1 p-2 text-xs">
                {conversations.map((c) => (
                  <li key={c.id}>
                    <button className="w-full rounded px-2 py-1 text-left hover:bg-line" onClick={(e) => { (e.currentTarget.closest("details") as HTMLDetailsElement).open = false; onChat(c.kind, c.id); }}>
                      <span className="text-slate-500">{c.id} · {c.kind === "meeting" ? "reunião" : c.kind === "review" ? "revisão do PO" : "brainstorm"}</span>
                      <div className="truncate text-slate-100">{c.title || "(sem título)"}</div>
                      <div className="text-[10px] text-slate-500">{c.kind === "review" ? "pedido aguardando decisão" : `${c.cards} cards`} · {c.turns} mensagens</div>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
          <button className={`${kickoff ? "btn-primary" : "btn-ghost"} text-xs`}
            title={kickoff ? "Abertura da fábrica: visão, primeira versão e stack" : "Discutir uma ideia com a fábrica"}
            onClick={() => onChat("brainstorm")}>{kickoff ? <><Icon name="lightbulb" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Brainstorm de abertura</> : <><Icon name="lightbulb" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Brainstorm</>}</button>
          {/* a sprint starts only from a meeting, after the Product Owner's proposal (ADR-0017) */}
          <button className={`${kickoff ? "btn-ghost opacity-50" : "btn-primary"} text-xs`} disabled={kickoff}
            title={kickoff ? "Comece pelo brainstorm de abertura: ele decide a visão, a primeira versão e a stack" : "O Master abre com o estado do projeto; o Product Owner propõe o sprint"}
            onClick={() => onChat("meeting")}><Icon name="calendar" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />Reunião de Sprint</button>
        </div>
      </div>
      <div className="scroll-thin grid flex-1 grid-cols-6 gap-2 overflow-x-auto p-3">
        {columns.map((c) => (
          <div key={c.key} className={`flex min-w-[150px] flex-col rounded-md border-t-4 bg-ink/50 ${TONE[c.key] ?? ""}`}>
            <div className="flex items-center justify-between px-2 py-1 text-xs font-semibold text-slate-300">
              <span>{c.label}</span><span className="text-slate-500">{c.stories.filter((s) => showArchived || !s.archived).length}</span>
            </div>
            <div className="scroll-thin flex-1 space-y-1.5 overflow-y-auto px-2 pb-2 pt-1">
              {ordered(c).filter((s) => (!epicFilter || s.epic_id === epicFilter) && (showArchived || !s.archived)).map((s) =>
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
                    <Card s={s} deps={byId} epicName={s.epic_id ? epics[s.epic_id] : undefined} cap={storyCap} hl={hl} onHl={setHl} onOpen={onOpen} onUnpin={onUnpin} />
                  </div>
                ) : (
                  <div key={s.id} className={s.archived ? "opacity-60" : undefined}>
                    <Card s={s} deps={byId} epicName={s.epic_id ? epics[s.epic_id] : undefined} cap={storyCap} hl={hl} onHl={setHl} running={working(s, live, now, engine)} agent={AT_WORK.has(s.stage) ? holder(agents, s, latest(s.activity, live[s.id])) : null} activity={AT_WORK.has(s.stage) ? latest(s.activity, live[s.id]) : null} now={now} onOpen={onOpen} />
                  </div>
                ),
              )}
              {/* the archived cards of Concluído: a line at the end of the column, at its top when
                  no card is shown yet */}
              {c.key === "DONE" && c.stories.some((s) => s.archived) && (
                <button className="w-full rounded px-1 py-1 text-left text-[11px] text-slate-500 hover:text-slate-300" onClick={() => setShowArchived((v) => !v)}>
                  {showArchived ? "ocultar arquivadas" : `${c.stories.filter((s) => s.archived).length} arquivadas · mostrar`}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/** An approved story waiting behind another in the merge queue: nobody works on it until its
 * turn (ADR-0029). The head of the queue is being merged, so it is at work like any other. */
function behindInQueue(s: StoryCard): boolean {
  return (s.merge_queue?.position ?? 0) > 1;
}

/** A card is being worked on when it sits in a working stage and the factory gave a sign of
 * life on it in the last five minutes. Those cards rise to the top and get the amber ring. */
function working(s: StoryCard, live: Record<string, StoryActivity>, now: number, engine: boolean): boolean {
  // the belt stopped: nothing is running, however recent the last sign of life was
  if (!engine) return false;
  if (!AT_WORK.has(s.stage) || s.waiting?.gate === "wait" || behindInQueue(s)) return false;
  const a = latest(s.activity, live[s.id]);
  if (!a || a.stalled) return false;
  return (now - Date.parse(a.last_at)) / 1000 <= 300;
}

// one fixed glyph per kind, left of the id, like an issue-type icon (11.1)
const KIND_ICON: Record<string, { icon: IconName; label: string }> = {
  feature: { icon: "sparkles", label: "funcionalidade" }, bugfix: { icon: "bug", label: "correção" }, research: { icon: "search", label: "pesquisa" },
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

function Card({ s, agent, activity, now, running = false, cap = 0, hl = {}, onHl, onOpen, onUnpin, epicName, deps }: { deps?: Map<string, StoryCard>; epicName?: string; s: StoryCard; agent?: Agent | null; activity?: StoryActivity | null; now?: number; running?: boolean; cap?: number; hl?: { sprint?: string; ids?: string[] }; onHl?: (h: { sprint?: string; ids?: string[] }) => void; onOpen: (id: string) => void; onUnpin?: (id: string) => Promise<void> }) {
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
  if (s.merge_queue) {
    const first = s.merge_queue.position === 1;
    alerts.push({
      text: first ? "integrando" : `fila de merge · ${s.merge_queue.position}º`,
      cls: "bg-emerald-900/50 text-emerald-200",
      title: first
        ? "Aprovada: está sendo integrada à versão principal agora (atualizada com o que entrou depois e testada de novo)."
        : `Aprovada: entra na versão principal depois das ${s.merge_queue.position - 1} aprovadas antes dela, uma de cada vez, na ordem em que você aprovou.`,
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
  // what the card hangs on, in two tones. Closed padlock, solid: something really holds it (a
  // dependency missing, or its own delivery waiting behind another). Open padlock, outlined: it
  // is free to work — it only carries the branch of a story whose delivery you have not accepted
  // yet, or its dependencies are already delivered (11.5).
  const gate = open ? s.waiting : null;
  // the dependencies that still hold it: anything not delivered and not test-approved yet, plus
  // what the backend says is missing (the gate runs only once the card reaches planning)
  const dependsOn = open ? s.depends_on ?? [] : [];
  const unreleased = dependsOn.filter((d) => !released(deps?.get(d)));
  const held = gate ? [...new Set([...gate.gone, ...gate.pending, ...gate.chain, ...unreleased])] : [...new Set(unreleased)];
  const linked = !open || held.length ? [] : dependsOn;
  // a card that waits is not being worked on: the stage tag goes quiet and no Loompa holds it,
  // the same condition `working()` uses for the amber ring (11.5). `waiting.ready` is the card
  // that is finished and green and only waits for your acceptance of the delivery before it;
  // the others wait for a dependency that is not in the base yet.
  const parked = open && (s.waiting?.gate === "wait" || behindInQueue(s));
  const size = COMPLEXITY[s.complexity] ?? COMPLEXITY.STANDARD;
  // the pointer answers by lifting the card a little, never by changing a title's weight: this
  // card under the mouse, or a story someone's sprint tag / blocker chip is pointing at
  const pointed = !!hl.sprint && hl.sprint === s.sprint_id ? "sprint" : hl.ids?.includes(s.id) ? "block" : "";
  return (
    <div className={`group relative rounded-md border p-2 text-xs transition-transform duration-200 ease-out hover:scale-[1.03] ${pointed ? "scale-[1.03]" : ""} ${running ? "border-brand bg-brand/[0.07] ring-1 ring-brand running-glow" : "border-line bg-panel hover:border-slate-500"}`}>
      <div className="flex items-center gap-1 text-[10px] text-slate-500">
        <span className="text-[12px] leading-none" title={kind.label}><Icon name={kind.icon} className="h-3.5 w-3.5" /></span>
        <button className="hover:text-slate-300" onClick={() => onOpen(s.id)}>{s.id}</button>
        <span className={`chip px-1.5 py-0 text-[9px] ${size.cls}`} title={size.title}>{size.text}</span>
        {s.sprint_id && (
          <span className="cursor-default rounded bg-sky-950/70 px-1 text-sky-500/80 hover:text-sky-300" title={`Sprint ${s.sprint_id}: passe o mouse para destacar as histórias dele`}
            onMouseEnter={() => onHl?.({ sprint: s.sprint_id ?? undefined })} onMouseLeave={() => onHl?.({})}>{s.sprint_id}</span>
        )}
        {epicName && <span className="max-w-[7rem] truncate rounded bg-violet-950/70 px-1 text-violet-300/90" title={`Épico: ${epicName}`}>{epicName}</span>}
        <span className="ml-auto flex items-center gap-1">
          {pinned && onUnpin && (
            <button className="text-[11px] opacity-80 hover:opacity-100" title="Posição fixada por você: o Product Owner não move este card. Clique para devolver a ordem a ele." onClick={() => onUnpin(s.id)}><Icon name="pin" className="h-3 w-3" /></button>
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
        {alerts.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {alerts.map((a) => <span key={a.text} className={`chip ${a.cls}`} title={a.title}>{a.text}</span>)}
          </div>
        )}
        {(held.length > 0 || linked.length > 0) && (
          <div className="mt-1">
            <span
              className={`chip ${held.length ? "bg-red-900/70 text-red-200" : "border border-red-400/70 bg-transparent font-normal text-red-300"}`}
              title={held.length
                ? s.waiting?.label || `Esta história só segue depois que ${held.join(", ")} estiver concluída ou com teste aprovado`
                : s.waiting?.ready
                  ? `Pronta e verde: escrita sobre ${linked.join(", ")}. Só falta integrar, e isso espera o seu aceite daquela entrega.`
                  : `Livre para trabalhar: escrita sobre ${linked.join(", ")}. Só a integração espera o seu aceite daquela entrega.`}
              onMouseEnter={() => onHl?.({ ids: held.length ? held : linked })} onMouseLeave={() => onHl?.({})}>
              <Icon name={held.length ? "lock" : "unlock"} className="mr-1 inline h-3 w-3 align-[-1px]" />{(held.length ? held : linked).join(", ")}
            </span>
          </div>
        )}
        {s.tasks_total > 0 && (
          <div className="mt-1.5 flex items-center gap-1.5" title={`${s.tasks_done} de ${s.tasks_total} tarefas do plano concluídas`}>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, (100 * s.tasks_done) / s.tasks_total)}%` }} />
            </div>
            <span className="text-[10px] tabular-nums text-slate-400">{s.tasks_done}/{s.tasks_total}</span>
          </div>
        )}
        {/* what the Loompa says it is doing, right above its figure and aligned with it; it
            may take two lines and nothing below moves because of that */}
        {activity && !parked && (
          <div className="mt-1 break-words text-right text-[10px] text-slate-400" title={doing(activity).title}>
            {doing(activity).text}
            {/* the token counter never shares a line: it is the part that keeps moving */}
            {doing(activity).thinking && <div className="text-slate-500">{doing(activity).thinking}</div>}
          </div>
        )}
        {/* the stage tag in the bottom-left corner, the Loompa in the bottom-right one */}
        {(AT_WORK.has(s.stage) || agent || stalled) && (
          <div className="mt-1.5 flex items-end gap-1 text-[10px]">
            <Stage stage={s.stage} since={activity?.stage_since} now={now} parked={parked} ready={!!s.waiting?.ready} />
            <span className="flex-1" />
            {stalled ? (
              <span className="shrink-0 rounded bg-red-900/60 px-1 text-red-200" title="Nenhum sinal da fábrica há muito tempo: o Ops foi avisado"><Icon name="pause" className="h-3 w-3" /></span>
            ) : agent && !parked ? <Avatar agent={agent} tier={s.current_tier} model={activity?.model} /> : null}
          </div>
        )}
      </button>
    </div>
  );
}

/** The Loompa holding the card, with the tier and the model it is on. No life dot here: the
 * card's amber border already says which ones are being worked on right now (11.2). */
function Avatar({ agent, tier, model }: { agent: Agent; tier?: string; model?: string | null }) {
  const short = shortModel(model ?? "");
  return (
    // two lines of text with the figure standing beside them, as tall as both
    <span className="flex min-w-0 shrink-0 items-end gap-1.5 text-[10px] text-slate-300"
      title={`${agent.name} está com este card${tier ? ` · ${TIER_NAME[tier] ?? tier}` : ""}${model ? ` · ${model}` : ""}${agent.detail ? ` · ${agent.detail}` : ""}`}>
      <span className="flex min-w-0 flex-col items-end">
        <span className="whitespace-nowrap">{tier ? `(${TIER_SHORT[tier] ?? tier}) ` : ""}{agent.name.replace(/ Loompa$/, "")}</span>
        {short && <span className="break-all text-right text-slate-500">{short}</span>}
      </span>
      <LoompaFigure role={agent.role} lg={!!short} />
    </span>
  );
}

const TIER_SHORT: Record<string, string> = { tier1: "T1", tier2: "T2", tier3: "T3" };
const TIER_NAME: Record<string, string> = {
  tier1: "Tier 1 (os mais fortes)", tier2: "Tier 2 (custo-benefício)", tier3: "Tier 3 (gratuitos)",
};

/** The model as a name someone can read on a card: no provider, no date, no suffix. */
function shortModel(model: string): string {
  const name = (model.split(":").pop() ?? model).split("/").pop() ?? "";
  return name
    .replace(/^(claude|gpt|gemini|grok|llama|qwen|deepseek)-/i, (m) => m.toLowerCase())
    .replace(/-(\d{8}|latest|preview|exp)$/i, "")
    .slice(0, 18);
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
  const mine = base.story_id === s.id; // the row's model and detail are about this card only when the row is
  return { ...base, state: "WORKING", story_id: s.id, detail: mine ? base.detail : "", model: mine ? base.model : "" } as Agent;
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

function Stage({ stage, since: at, now, parked = false, ready = false }: { stage: string; since?: string | null; now?: number; parked?: boolean; ready?: boolean }) {
  const tag = STAGE_TAG[stage];
  if (!tag) return null;
  const inStage = now !== undefined ? since(at, now) : null;
  // while the card waits, the tag says that and nothing else: the colour of the etapa would
  // read as "a Loompa is on it", and none is (11.5)
  const shown = !parked
    ? tag
    : ready
      ? { text: "pronta", cls: "bg-emerald-500/15 text-emerald-300/80 ring-1 ring-emerald-500/30", title: "Pronta e verde: nenhum Loompa trabalha nela, só falta o seu aceite da entrega anterior" }
      : { text: tag.text, cls: "bg-slate-800 text-slate-400", title: "Parada nesta etapa: nenhum Loompa trabalha nela enquanto espera o que a segura" };
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span className={`chip px-1.5 py-0 text-[9px] ${shown.cls}`} title={shown.title}>{shown.text}</span>
      {inStage !== null && <span className="tabular-nums text-slate-400" title={`Nesta etapa há ${ago(inStage)}`}>{ago(inStage)}</span>}
    </span>
  );
}

/** What the Loompa is doing right now, in its own words whenever it said them ("alinhando
 * critérios ao pedido de ajustes", "abrindo o produto no navegador"). The generic word for the
 * event type is only the last resort. No clocks here — the stage tag carries the time. */
function doing(a: StoryActivity): { text: string; thinking: string; title: string } {
  const target = a.last_target ? ` ${a.last_target.split("/").pop()}` : "";
  // a streamed call still writing: how much it has written so far (ADR-0016). It goes on a
  // line of its own, because it is the one number that changes every second.
  const thinking = a.thinking ? `pensando · ${tokens(a.thinking)} tokens` : "";
  if (a.task) {
    // "T3 (6)": the task and the tool calls it has made so far, then the one in progress
    const tool = a.last_tool ? `${DOING[a.last_tool] ?? a.last_tool}${target}` : "";
    return {
      text: `T${a.task} (${a.calls ?? 0})${tool ? ` · ${tool}` : ""}`,
      thinking,
      title: `${a.task_text ? `T${a.task}: ${a.task_text}\n` : ""}(${a.calls ?? 0}) ferramentas usadas nesta tarefa`,
    };
  }
  // the Worker's own detail is "T3: texto da tarefa", already said by the branch above
  const said = a.detail && !/^T\d+:/.test(a.detail) ? a.detail : "";
  const text = said || (thinking ? "" : HAPPENING[a.last_event] || "trabalhando");
  return { text, thinking, title: [text, thinking].filter(Boolean).join(" · ") };
}
