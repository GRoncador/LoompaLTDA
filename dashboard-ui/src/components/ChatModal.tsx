import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatReply, Conversation, ConversationKind, DraftItem } from "../types";
import { Modal } from "./Modal";

const COPY: Record<ConversationKind, { title: string; hint: string; placeholder: string; agent: string }> = {
  meeting: {
    title: "🗓 Reunião de Sprint com o Master Loompa",
    hint: "O Master abre com o estado do projeto. Conte o que você quer fazer; quando o rascunho estiver bom, o Product Owner propõe o sprint e você aprova ou ajusta antes de começar.",
    placeholder: "Ex.: Hoje quero a recuperação de senha pronta; refatorar o webhook de cobrança; e um relatório mensal em CSV.",
    agent: "Master Loompa",
  },
  brainstorm: {
    title: "💡 Brainstorm com o Analyst Loompa",
    hint: "Pense em voz alta. As ideias concretas viram cards no rascunho e o Product Owner decide o que entra no backlog.",
    placeholder: "Ex.: Como podemos tornar o primeiro acesso dos clientes mais simples?",
    agent: "Analyst Loompa",
  },
  review: {
    title: "📋 Revisão do pedido com o Product Owner",
    hint: "O Product Owner não gravou o card. Esclareça ou corrija o pedido e ele revê; se mesmo assim quiser o card, a palavra final é sua.",
    placeholder: "Explique melhor o pedido…",
    agent: "Product Owner Loompa",
  },
};

const KIND_LABEL: Record<string, string> = { bugfix: "correção", research: "pesquisa", feature: "funcionalidade" };

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
  const [text, setText] = useState(initialText ?? "");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [waitingFor, setWaitingFor] = useState<string | null>(null); // who is thinking right now
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
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
  // the sprint starts only after the Product Owner's proposal, and only with cards it saw
  const unseen = proposal ? inSprint.filter((i) => !proposal.keys.includes(i.key)).map((i) => i.key) : [];
  const canStart = !!proposal && inSprint.length > 0 && unseen.length === 0;
  const founderTurns = conv?.turns.filter((t) => t.who === "founder").length ?? 0;
  const review = conv?.draft.review ?? null;

  useEffect(() => {
    if (resumeId) {
      api.conversation(slug, resumeId).then((r) => setConv(r.conversation)).catch((e) => setErr(detail(e)));
    } else if (kind === "meeting" && !opening.current) {
      // the Master opens the meeting with where the project stands (plan 10.2)
      opening.current = true;
      setBusy(true); setWaitingFor("Master Loompa está preparando o parecer");
      api.openConversation(slug, "meeting", "").then((r) => setConv(r.conversation)).catch((e) => setErr(detail(e))).finally(() => { setBusy(false); setWaitingFor(null); });
    }
  }, [slug, resumeId, kind]);
  useEffect(() => { setGoal(conv?.draft.goal ?? ""); }, [conv?.draft.goal]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [conv?.turns.length, pending]);

  const apply = (r: ChatReply) => {
    setConv(r.conversation);
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
  const commit = (start: boolean, force = false) => conv && run(() => api.commit(slug, conv.id, { start_sprint: start, goal, run: true, force }), start ? "Começando o sprint" : null);
  const discard = async () => {
    if (!conv || !window.confirm(isReview ? "Desistir deste pedido? Nada será gravado." : "Descartar esta conversa? Nada do rascunho será salvo.")) return;
    if (await run(() => api.discard(slug, conv.id))) onClose();
  };
  // a meeting opened and left before the founder said anything is not worth keeping
  const close = () => {
    if (conv && conv.kind === "meeting" && conv.status === "open" && founderTurns === 0) api.discard(slug, conv.id).catch(() => {});
    onClose();
  };
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
  const startHint = !proposal ? "Peça a proposta do Product Owner antes de começar"
    : inSprint.length === 0 ? "Marque ao menos um card para o sprint"
    : unseen.length ? `${unseen.join(", ")} entrou depois da proposta: peça uma nova`
    : undefined;
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
                value={text} rows={3} disabled={busy && !!pending}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder="Escreva e tecle Enter (Shift+Enter quebra a linha)"
                className="w-full resize-none rounded-md border border-line bg-ink p-2 text-sm"
              />
              <div className="mt-1 flex items-center gap-2">
                <button className={recording ? "btn bg-red-600 text-white" : "btn-ghost"} onClick={toggleRecord} disabled={busy}>{recording ? "⏹ Parar ditado" : "🎙 Ditar"}</button>
                <div className="mx-auto" />
                <button className="btn-primary" disabled={busy || !text.trim()} onClick={send}>Enviar</button>
              </div>
            </div>
          ) : (
            <div className="border-t border-line p-3 text-right"><button className="btn-primary" onClick={onClose}>Fechar</button></div>
          )}
        </section>

        {isReview ? (
          <ReviewPanel conv={conv} review={review} busy={busy} open={open} founderTurns={founderTurns}
            onForce={() => commit(false, true)} onBrainstorm={onBrainstorm ? toBrainstorm : undefined} onDiscard={discard} />
        ) : (
          <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <h4 className="text-sm font-semibold">Rascunho {isMeeting ? "do backlog e do sprint" : "de ideias"}</h4>
              <span className="text-[11px] text-slate-500">{items.length} cards{isMeeting ? ` · ${inSprint.length} no sprint` : ""}</span>
            </div>
            {isMeeting && (
              <input
                value={goal} disabled={!open || !conv} placeholder="Meta do sprint (uma frase)"
                onChange={(e) => setGoal(e.target.value)}
                onBlur={saveGoal}
                className="mx-3 mt-2 rounded-md border border-line bg-ink px-2 py-1 text-xs"
              />
            )}
            <ul className="scroll-thin flex-1 space-y-1.5 overflow-y-auto p-3">
              {items.length === 0 && <li className="text-xs text-slate-500">O rascunho está vazio. Conte suas ideias na conversa.</li>}
              {items.map((i) => <Item key={i.key} item={i} sprint={isMeeting} editable={open && !busy} onEdit={edit} />)}
            </ul>
            {open && conv && (
              <div className="space-y-1 border-t border-line p-2">
                {isMeeting && (
                  <button className="btn-ghost w-full" disabled={busy || items.length === 0} title="O Product Owner propõe o que entra no sprint, em que ordem e como os cards se relacionam" onClick={propose}>
                    📋 {proposal ? "Pedir nova proposta ao Product Owner" : "Pedir a proposta do Product Owner"}
                  </button>
                )}
                <div className="flex gap-2">
                  {isMeeting ? (
                    <>
                      <button className="btn-ghost flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Salvar no backlog</button>
                      <button className="btn-primary flex-1" disabled={busy || !canStart} title={startHint} onClick={async () => { if (await commit(true)) onClose(); }}>Começar Sprint ▶</button>
                    </>
                  ) : (
                    <button className="btn-primary flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Enviar ao backlog (o Product Owner admite)</button>
                  )}
                </div>
                {isMeeting && !canStart && startHint && items.length > 0 && <p className="text-center text-[11px] text-slate-500">{startHint}</p>}
                <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={discard}>Descartar conversa</button>
              </div>
            )}
            {done && conv?.result.created && <p className="border-t border-line p-3 text-xs text-emerald-300">✔ {conv.result.created.length === 1 ? "1 nova história" : `${conv.result.created.length} novas histórias`}{conv.result.sprint_id ? ` · ${conv.result.sprint_id} em andamento` : ""}.</p>}
          </section>
        )}
      </div>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </Modal>
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

function Item({ item, sprint, editable, onEdit }: { item: DraftItem; sprint: boolean; editable: boolean; onEdit: (ops: Record<string, unknown>[]) => void }) {
  return (
    <li className="rounded-md border border-line bg-panel p-2 text-xs">
      <div className="flex items-start gap-2">
        {sprint && <input type="checkbox" className="mt-1" title="No sprint" checked={item.in_sprint} disabled={!editable} onChange={(e) => onEdit([{ op: "update", ref: item.key, in_sprint: e.target.checked }])} />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
            <span>{item.key}</span>
            {item.story_id && <span className="chip bg-sky-900/50 text-sky-200">já no backlog</span>}
            {item.epic && <span className="chip bg-slate-800 text-slate-400">{item.epic}</span>}
            {item.unpin && <span className="chip bg-slate-800 text-slate-400" title="A posição fixada volta ao Product Owner ao salvar">solta o 📌</span>}
          </div>
          <div className="font-medium leading-snug text-slate-100">{item.title}</div>
          {item.description && !item.story_id && <div className="mt-0.5 line-clamp-2 text-slate-400">{item.description}</div>}
          {item.note && <div className="mt-1 text-amber-300">{item.note}</div>}
        </div>
        <select value={item.priority} disabled={!editable} title="Prioridade (1 = urgente)" onChange={(e) => onEdit([{ op: "update", ref: item.key, priority: Number(e.target.value) }])} className="rounded border border-line bg-ink px-1 py-0.5 text-[11px]">
          {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>P{p}</option>)}
        </select>
        <button className="text-slate-500 hover:text-red-300 disabled:opacity-40" title="Tirar do rascunho" disabled={!editable} onClick={() => onEdit([{ op: "drop", ref: item.key }])}>✕</button>
      </div>
    </li>
  );
}
