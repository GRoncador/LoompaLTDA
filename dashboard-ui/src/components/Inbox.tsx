import { useState } from "react";
import type { Message, Overview } from "../types";

const KIND: Record<Message["kind"], { label: string; cls: string }> = {
  decision: { label: "Decisão", cls: "bg-violet-900/60 text-violet-200" },
  blocked: { label: "Bloqueada", cls: "bg-red-900/60 text-red-200" },
  delivery: { label: "Entrega", cls: "bg-emerald-900/60 text-emerald-200" },
  info: { label: "Informe", cls: "bg-slate-800 text-slate-300" },
  finance: { label: "Finanças", cls: "bg-amber-900/60 text-amber-200" },
  kaizen: { label: "Kaizen", cls: "bg-lime-900/60 text-lime-200" },
};

export default function Inbox(props: {
  messages: Message[]; finance: Overview["finance"] | null; kaizen: number;
  onReply: (m: Message, option: string | null, text: string | null, decisions?: Record<string, string>) => Promise<void>;
  onArchive: (m: Message) => Promise<void>;
  onOpenStory: (id: string) => void;
}) {
  const { messages, finance, kaizen } = props;
  const actionable = messages.filter((m) => m.options.length > 0 || m.kind === "decision" || m.kind === "blocked");
  const info = messages.filter((m) => !actionable.includes(m));
  return (
    <>
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <h2 className="text-sm font-semibold">📬 Caixa de Entrada do Founder</h2>
        <span className="text-xs text-slate-400">linguagem executiva · responda em lote</span>
      </div>
      <div className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3">
        {finance && (
          <div className="flex items-center justify-between rounded-md border border-line bg-ink/60 px-3 py-2 text-xs">
            <span>💰 Gasto hoje: <b>US$ {finance.today_usd.toFixed(2)}</b> · mês: US$ {finance.month_usd.toFixed(2)} de {finance.cap_usd.toFixed(0)}</span>
            <span>💡 {kaizen} melhorias catalogadas hoje</span>
          </div>
        )}
        {messages.length === 0 && <p className="py-8 text-center text-sm text-slate-500">Nada pendente. Aproveite o dia. 🎉</p>}
        {actionable.map((m) => <MessageCard key={m.id} m={m} {...props} />)}
        {info.map((m) => <MessageCard key={m.id} m={m} {...props} />)}
      </div>
    </>
  );
}

function MessageCard({ m, onReply, onArchive, onOpenStory }: { m: Message } & Pick<Parameters<typeof Inbox>[0], "onReply" | "onArchive" | "onOpenStory">) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(m.kind !== "info");
  // side decisions (suggested cards): the recommended option unless the founder picks another
  const open_decisions = m.decisions.filter((d) => !d.chosen);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const choice = (d: Message["decisions"][number]) => picked[d.id] ?? d.options.find((o) => o.recommended)?.key ?? d.options[0]?.key ?? "backlog";
  const k = KIND[m.kind];
  const send = async (opt: string | null) => {
    setBusy(true);
    try { await onReply(m, opt, text || null, Object.fromEntries(open_decisions.map((d) => [d.id, choice(d)]))); } finally { setBusy(false); }
  };
  return (
    <article className="rounded-md border border-line bg-ink/70 p-3">
      <div className="flex items-start gap-2">
        <span className={`chip ${k.cls}`}>{k.label}</span>
        <button className="flex-1 text-left text-sm font-semibold leading-snug" onClick={() => setOpen((o) => !o)}>{m.title}</button>
        {m.story_id && <button className="text-xs text-brand hover:underline" onClick={() => onOpenStory(m.story_id!)}>{m.story_id}</button>}
      </div>
      {open && (
        <>
          <p className="mt-2 whitespace-pre-line text-sm text-slate-300">{m.context}</p>
          {m.impact && <p className="mt-1 text-xs italic text-slate-400">{m.impact}</p>}
          {m.decisions.length > 0 && (
            <div className="mt-3 space-y-2 rounded-md border border-line bg-panel/60 p-2">
              <p className="text-xs font-semibold text-slate-300">💡 Achados sugeridos — decida cada um</p>
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
                  {o.label}{o.recommended ? " ★" : ""}
                </button>
              ))}
            </div>
          )}
          {m.allow_free_text && (
            <div className="mt-2 flex gap-2">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Orientação livre (opcional)…" className="flex-1 rounded-md border border-line bg-panel px-2 py-1 text-sm" />
              {m.options.length === 0 && <button className="btn-primary" disabled={busy || !text} onClick={() => send(null)}>Enviar</button>}
            </div>
          )}
          <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
            <span>{m.sender} · {new Date(m.created_at).toLocaleString("pt-BR", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" })}</span>
            <button className="hover:text-slate-300" onClick={() => onArchive(m)}>arquivar</button>
          </div>
        </>
      )}
    </article>
  );
}
