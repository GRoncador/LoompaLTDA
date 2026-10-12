import { useEffect, useState } from "react";
import type { Message } from "../types";
import { DesignScreens } from "./DesignScreens";
import { Icon } from "./Icon";

const KIND: Record<Message["kind"], { label: string; cls: string }> = {
  decision: { label: "Decisão", cls: "bg-violet-900/60 text-violet-200" },
  blocked: { label: "Bloqueada", cls: "bg-red-900/60 text-red-200" },
  delivery: { label: "Entrega", cls: "bg-emerald-900/60 text-emerald-200" },
  info: { label: "Informe", cls: "bg-slate-800 text-slate-300" },
  finance: { label: "Finanças", cls: "bg-amber-900/60 text-amber-200" },
  kaizen: { label: "Kaizen", cls: "bg-lime-900/60 text-lime-200" },
};

export default function Inbox(props: {
  messages: Message[];
  onReply: (m: Message, option: string | null, text: string | null, decisions?: Record<string, string>, answers?: Record<string, string>) => Promise<void>;
  onArchive: (m: Message) => Promise<void>;
  onOpenStory: (id: string) => void;
  onOpenSprint: (id: string) => void;
  onOpenMeeting?: (id: string) => void;
}) {
  const { messages } = props;
  const [read, setRead] = useState(loadRead);
  const markRead = (id: string) => setRead((r) => {
    if (r.has(id)) return r;
    const next = new Set(r).add(id);
    try { localStorage.setItem(READ_KEY, JSON.stringify([...next].slice(-500))); } catch { /* private window */ }
    return next;
  });
  // newest first, every note closed: the tag and the title, bold until opened
  const sorted = [...messages].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const unread = messages.filter((m) => !read.has(m.id)).length;
  // the "unread only" filter: available only while something is unread. The notes unread when it
  // was turned on stay listed after the founder opens them, so a card does not vanish under the cursor.
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [kept, setKept] = useState<Set<string>>(new Set());
  useEffect(() => { if (unread === 0) setOnlyUnread(false); }, [unread]);
  const filtering = onlyUnread && unread > 0;
  const toggleUnread = () => {
    if (!filtering) setKept(new Set(sorted.filter((m) => !read.has(m.id)).map((m) => m.id)));
    setOnlyUnread(!filtering);
  };
  const shown = filtering ? sorted.filter((m) => !read.has(m.id) || kept.has(m.id)) : sorted;
  return (
    <>
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold"><Icon name="inbox" className="h-4 w-4 text-slate-400" />Caixa de Entrada{unread > 0 && ` (${unread})`}</h2>
        <button
          type="button"
          aria-pressed={filtering}
          disabled={unread === 0}
          onClick={toggleUnread}
          title={unread === 0 ? "Não há notas não lidas" : filtering ? "Mostrar todas as notas" : "Mostrar só as notas não lidas"}
          className={`rounded-md border px-2 py-0.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${filtering ? "border-brand bg-brand/20 text-slate-50" : "border-line text-slate-400 hover:text-slate-200"}`}
        >
          Só não lidas
        </button>
      </div>
      <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3">
        {messages.length === 0 && <p className="py-8 text-center text-sm text-slate-500">Nada pendente. Aproveite o dia.</p>}
        {shown.map((m) => <MessageCard key={m.id} m={m} unread={!read.has(m.id)} onOpen={() => markRead(m.id)} {...props} />)}
      </div>
    </>
  );
}

/** Which notes the founder has opened, kept in this browser only. */
const READ_KEY = "loompa.inbox.read";
function loadRead(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) ?? "[]")); } catch { return new Set(); }
}

const SHOT_LABEL: Record<string, string> = { "desktop.png": "Computador", "mobile.png": "Celular (390 px)" };

/** The page as the smoke saw it, at each width: the founder looks before opening the product
 * (ADR-0030). A click opens the full image. */
function Shots({ m }: { m: Message }) {
  const url = (name: string) => `/api/factories/${m.factory}/stories/${m.story_id}/shots/${name}`;
  return (
    <div className="mt-2 flex items-start gap-2">
      {(m.images ?? []).map((name) => (
        <a key={name} href={url(name)} target="_blank" rel="noreferrer" className="group block" title="Abrir a imagem inteira">
          <img src={url(name)} alt={SHOT_LABEL[name] ?? name} loading="lazy"
            className={`max-h-40 rounded border border-line object-cover object-top group-hover:border-slate-400 ${name === "mobile.png" ? "w-20" : "w-48"}`} />
          <span className="mt-0.5 block text-center text-[10px] text-slate-400">{SHOT_LABEL[name] ?? name}</span>
        </a>
      ))}
    </div>
  );
}

export function MessageCard({ m, unread = false, onOpen, onReply, onArchive, onOpenStory, onOpenSprint, onOpenMeeting }: { m: Message; unread?: boolean; onOpen?: () => void } & Pick<Parameters<typeof Inbox>[0], "onReply" | "onArchive" | "onOpenStory" | "onOpenSprint" | "onOpenMeeting">) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const toggle = () => { if (!open) onOpen?.(); setOpen((o) => !o); };
  // side decisions (suggested cards): the recommended option unless the founder picks another
  const open_decisions = m.decisions.filter((d) => !d.chosen);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const choice = (d: Message["decisions"][number]) => picked[d.id] ?? d.options.find((o) => o.recommended)?.key ?? d.options[0]?.key ?? "backlog";
  // the batch questions (plan 15.4): the recommended option unless the founder picks or writes another
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const answerOf = (q: NonNullable<Message["questions"]>[number]) => answers[q.id] ?? q.options.find((o) => o.recommended)?.key ?? q.options[0]?.key ?? "";
  // what to adjust in each screen of a design approval, joined into the answer's text
  const [notes, setNotes] = useState<Record<string, string>>({});
  const adjustText = () => [text, ...Object.entries(notes).filter(([, v]) => v.trim()).map(([k, v]) => `${k}: ${v.trim()}`)].filter(Boolean).join("\n");
  const k = KIND[m.kind];
  const send = async (opt: string | null) => {
    setBusy(true);
    try {
      const qa = Object.fromEntries((m.questions ?? []).map((q) => [q.id, answerOf(q)]));
      await onReply(m, opt, (m.screens?.length ? adjustText() : text) || null, Object.fromEntries(open_decisions.map((d) => [d.id, choice(d)])), qa);
    } finally { setBusy(false); }
  };
  return (
    <article className={`rounded-md border p-3 ${unread ? "border-line bg-ink/90" : "border-line bg-ink/40"}`}>
      <div className="flex items-start gap-2">
        {unread && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" title="Não lida" />}
        <span className={`chip ${k.cls} ${unread ? "" : "opacity-70"}`}>{k.label}</span>
        <button className={`flex-1 text-left text-sm leading-snug ${unread ? "font-bold text-slate-50" : "font-normal text-slate-400"}`} onClick={toggle}>{m.title}</button>
        <time dateTime={m.created_at} className="shrink-0 whitespace-nowrap pt-0.5 text-[11px] text-slate-500" title="Data e hora em que chegou">{new Date(m.created_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
      </div>
      {open && (
        <>
          <p className="mt-2 whitespace-pre-line text-sm text-slate-300">{m.context}</p>
          {m.impact && <p className="mt-1 text-xs italic text-slate-400">{m.impact}</p>}
          {m.story_id && (m.images?.length ?? 0) > 0 && <Shots m={m} />}
          {(m.screens?.length ?? 0) > 0 && (
            <DesignScreens slug={m.factory} screens={m.screens!} notes={m.status === "pending" ? notes : undefined} onNote={(key, v) => setNotes((n) => ({ ...n, [key]: v }))} />
          )}
          {(m.questions?.length ?? 0) > 0 && (
            <div className="mt-3 space-y-3 rounded-md border border-line bg-panel/60 p-2">
              {m.questions!.map((q) => {
                const typed = !q.options.some((o) => o.key === answerOf(q));
                return (
                  <div key={q.id} className="text-xs">
                    <div className="text-sm text-slate-100">{q.text}</div>
                    {q.why && <div className="text-slate-400">{q.why}</div>}
                    <div className="mt-1 flex flex-wrap gap-1">
                      {q.options.map((o) => (
                        <button key={o.key} type="button" onClick={() => setAnswers((a) => ({ ...a, [q.id]: o.key }))} className={`rounded border px-2 py-0.5 ${answerOf(q) === o.key ? "border-brand bg-brand/20 text-slate-50" : "border-line text-slate-400 hover:text-slate-200"}`}>
                          {o.label}{o.recommended && <Icon name="star" className="ml-1 inline h-3 w-3 align-[-1px] text-amber-300" />}
                        </button>
                      ))}
                    </div>
                    <input value={typed ? answerOf(q) : ""} onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))} placeholder="…ou escreva outra resposta" className="mt-1 w-full rounded-md border border-line bg-ink px-2 py-0.5" />
                  </div>
                );
              })}
            </div>
          )}
          {m.conversation_id && onOpenMeeting && (
            <button className="btn-primary mt-2 mr-2 text-xs" onClick={() => onOpenMeeting(m.conversation_id!)}>
              {m.title.toLowerCase().includes("brainstorm") ? <><Icon name="lightbulb" /> Abrir o brainstorm</> : <><Icon name="calendar" /> Abrir a reunião</>}
            </button>
          )}
          {m.sprint_id && <button className="btn-ghost mt-2 text-xs" onClick={() => onOpenSprint(m.sprint_id!)}><Icon name="barChart" /> Ver o relatório do {m.sprint_id}</button>}
          {m.decisions.length > 0 && (
            <div className="mt-3 space-y-2 rounded-md border border-line bg-panel/60 p-2">
              <p className="text-xs font-semibold text-slate-300"><Icon name="lightbulb" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" /> Achados sugeridos — decida cada um</p>
              {m.decisions.map((d) => (
                <div key={d.id} className="text-xs">
                  <div className="text-slate-200">{d.title}</div>
                  {d.context && <div className="text-slate-400">{d.context}</div>}
                  {d.chosen ? (
                    <div className="mt-1 text-lime-300">decidido: {d.options.find((o) => o.key === d.chosen)?.label ?? d.chosen}</div>
                  ) : (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {d.options.map((o) => (
                        <button key={o.key} type="button" onClick={() => setPicked((p) => ({ ...p, [d.id]: o.key }))} className={`rounded border px-2 py-0.5 ${choice(d) === o.key ? "border-brand bg-brand/20 text-slate-50" : "border-line text-slate-400 hover:text-slate-200"}`}>
                          {o.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {m.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {m.options.map((o) => (
                <button key={o.key} disabled={busy} title={o.description} onClick={() => send(o.key)} className={o.recommended ? "btn-primary" : "btn-ghost"}>
                  {o.label}{o.recommended && <Icon name="star" className="ml-1 inline h-3 w-3 align-[-1px] text-amber-300" />}
                </button>
              ))}
            </div>
          )}
          {/* what an option makes the factory do, readable without hovering (ADR-0037 §1) */}
          {m.options.some((o) => o.description) && (
            <ul className="mt-1 space-y-0.5 text-[11px] text-slate-400">
              {m.options.filter((o) => o.description).map((o) => (
                <li key={o.key}><span className="text-slate-300">{o.label}:</span> {o.description}</li>
              ))}
            </ul>
          )}
          {m.allow_free_text && (
            <div className="mt-2 flex gap-2">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Orientação livre (opcional)…" className="flex-1 rounded-md border border-line bg-panel px-2 py-1 text-sm" />
              {m.options.length === 0 && <button className="btn-primary" disabled={busy || !text} onClick={() => send(null)}>Enviar</button>}
            </div>
          )}
          <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
            <span>{m.sender}{m.story_id && <> · <button className="text-sky-300 hover:underline" onClick={() => onOpenStory(m.story_id!)}>{m.story_id}</button></>}</span>
            <button className="hover:text-slate-300" onClick={() => onArchive(m)}>arquivar</button>
          </div>
        </>
      )}
    </article>
  );
}
