import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatReply, Consultant, Conversation, ConversationKind, DraftItem, MeetingMode, Opinion, SprintContext } from "../types";
import { Modal } from "./Modal";

const COPY: Record<ConversationKind, { title: string; hint: string; placeholder: string; agent: string }> = {
  meeting: {
    title: "🗓 Reunião de Sprint com o Master Loompa",
    hint: "O Master abre com o estado do projeto. Conte o que você quer fazer; quando o rascunho estiver bom, o Product Owner propõe o sprint e você aprova ou ajusta antes de começar.",
    placeholder: "Ex.: Hoje quero a recuperação de senha pronta; refatorar o webhook de cobrança; e um relatório mensal em CSV.",
    agent: "Master Loompa",
  },
  brainstorm: {
    title: "💡 Brainstorm com o Master Loompa",
    hint: "Pense em voz alta. O Master chama o Analyst ou o Architect quando a ideia pede, e os pareceres voltam preliminares. Aprove o rumo (1º OK), o Product Owner propõe os cards e você grava (2º OK).",
    placeholder: "Ex.: Como podemos tornar o primeiro acesso dos clientes mais simples?",
    agent: "Master Loompa",
  },
  review: {
    title: "📋 Revisão do pedido com o Product Owner",
    hint: "O Product Owner não gravou o card. Esclareça ou corrija o pedido e ele revê; se mesmo assim quiser o card, a palavra final é sua.",
    placeholder: "Explique melhor o pedido…",
    agent: "Product Owner Loompa",
  },
};

const KIND_LABEL: Record<string, string> = { bugfix: "correção", research: "pesquisa", feature: "funcionalidade" };
const STAGE_LABEL: Record<string, string> = {
  BACKLOG: "backlog", SPEC: "especificação", PLAN: "plano", DEV: "desenvolvimento", TEST: "testes", REVIEW: "revisão",
  AWAITING_FOUNDER: "aguardando você", DONE: "concluída", CANCELLED: "cancelada",
};

// api.ts throws `${status} ${body}`; the body is FastAPI's {"detail": "..."}.
function detail(e: unknown): string {
  const raw = String(e instanceof Error ? e.message : e);
  const json = raw.slice(raw.indexOf("{"));
  try { return String(JSON.parse(json).detail ?? raw); } catch { return raw; }
}

export default function ChatModal({ slug, kind, resumeId, initialText, onBrainstorm, onClose }: {
  slug: string; kind: ConversationKind; resumeId?: string; initialText?: string;
  onBrainstorm?: (text: string) => void; onClose: () => void;
}) {
  const [conv, setConv] = useState<Conversation | null>(null);
  const [sprints, setSprints] = useState<SprintContext | null>(null);
  const [text, setText] = useState(initialText ?? "");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [waitingFor, setWaitingFor] = useState<string | null>(null); // who is thinking right now
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [consultants, setConsultants] = useState<Consultant[]>([]);
  const [recording, setRecording] = useState(false);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const opening = useRef(false);

  const current: ConversationKind = conv?.kind ?? kind;
  const copy = COPY[current];
  const isMeeting = current === "meeting";
  const isReview = current === "review";
  const open = !conv || conv.status === "open";
  const items = conv?.draft.items ?? [];
  const inSprint = items.filter((i) => i.in_sprint);
  const proposal = conv?.draft.proposal ?? null;
  const running = sprints?.running ?? null;
  const planned = sprints?.planned ?? null;
  // with a sprint running the founder says what the meeting is about first (ADR-0018)
  const choosing = isMeeting && !!conv && conv.mode === null && conv.status === "open";
  const isCurrent = isMeeting && conv?.mode === "current";
  const members = conv?.draft.members ?? [];
  const joining = isCurrent ? inSprint.filter((i) => !i.story_id || !members.includes(i.story_id)) : inSprint;
  // the sprint starts (or joins) only after the Product Owner's proposal, and only with cards it saw
  const unseen = proposal ? joining.filter((i) => !proposal.keys.includes(i.key)).map((i) => i.key) : joining.map((i) => i.key);
  const reviewed = !!proposal && unseen.length === 0;
  const canStart = reviewed && inSprint.length > 0 && !running;
  const canPlan = reviewed && inSprint.length > 0;
  const canApply = joining.length === 0 || reviewed;
  const reviewingPlanned = isMeeting && !isCurrent && !running && !!planned && conv?.draft.sprint_id === planned.id;
  const founderTurns = conv?.turns.filter((t) => t.who === "founder").length ?? 0;
  const review = conv?.draft.review ?? null;

  useEffect(() => {
    if (resumeId) {
      api.conversation(slug, resumeId).then((r) => { setConv(r.conversation); if (r.sprints) setSprints(r.sprints); if (r.consultants) setConsultants(r.consultants); }).catch((e) => setErr(detail(e)));
    } else if (kind === "meeting" && !opening.current) {
      // the Master opens the meeting with where the project stands (plan 10.2)
      opening.current = true;
      setBusy(true); setWaitingFor("Master Loompa está preparando o parecer");
      api.openConversation(slug, "meeting", "").then((r) => { setConv(r.conversation); if (r.sprints) setSprints(r.sprints); }).catch((e) => setErr(detail(e))).finally(() => { setBusy(false); setWaitingFor(null); });
    }
  }, [slug, resumeId, kind]);
  useEffect(() => { setGoal(conv?.draft.goal ?? ""); }, [conv?.draft.goal]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [conv?.turns.length, pending]);

  const apply = (r: ChatReply) => {
    setConv(r.conversation);
    if (r.sprints) setSprints(r.sprints);
    if (r.consultants) setConsultants(r.consultants);
    setNotes(r.turn?.ignored ?? r.report?.ignored ?? []);
  };
  const run = async (fn: () => Promise<ChatReply>, who: string | null = null): Promise<boolean> => {
    setBusy(true); setErr(null); setWaitingFor(who);
    try { apply(await fn()); return true; } catch (e) { setErr(detail(e)); return false; } finally { setBusy(false); setWaitingFor(null); }
  };

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setText(""); setPending(t);
    const ok = await run(() => (conv ? api.say(slug, conv.id, t) : api.openConversation(slug, kind, t)), `${copy.agent} está pensando`);
    setPending(null);
    if (!ok) setText(t);
  };
  const edit = (ops: Record<string, unknown>[]) => conv && run(() => api.editDraft(slug, conv.id, ops));
  // Saved quietly (no `busy`): a blur must not disable the button the founder is about to click.
  const saveGoal = () => {
    if (!open || !conv || goal === conv.draft.goal) return;
    api.editDraft(slug, conv.id, [{ op: "goal", text: goal }]).then(apply).catch((e) => setErr(detail(e)));
  };
  const propose = () => conv && run(() => api.propose(slug, conv.id), "O Product Owner está montando a proposta");
  const commit = (start: boolean, force = false, planNext = false) => conv && run(() => api.commit(slug, conv.id, { start_sprint: start, plan_next: planNext, goal, run: true, force }), start ? "Começando o sprint" : isCurrent ? "Aplicando as mudanças no sprint" : null);
  const choose = (mode: MeetingMode) => conv && run(() => api.chooseMode(slug, conv.id, mode));
  const discard = async () => {
    if (!conv || !window.confirm(isReview ? "Desistir deste pedido? Nada será gravado." : "Descartar esta conversa? Nada do rascunho será salvo.")) return;
    if (await run(() => api.discard(slug, conv.id))) onClose();
  };
  // a meeting opened and left before the founder said anything is not worth keeping
  const close = () => {
    if (conv && conv.kind === "meeting" && conv.status === "open" && founderTurns === 0) api.discard(slug, conv.id).catch(() => {});
    onClose();
  };
  // a brainstorm (ADR-0020): the founder asks a role directly, with the text box as the question
  const consult = async (c: Consultant) => {
    const q = text.trim();
    if (!q || busy) return;
    const opened = conv ?? await api.openConversation(slug, "brainstorm", "").then((r) => { apply(r); return r.conversation; }).catch((e) => { setErr(detail(e)); return null; });
    if (!opened) return;
    setText(""); setPending(`Parecer do ${c.role}: ${q}`);
    const ok = await run(() => api.consult(slug, opened.id, c.role, q), `O ${c.role[0].toUpperCase()}${c.role.slice(1)} está preparando o parecer`);
    setPending(null);
    if (!ok) setText(q);
  };
  const approve = () => conv && run(() => api.approve(slug, conv.id), "O Product Owner está dividindo o rumo em cards");
  const reopen = () => conv && run(() => api.reopen(slug, conv.id));
  const editSplit = (ops: Record<string, unknown>[]) => conv && run(() => api.editDraft(slug, conv.id, ops, true));
  const toBrainstorm = async () => {
    if (!conv) return;
    const said = conv.turns.filter((t) => t.who === "founder").map((t) => t.text).join("\n");
    if (await run(() => api.discard(slug, conv.id))) onBrainstorm?.(said);
  };

  const toggleRecord = async () => {
    if (recording) { rec.current?.stop(); setRecording(false); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => chunks.current.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setBusy(true);
        try {
          const { text: heard } = await api.transcribe(slug, new Blob(chunks.current, { type: "audio/webm" }));
          setText((g) => (g ? `${g}\n${heard}` : heard));
        } catch { setErr("Ditado por voz indisponível: instale loompa-core[voice]."); } finally { setBusy(false); }
      };
      mr.start();
      rec.current = mr;
      setRecording(true);
    } catch { setErr("Microfone não autorizado."); }
  };

  const done = conv && conv.status !== "open";
  const startHint = running && !isCurrent ? `O ${running.id} ainda está rodando: só um sprint por vez`
    : !proposal ? "Peça a proposta do Product Owner antes de começar"
    : inSprint.length === 0 ? "Marque ao menos um card para o sprint"
    : unseen.length ? `${unseen.join(", ")} entrou depois da proposta: peça uma nova`
    : undefined;
  const applyHint = canApply ? undefined
    : !proposal ? "O Product Owner avalia o que entra no sprint: peça a avaliação dele"
    : `${unseen.join(", ")} entrou depois da avaliação: peça uma nova`;
  return (
    <Modal wide title={copy.title} onClose={close}>
      <p className="text-xs text-slate-400">{copy.hint}</p>
      {conv?.limits.map((l, i) => <p key={i} className="mt-2 rounded-md border border-amber-700/60 bg-amber-900/30 px-2 py-1 text-xs text-amber-100">{l}</p>)}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
          <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3 text-sm">
            {!conv && !pending && !busy && <p className="text-slate-500">{copy.placeholder}</p>}
            {conv?.turns.map((t, i) => <Bubble key={i} who={t.who} name={t.name || copy.agent} text={t.text} changes={t.changes} />)}
            {pending && <Bubble who="founder" name="" text={pending} changes={[]} />}
            {busy && waitingFor && <p className="text-xs text-slate-500">{waitingFor}…</p>}
            <div ref={endRef} />
          </div>
          {notes.length > 0 && <div className="border-t border-line px-3 py-1 text-[11px] text-amber-300">{notes.map((n, i) => <div key={i}>! {n}</div>)}</div>}
          {open ? (
            <div className="border-t border-line p-2">
              <textarea
                value={text} rows={3} disabled={(busy && !!pending) || choosing}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder={choosing ? "Escolha ao lado do que trata a reunião" : "Escreva e tecle Enter (Shift+Enter quebra a linha)"}
                className="w-full resize-none rounded-md border border-line bg-ink p-2 text-sm"
              />
              <div className="mt-1 flex items-center gap-2">
                <button className={recording ? "btn bg-red-600 text-white" : "btn-ghost"} onClick={toggleRecord} disabled={busy}>{recording ? "⏹ Parar ditado" : "🎙 Ditar"}</button>
                <div className="mx-auto" />
                <button className="btn-primary" disabled={busy || !text.trim() || choosing} onClick={send}>Enviar</button>
              </div>
            </div>
          ) : (
            <div className="border-t border-line p-3 text-right"><button className="btn-primary" onClick={onClose}>Fechar</button></div>
          )}
        </section>

        {current === "brainstorm" ? (
          <BrainstormPanel conv={conv} consultants={consultants} busy={busy} open={open} question={text.trim()}
            onConsult={consult} onEdit={(ops) => edit(ops)} onEditSplit={editSplit} onApprove={approve} onReopen={reopen}
            onCommit={() => commit(false)} onDiscard={discard} />
        ) : isReview ? (
          <ReviewPanel conv={conv} review={review} busy={busy} open={open} founderTurns={founderTurns}
            onForce={() => commit(false, true)} onBrainstorm={onBrainstorm ? toBrainstorm : undefined} onDiscard={discard} />
        ) : choosing && running ? (
          <section className="flex h-[52vh] flex-col justify-center gap-3 rounded-md border border-line bg-ink/40 p-4">
            <h4 className="text-sm font-semibold">Do que trata esta reunião?</h4>
            <button className="btn-primary text-left" disabled={busy} onClick={() => choose("current")}>
              🔧 Ajustar o {running.id} em andamento
              <span className="mt-0.5 block text-[11px] font-normal opacity-80">tirar ou incluir cards, recomeçar uma história, cancelar o sprint; discutir alternativas, dependências e bloqueios</span>
            </button>
            <button className="btn-ghost text-left" disabled={busy} onClick={() => choose("next")}>
              🗓 Pré-montar o próximo sprint <span className="text-amber-300">(não recomendado)</span>
              <span className="mt-0.5 block text-[11px] font-normal text-slate-400">ele espera o {running.id} terminar e começa por uma nova reunião; o que este sprint ensinar ainda não entrou</span>
            </button>
            <button className="text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={discard}>Descartar conversa</button>
          </section>
        ) : (
          <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <h4 className="text-sm font-semibold">
                {isCurrent ? `${conv?.draft.sprint_id} em andamento` : isMeeting ? (running ? "Próximo sprint (fica montado)" : "Rascunho do backlog e do sprint") : "Rascunho de ideias"}
              </h4>
              <span className="text-[11px] text-slate-500">{items.length} cards{isMeeting ? ` · ${inSprint.length} no sprint` : ""}</span>
            </div>
            {reviewingPlanned && planned && open && (
              <div className="mx-3 mt-2 rounded-md border border-sky-700/60 bg-sky-900/30 px-2 py-1.5 text-xs text-sky-100">
                O {planned.id} está montado com {planned.story_ids.length} {planned.story_ids.length === 1 ? "card" : "cards"} e espera você: revise com o Product Owner e inicie.
              </div>
            )}
            {isCurrent && conv?.draft.cancel_sprint && (
              <div className="mx-3 mt-2 rounded-md border border-red-700/60 bg-red-900/30 px-2 py-1.5 text-xs text-red-100">
                O {conv.draft.sprint_id} será cancelado ao aplicar: as histórias não concluídas voltam ao backlog.
              </div>
            )}
            {isMeeting && !isCurrent && (
              <input
                value={goal} disabled={!open || !conv} placeholder="Meta do sprint (uma frase)"
                onChange={(e) => setGoal(e.target.value)}
                onBlur={saveGoal}
                className="mx-3 mt-2 rounded-md border border-line bg-ink px-2 py-1 text-xs"
              />
            )}
            <ul className="scroll-thin flex-1 space-y-1.5 overflow-y-auto p-3">
              {items.length === 0 && <li className="text-xs text-slate-500">O rascunho está vazio. Conte suas ideias na conversa.</li>}
              {items.map((i) => <Item key={i.key} item={i} sprint={isMeeting} member={isCurrent && !!i.story_id && members.includes(i.story_id)} editable={open && !busy && !conv?.draft.cancel_sprint} onEdit={edit} />)}
            </ul>
            {open && conv && isCurrent && (
              <div className="space-y-1 border-t border-line p-2">
                <button className="btn-ghost w-full" disabled={busy || joining.length === 0 || !!conv.draft.cancel_sprint} title="O Product Owner avalia os cards que entrariam no sprint em andamento" onClick={propose}>
                  📋 Pedir a avaliação do Product Owner{joining.length ? ` (${joining.length} entrando)` : ""}
                </button>
                <div className="flex gap-2">
                  <button className={conv.draft.cancel_sprint ? "btn-ghost flex-1" : "btn flex-1 border border-red-800 text-red-200 hover:bg-red-950"} disabled={busy}
                    onClick={() => { if (conv.draft.cancel_sprint) edit([{ op: "keep_sprint" }]); else if (window.confirm(`Cancelar o ${conv.draft.sprint_id} ao aplicar? As histórias não concluídas voltam ao backlog.`)) edit([{ op: "cancel_sprint", reason: "decisão do Founder na reunião" }]); }}>
                    {conv.draft.cancel_sprint ? "Manter o sprint" : "Cancelar o sprint"}
                  </button>
                  <button className="btn-primary flex-1" disabled={busy || !canApply} title={applyHint} onClick={async () => { if (await commit(false)) onClose(); }}>Aplicar no sprint</button>
                </div>
                {applyHint && <p className="text-center text-[11px] text-slate-500">{applyHint}</p>}
                <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={discard}>Descartar conversa (nada muda)</button>
              </div>
            )}
            {open && conv && !isCurrent && (
              <div className="space-y-1 border-t border-line p-2">
                {isMeeting && (
                  <button className="btn-ghost w-full" disabled={busy || items.length === 0} title="O Product Owner propõe o que entra no sprint, em que ordem e como os cards se relacionam" onClick={propose}>
                    📋 {reviewingPlanned && planned ? `Revisar o ${planned.id} com o Product Owner` : proposal ? "Pedir nova proposta ao Product Owner" : "Pedir a proposta do Product Owner"}
                  </button>
                )}
                <div className="flex gap-2">
                  {isMeeting ? (
                    <>
                      <button className="btn-ghost flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Salvar no backlog</button>
                      {running ? (
                        <button className="btn-primary flex-1" disabled={busy || !canPlan} title={canPlan ? `Fica montado e espera o ${running.id} terminar` : startHint} onClick={async () => { if (await commit(false, false, true)) onClose(); }}>Salvar como próximo sprint</button>
                      ) : (
                        <button className="btn-primary flex-1" disabled={busy || !canStart} title={startHint} onClick={async () => { if (await commit(true)) onClose(); }}>{reviewingPlanned && planned ? `Iniciar ${planned.id} ▶` : "Começar Sprint ▶"}</button>
                      )}
                    </>
                  ) : (
                    <button className="btn-primary flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Enviar ao backlog (o Product Owner admite)</button>
                  )}
                </div>
                {isMeeting && !(running ? canPlan : canStart) && startHint && items.length > 0 && <p className="text-center text-[11px] text-slate-500">{startHint}</p>}
                <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={discard}>Descartar conversa</button>
              </div>
            )}
            {done && conv?.result.created && !isCurrent && <p className="border-t border-line p-3 text-xs text-emerald-300">✔ {conv.result.created.length === 1 ? "1 nova história" : `${conv.result.created.length} novas histórias`}{conv.result.sprint_id ? ` · ${conv.result.sprint_id}` : ""}.</p>}
          </section>
        )}
      </div>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </Modal>
  );
}

const ROLE_LABEL: Record<string, string> = { analyst: "Analyst", architect: "Architect" };

/** A brainstorm (ADR-0020): the direction and the ideas while it is a conversation, the opinions
 *  the roles gave, and, after the first OK, the Product Owner's split waiting for the second. */
function BrainstormPanel({ conv, consultants, busy, open, question, onConsult, onEdit, onEditSplit, onApprove, onReopen, onCommit, onDiscard }: {
  conv: Conversation | null; consultants: Consultant[]; busy: boolean; open: boolean; question: string;
  onConsult: (c: Consultant) => void; onEdit: (ops: Record<string, unknown>[]) => void; onEditSplit: (ops: Record<string, unknown>[]) => void;
  onApprove: () => void; onReopen: () => void; onCommit: () => void; onDiscard: () => void;
}) {
  const draft = conv?.draft;
  const ideas = draft?.items ?? [];
  const split = draft?.split ?? null;
  const opinions = draft?.consults ?? [];
  const editable = open && !busy;
  const done = conv && conv.status !== "open";
  return (
    <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <h4 className="text-sm font-semibold">{split ? "Proposta de cards do Product Owner" : "Rumo da ideia"}</h4>
        <span className="text-[11px] text-slate-500">{split ? `${split.length} cards` : `${ideas.length} ${ideas.length === 1 ? "ideia" : "ideias"} · ${opinions.length} ${opinions.length === 1 ? "parecer" : "pareceres"}`}</span>
      </div>
      <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3 text-xs">
        {split ? (
          <>
            <p className="rounded-md border border-sky-700/60 bg-sky-900/30 px-2 py-1.5 text-sky-100">
              Você aprovou o rumo. Confira os cards: mude a prioridade ou tire um; nada vai ao backlog até você gravar.
            </p>
            <ul className="space-y-1.5">
              {split.map((c) => <SplitCard key={c.key} item={c} editable={editable} onEdit={onEditSplit} />)}
            </ul>
            {ideas.some((i) => i.note) && (
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Ficam de fora (continuam aqui)</div>
                {ideas.filter((i) => i.note).map((i) => <div key={i.key} className="text-amber-300">{i.key} · {i.title}: {i.note}</div>)}
              </div>
            )}
          </>
        ) : (
          <>
            <div className={`rounded-md border p-2 ${draft?.ready ? "border-emerald-700/70 bg-emerald-950/30" : "border-line bg-panel"}`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Rumo{draft?.ready ? " · o Master acha que está claro" : ""}</div>
              <div className="mt-0.5 whitespace-pre-wrap text-slate-100">{draft?.direction || <span className="text-slate-500">Ainda sem rumo. Conte a ideia na conversa.</span>}</div>
            </div>
            <ul className="space-y-1.5">
              {ideas.length === 0 && <li className="text-slate-500">Nenhuma ideia no rascunho ainda.</li>}
              {ideas.map((i) => <Item key={i.key} item={i} sprint={false} editable={editable} onEdit={onEdit} />)}
            </ul>
            {opinions.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Pareceres (preliminares)</div>
                {opinions.map((o) => <OpinionCard key={o.key} o={o} editable={editable} onEdit={onEdit} />)}
              </div>
            )}
          </>
        )}
        {done && conv?.result && (conv.result.created?.length || conv.result.amended?.length) ? (
          <p className="text-emerald-300">✔ {[...(conv.result.created ?? []), ...(conv.result.amended ?? [])].join(", ")} no backlog.</p>
        ) : null}
      </div>
      {open && (
        <div className="space-y-1 border-t border-line p-2">
          {!split && consultants.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 text-[11px]">
              <span className="text-slate-500">Pedir parecer:</span>
              {consultants.map((c) => (
                <button key={c.role} className="btn-ghost px-2 py-0.5 text-[11px]" disabled={busy || !question}
                  title={question ? `${ROLE_LABEL[c.role] ?? c.role}: ${c.label}. A pergunta é o texto da caixa ao lado.` : "Escreva a pergunta na caixa de mensagem"}
                  onClick={() => onConsult(c)}>{ROLE_LABEL[c.role] ?? c.role}</button>
              ))}
            </div>
          )}
          {split ? (
            <div className="flex gap-2">
              <button className="btn-ghost flex-1" disabled={busy} onClick={onReopen}>↩ Voltar à conversa</button>
              <button className="btn-primary flex-1" disabled={busy || split.length === 0} title="2º OK: o Product Owner grava os cards no backlog" onClick={onCommit}>✅ Gravar no backlog</button>
            </div>
          ) : (
            <button className={`w-full ${draft?.ready ? "btn-primary" : "btn-ghost"}`} disabled={busy || !conv || (ideas.length === 0 && !draft?.direction)}
              title="1º OK: o rumo fica aprovado e o Product Owner propõe os cards" onClick={onApprove}>✅ Aprovar o rumo</button>
          )}
          <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy || !conv} onClick={onDiscard}>Descartar conversa</button>
        </div>
      )}
    </section>
  );
}

function OpinionCard({ o, editable, onEdit }: { o: Opinion; editable: boolean; onEdit: (ops: Record<string, unknown>[]) => void }) {
  const [openDetails, setOpenDetails] = useState(false);
  return (
    <div className={`rounded-md border border-line bg-panel p-2 ${o.dismissed || o.failed ? "opacity-60" : ""}`}>
      <div className="flex items-start gap-2">
        <button className="min-w-0 flex-1 text-left" onClick={() => setOpenDetails(!openDetails)}>
          <div className="text-[10px] text-slate-500">{o.key} · {o.name}{o.asked_by === "founder" ? " · pedido por você" : ""}{o.dismissed ? " · deixado de lado" : ""}{o.failed ? " · não respondeu" : ""}</div>
          <div className="text-slate-200">{o.failed ? "Não foi possível ouvir este papel agora." : o.summary}</div>
        </button>
        {!o.failed && (
          <button className="text-[10px] text-slate-500 hover:text-amber-300 disabled:opacity-40" disabled={!editable}
            title={o.dismissed ? "Trazer de volta à conversa" : "Deixar de lado: o Master para de considerar"}
            onClick={() => onEdit([{ op: o.dismissed ? "restore" : "dismiss", ref: o.key }])}>{o.dismissed ? "retomar" : "deixar de lado"}</button>
        )}
      </div>
      {openDetails && !o.failed && (
        <div className="mt-1 space-y-0.5 border-t border-white/10 pt-1 text-slate-400">
          <div><span className="text-slate-500">Pergunta:</span> {o.question}</div>
          {o.attention.length > 0 && <div><span className="text-slate-500">Atenção:</span> {o.attention.join("; ")}</div>}
          {o.cost && <div><span className="text-slate-500">Custo:</span> {o.cost}</div>}
          {o.benefit && <div><span className="text-slate-500">Benefício:</span> {o.benefit}</div>}
          {o.counterpoints.length > 0 && <div className="text-amber-300"><span className="text-slate-500">Contrapontos:</span> {o.counterpoints.join("; ")}</div>}
          {o.sources.filter((u) => u.startsWith("http")).map((u) => <a key={u} href={u} target="_blank" rel="noreferrer" className="block truncate text-sky-300 underline">{u}</a>)}
        </div>
      )}
    </div>
  );
}

function SplitCard({ item, editable, onEdit }: { item: DraftItem; editable: boolean; onEdit: (ops: Record<string, unknown>[]) => void }) {
  return (
    <li className="rounded-md border border-line bg-panel p-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
            <span>{item.key}</span>
            {item.amend ? <span className="chip bg-sky-900/50 text-sky-200">acrescenta a {item.story_id}</span>
              : item.story_id ? <span className="chip bg-slate-800 text-slate-300">já é {item.story_id}</span>
              : <span className="chip bg-emerald-900/50 text-emerald-200">card novo</span>}
            {item.kind && item.kind !== "feature" && <span className="chip bg-fuchsia-900/50 text-fuchsia-200">{KIND_LABEL[item.kind] ?? item.kind}</span>}
            {item.epic && <span className="chip bg-slate-800 text-slate-400">{item.epic}</span>}
            {item.ideas && item.ideas.length > 0 && <span>de {item.ideas.join(", ")}</span>}
            {!!item.depends_on?.length && <span className="chip bg-indigo-900/50 text-indigo-200">↳ depende de {item.depends_on.join(", ")}</span>}
          </div>
          <div className="font-medium leading-snug text-slate-100">{item.title}</div>
          {(item.amend || item.description) && <div className="mt-0.5 line-clamp-3 text-slate-400">{item.amend || item.description}</div>}
          {item.note && <div className="mt-1 text-amber-300">{item.note}</div>}
        </div>
        <select value={item.priority} disabled={!editable} title="Prioridade (1 = primeiro)" onChange={(e) => onEdit([{ op: "update", ref: item.key, priority: Number(e.target.value) }])} className="rounded border border-line bg-ink px-1 py-0.5 text-[11px]">
          {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>P{p}</option>)}
        </select>
        <button className="text-slate-500 hover:text-red-300 disabled:opacity-40" title="Tirar da proposta (as ideias dele continuam no brainstorm)" disabled={!editable} onClick={() => onEdit([{ op: "drop", ref: item.key }])}>✕</button>
      </div>
    </li>
  );
}

/** A refused quick story: the card as the Product Owner would file it, why it did not, and the
 *  founder's ways out (clarify in the chat, file it anyway, take it to a brainstorm, give up). */
function ReviewPanel({ conv, review, busy, open, founderTurns, onForce, onBrainstorm, onDiscard }: {
  conv: Conversation | null; review: Conversation["draft"]["review"]; busy: boolean; open: boolean; founderTurns: number;
  onForce: () => void; onBrainstorm?: () => void; onDiscard: () => void;
}) {
  const item = conv?.draft.items[0];
  const filed = conv?.result.created?.[0] ?? conv?.result.existing?.[0];
  return (
    <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
      <div className="border-b border-line px-3 py-2"><h4 className="text-sm font-semibold">O card como o Product Owner gravaria</h4></div>
      <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3 text-xs">
        {item ? (
          <div className="rounded-md border border-line bg-panel p-2">
            <div className="flex flex-wrap gap-1 text-[10px]">
              {review?.kind && <span className="chip bg-fuchsia-900/50 text-fuchsia-200">{KIND_LABEL[review.kind] ?? review.kind}</span>}
              {item.epic && <span className="chip bg-slate-800 text-slate-400">{item.epic}</span>}
            </div>
            <div className="mt-1 font-medium text-slate-100">{item.title}</div>
            {item.description && <div className="mt-0.5 whitespace-pre-wrap text-slate-400">{item.description}</div>}
          </div>
        ) : <p className="text-slate-500">carregando…</p>}
        {review && !review.admit && open && <p className="rounded-md border border-amber-700/60 bg-amber-900/30 px-2 py-1 text-amber-100">{review.reason}</p>}
        {filed && <p className="text-emerald-300">✔ {filed} no backlog.</p>}
      </div>
      {open && conv && (
        <div className="space-y-1 border-t border-line p-2">
          {review?.reason_code === "too_big" && onBrainstorm && (
            <button className="btn-ghost w-full" disabled={busy} onClick={onBrainstorm}>💡 Levar ao Brainstorm</button>
          )}
          <button className="btn-primary w-full" disabled={busy || founderTurns < 2 || !review || review.admit}
            title={founderTurns < 2 ? "Antes, diga ao Product Owner por que o card deve entrar" : "A objeção do Product Owner fica registrada no card"} onClick={onForce}>
            Gravar mesmo assim
          </button>
          <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={onDiscard}>Desistir do pedido</button>
        </div>
      )}
    </section>
  );
}

function Bubble({ who, name, text, changes }: { who: "founder" | "agent"; name: string; text: string; changes: string[] }) {
  const mine = who === "founder";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] rounded-lg px-3 py-2 ${mine ? "bg-brand/20 text-amber-50" : "bg-slate-800 text-slate-100"}`}>
        {!mine && <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">{name}</div>}
        <div className="whitespace-pre-wrap">{text}</div>
        {changes.length > 0 && <ul className="mt-1 space-y-0.5 border-t border-white/10 pt-1 text-[11px] text-emerald-300">{changes.map((c, i) => <li key={i}>• {c}</li>)}</ul>}
      </div>
    </div>
  );
}

function Item({ item, sprint, member = false, editable, onEdit }: { item: DraftItem; sprint: boolean; member?: boolean; editable: boolean; onEdit: (ops: Record<string, unknown>[]) => void }) {
  // a card of the running sprint: unchecking takes it out (back to the backlog); ↺ starts it over
  const leaving = member && !item.in_sprint;
  return (
    <li className={`rounded-md border bg-panel p-2 text-xs ${leaving ? "border-red-900/70 opacity-70" : item.restart ? "border-amber-700/70" : "border-line"}`}>
      <div className="flex items-start gap-2">
        {sprint && <input type="checkbox" className="mt-1" title={member ? "No sprint (desmarque para tirar e devolver ao backlog)" : "No sprint"} checked={item.in_sprint} disabled={!editable} onChange={(e) => onEdit([{ op: "update", ref: item.key, in_sprint: e.target.checked }])} />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
            <span>{item.key}</span>
            {member && item.stage && <span className="chip bg-slate-800 text-slate-300">{STAGE_LABEL[item.stage] ?? item.stage}</span>}
            {leaving && <span className="chip bg-red-900/50 text-red-200">sai do sprint</span>}
            {item.restart && <span className="chip bg-amber-900/60 text-amber-200" title={item.restart_reason || undefined}>recomeça do zero</span>}
            {item.story_id && !member && <span className="chip bg-sky-900/50 text-sky-200">já no backlog</span>}
            {item.epic && <span className="chip bg-slate-800 text-slate-400">{item.epic}</span>}
            {!!item.depends_on?.length && (
              <button className="chip bg-indigo-900/50 text-indigo-200 hover:bg-indigo-800/60 disabled:opacity-60" disabled={!editable}
                title="Depende destes cards: o plano espera eles serem entregues. Clique para tirar a dependência."
                onClick={() => onEdit([{ op: "update", ref: item.key, depends_on: [] }])}>↳ depende de {item.depends_on.join(", ")} ✕</button>
            )}
            {item.unpin && <span className="chip bg-slate-800 text-slate-400" title="A posição fixada volta ao Product Owner ao salvar">solta o 📌</span>}
          </div>
          <div className="font-medium leading-snug text-slate-100">{item.title}</div>
          {item.description && !item.story_id && <div className="mt-0.5 line-clamp-2 text-slate-400">{item.description}</div>}
          {item.note && <div className="mt-1 text-amber-300">{item.note}</div>}
        </div>
        <select value={item.priority} disabled={!editable} title="Prioridade (1 = urgente)" onChange={(e) => onEdit([{ op: "update", ref: item.key, priority: Number(e.target.value) }])} className="rounded border border-line bg-ink px-1 py-0.5 text-[11px]">
          {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>P{p}</option>)}
        </select>
        {member ? (
          <button className={`disabled:opacity-40 ${item.restart ? "text-amber-300" : "text-slate-500 hover:text-amber-300"}`} title={item.restart ? "Não recomeçar" : "Recomeçar do zero (descarta spec, plano e código)"} disabled={!editable || leaving}
            onClick={() => onEdit([{ op: "restart", ref: item.key, restart: !item.restart, reason: item.restart ? "" : "pedido do Founder na reunião" }])}>↺</button>
        ) : (
          <button className="text-slate-500 hover:text-red-300 disabled:opacity-40" title="Tirar do rascunho" disabled={!editable} onClick={() => onEdit([{ op: "drop", ref: item.key }])}>✕</button>
        )}
      </div>
    </li>
  );
}
