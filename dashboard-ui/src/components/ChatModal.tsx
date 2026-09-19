import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatReply, Conversation, ConversationKind, DraftItem } from "../types";
import { Modal } from "./Modal";

const COPY: Record<ConversationKind, { title: string; hint: string; placeholder: string; agent: string }> = {
  meeting: {
    title: "☀️ Reunião de sprint com o Master Loompa",
    hint: "Conte o que você quer fazer. O rascunho do backlog e do sprint muda a cada mensagem; nada é salvo até você decidir.",
    placeholder: "Ex.: Hoje quero a recuperação de senha pronta; refatorar o webhook de cobrança; e um relatório mensal em CSV.",
    agent: "Master Loompa",
  },
  brainstorm: {
    title: "💡 Brainstorm com o Analyst Loompa",
    hint: "Pense em voz alta. As ideias concretas viram cards no rascunho e o Product Owner decide o que entra no backlog.",
    placeholder: "Ex.: Como podemos tornar o primeiro acesso dos clientes mais simples?",
    agent: "Analyst Loompa",
  },
};

// api.ts throws `${status} ${body}`; the body is FastAPI's {"detail": "..."}.
function detail(e: unknown): string {
  const raw = String(e instanceof Error ? e.message : e);
  const json = raw.slice(raw.indexOf("{"));
  try { return String(JSON.parse(json).detail ?? raw); } catch { return raw; }
}

export default function ChatModal({ slug, kind, resumeId, onClose }: { slug: string; kind: ConversationKind; resumeId?: string; onClose: () => void }) {
  const [conv, setConv] = useState<Conversation | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [recording, setRecording] = useState(false);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  const current: ConversationKind = conv?.kind ?? kind;
  const copy = COPY[current];
  const isMeeting = current === "meeting";
  const open = !conv || conv.status === "open";
  const items = conv?.draft.items ?? [];
  const inSprint = items.filter((i) => i.in_sprint).length;

  useEffect(() => {
    if (!resumeId) return;
    api.conversation(slug, resumeId).then((r) => setConv(r.conversation)).catch((e) => setErr(detail(e)));
  }, [slug, resumeId]);
  useEffect(() => { setGoal(conv?.draft.goal ?? ""); }, [conv?.draft.goal]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [conv?.turns.length, pending]);

  const apply = (r: ChatReply) => {
    setConv(r.conversation);
    setNotes(r.turn?.ignored ?? r.report?.ignored ?? []);
  };
  const run = async (fn: () => Promise<ChatReply>): Promise<boolean> => {
    setBusy(true); setErr(null);
    try { apply(await fn()); return true; } catch (e) { setErr(detail(e)); return false; } finally { setBusy(false); }
  };

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setText(""); setPending(t);
    const ok = await run(() => (conv ? api.say(slug, conv.id, t) : api.openConversation(slug, kind, t)));
    setPending(null);
    if (!ok) setText(t);
  };
  const edit = (ops: Record<string, unknown>[]) => conv && run(() => api.editDraft(slug, conv.id, ops));
  // Saved quietly (no `busy`): a blur must not disable the button the founder is about to click.
  const saveGoal = () => {
    if (!open || !conv || goal === conv.draft.goal) return;
    api.editDraft(slug, conv.id, [{ op: "goal", text: goal }]).then(apply).catch((e) => setErr(detail(e)));
  };
  const commit = (start: boolean) => conv && run(() => api.commit(slug, conv.id, { start_sprint: start, goal, run: true }));
  const discard = async () => {
    if (!conv || !window.confirm("Descartar esta conversa? Nada do rascunho será salvo.")) return;
    if (await run(() => api.discard(slug, conv.id))) onClose();
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
  return (
    <Modal wide title={copy.title} onClose={onClose}>
      <p className="text-xs text-slate-400">{copy.hint}</p>
      {conv?.limits.map((l, i) => <p key={i} className="mt-2 rounded-md border border-amber-700/60 bg-amber-900/30 px-2 py-1 text-xs text-amber-100">{l}</p>)}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
          <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3 text-sm">
            {!conv && !pending && <p className="text-slate-500">{copy.placeholder}</p>}
            {conv?.turns.map((t, i) => <Bubble key={i} who={t.who} name={t.name || copy.agent} text={t.text} changes={t.changes} />)}
            {pending && <Bubble who="founder" name="" text={pending} changes={[]} />}
            {busy && pending && <p className="text-xs text-slate-500">{copy.agent} está pensando…</p>}
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

        <section className="flex h-[52vh] flex-col rounded-md border border-line bg-ink/40">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <h4 className="text-sm font-semibold">Rascunho {isMeeting ? "do backlog e do sprint" : "de ideias"}</h4>
            <span className="text-[11px] text-slate-500">{items.length} cards{isMeeting ? ` · ${inSprint} no sprint` : ""}</span>
          </div>
          {isMeeting && (
            <input
              value={goal} disabled={!open} placeholder="Meta do sprint (uma frase)"
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
              <div className="flex gap-2">
                {isMeeting ? (
                  <>
                    <button className="btn-ghost flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Salvar no backlog</button>
                    <button className="btn-primary flex-1" disabled={busy || inSprint === 0} title={inSprint === 0 ? "Marque ao menos um card para o sprint" : undefined} onClick={async () => { if (await commit(true)) onClose(); }}>Começar Sprint ▶</button>
                  </>
                ) : (
                  <button className="btn-primary flex-1" disabled={busy || items.length === 0} onClick={() => commit(false)}>Enviar ao backlog (o Product Owner admite)</button>
                )}
              </div>
              <button className="w-full text-[11px] text-slate-500 hover:text-red-300" disabled={busy} onClick={discard}>Descartar conversa</button>
            </div>
          )}
          {done && conv?.result.created && <p className="border-t border-line p-3 text-xs text-emerald-300">✔ {conv.result.created.length === 1 ? "1 nova história" : `${conv.result.created.length} novas histórias`}{conv.result.sprint_id ? ` · ${conv.result.sprint_id} em andamento` : ""}.</p>}
        </section>
      </div>
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
    </Modal>
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
