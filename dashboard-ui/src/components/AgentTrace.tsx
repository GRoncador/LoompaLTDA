/** What the Loompas actually did on a story, the way an agent CLI shows its own run (plan 11.4):
 * the tree of phases, tasks, rounds of the tool loop, model calls and tool calls. Each line
 * carries its numbers; clicking one opens the prompt, the answer or the tool's result, which the
 * server resolves from the trace file only when asked. */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { TraceDetail, TraceRun, TraceSpan } from "../types";
import { loompaName } from "../names";

export default function AgentTrace({ slug, id, live = false }: { slug: string; id: string; live?: boolean }) {
  const [data, setData] = useState<TraceRun | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const version = useRef<string | null>(null);
  const stick = useRef(true); // the reader is at the end: keep them there as new steps arrive

  // the panel's scrolling element is the drawer's tab area, not this list
  const scroller = () => box.current?.closest(".scroll-always") as HTMLElement | null;
  const atBottom = () => {
    const el = scroller();
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await api.storyTrace(slug, id, version.current);
        if (!alive || d.unchanged) return; // nothing was written since the last poll
        stick.current = atBottom(); // decided before the list grows under the reader
        version.current = d.version;
        setData(d);
      } catch {
        if (alive) setError(true);
      }
    };
    version.current = null;
    load();
    // while a Loompa is on this story the tab follows it; a finished story is read once
    if (!live) return () => { alive = false; };
    const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [slug, id, live]);

  // the newest step is at the end: open at the end, and stay there while the reader is there
  useEffect(() => {
    const el = scroller();
    if (data && el && stick.current) el.scrollTop = el.scrollHeight;
  }, [data]);
  const t = data?.totals;
  return (
    <div ref={box} className="space-y-2 text-xs">
      {error && <p className="text-slate-400">Não foi possível ler o registro desta história.</p>}
      {!error && !data && <p className="text-slate-400">carregando…</p>}
      {data && data.spans.length === 0 && <p className="text-slate-400">Nenhum passo registrado ainda.</p>}
      {data && t && data.spans.length > 0 && (<>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
        {live && <span className="flex items-center gap-1 text-emerald-300"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />ao vivo</span>}
        <span>
          {t.calls} consultas ao modelo{t.cuts > 0 ? ` (${t.cuts} cortadas)` : ""} · {t.tools} usos de ferramenta ·
          US$ {t.cost_usd.toFixed(4)} · modelo {dur(t.model_ms)} · ferramentas {dur(t.tool_ms)}
          {data.truncated ? " · mostrando o trecho mais recente" : ""}
        </span>
        <span className="ml-auto">do começo (topo) ao mais recente (fim)</span>
      </div>
      <ol className="space-y-0.5">
        {data.spans.map((s) => (
          <li key={s.id} style={{ paddingLeft: `${Math.min(s.depth, 6) * 12}px` }}>
            <Line slug={slug} id={id} s={s} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)} />
          </li>
        ))}
      </ol>
      </>)}
    </div>
  );
}

function Line({ slug, id, s, open, onToggle }: { slug: string; id: string; s: TraceSpan; open: boolean; onToggle: () => void }) {
  const failed = s.status && s.status !== "ok";
  return (
    <>
      <button
        className={`flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left hover:bg-line/60 ${s.has_detail ? "" : "cursor-default"}`}
        onClick={() => s.has_detail && onToggle()}
        title={s.has_detail ? "abrir o que foi enviado e recebido" : undefined}
      >
        <span className="w-4 shrink-0 text-slate-600">{s.has_detail ? (open ? "▾" : "▸") : ""}</span>
        <span className="shrink-0 tabular-nums text-slate-600" title={new Date(s.start).toLocaleString("pt-BR")}>{clock(s.start)}</span>
        <span className={`shrink-0 rounded px-1 text-[9px] uppercase ${KIND[s.kind] ?? "bg-slate-800 text-slate-400"}`}>{KIND_LABEL[s.kind] ?? s.kind}</span>
        <span className="min-w-0 flex-1 break-words text-slate-300">{summary(s)}</span>
        <span className={`shrink-0 tabular-nums ${failed ? "text-red-300" : "text-slate-500"}`}>{failed ? s.status : dur(s.ms)}</span>
      </button>
      {open && <Detail slug={slug} id={id} spanId={s.id} />}
    </>
  );
}

const KIND: Record<string, string> = {
  node: "bg-violet-900/50 text-violet-200", task: "bg-emerald-900/50 text-emerald-200",
  round: "bg-slate-800 text-slate-400", llm: "bg-sky-900/50 text-sky-200", tool: "bg-amber-900/50 text-amber-200",
};
const KIND_LABEL: Record<string, string> = { node: "fase", task: "tarefa", round: "rodada", llm: "modelo", tool: "ferramenta" };

/** The line of each kind, with the numbers that explain it. */
function summary(s: TraceSpan): string {
  const a = s.attrs ?? {};
  if (s.kind === "llm") {
    const model = String(a.model ?? "?").split("/").pop();
    const parts = [
      `${loompaName(String(a.agent ?? ""))} · ${model}${a.responded ? ` → ${a.responded}` : ""}`,
      `entrada ${k(a.input_tokens)}${a.cached_tokens ? ` (cache ${k(a.cached_tokens)})` : ""}`,
      `saída ${k(a.output_tokens)}${a.reasoning_tokens ? ` (raciocínio ${k(a.reasoning_tokens)})` : ""}`,
      a.cost_usd ? `US$ ${Number(a.cost_usd).toFixed(4)}` : "",
      a.finish_reason && a.finish_reason !== "stop" ? `fim: ${a.finish_reason}` : "",
      a.cuts ? `${a.cuts} corte(s)` : "",
      (s.attempts ?? []).filter((x) => x.outcome !== "ok").length ? `${(s.attempts ?? []).filter((x) => x.outcome !== "ok").length} tentativa(s) antes` : "",
    ];
    return parts.filter(Boolean).join(" · ");
  }
  if (s.kind === "tool") {
    const args = Object.entries((a.args ?? {}) as Record<string, unknown>)
      .map(([key, v]) => `${key}=${String(v).replace(/\n/g, " ").slice(0, 40)}`).join(" ");
    const flags = [a.ok === false ? `erro: ${a.error ?? ""}` : "", a.repeat ? "repetida" : "", a.note ? "aviso do LoopGuard" : ""].filter(Boolean);
    return [`${s.name} ${args}`.trim(), a.chars ? `${k(a.chars)} caracteres` : "", ...flags].filter(Boolean).join(" · ");
  }
  if (s.kind === "task") return [`${s.name}`, a.text, a.origin ? `(${a.origin})` : "", a.outcome].filter(Boolean).join(" · ");
  if (s.kind === "node") return [s.name, a.tier, a.next ? `→ ${a.next}` : ""].filter(Boolean).join(" · ");
  return [s.name, a.label].filter(Boolean).join(" · ");
}

/** The prompt, the answer and the tool result of one span, fetched on demand. */
function Detail({ slug, id, spanId }: { slug: string; id: string; spanId: string }) {
  const [d, setD] = useState<TraceDetail | null>(null);
  useEffect(() => { api.storyTraceSpan(slug, id, spanId).then(setD).catch(() => setD(null)); }, [slug, id, spanId]);
  if (!d) return <p className="px-6 py-1 text-[11px] text-slate-500">carregando…</p>;
  return (
    <div className="my-1 space-y-2 rounded-md border border-line bg-ink/60 p-2">
      {d.error && <p className="text-[11px] text-red-300">{d.error}</p>}
      {d.messages.length > 0 && <Part title={`enviado (${d.messages.length} mensagens)`} items={d.messages} collapsed />}
      {d.response && <Part title="resposta" items={[d.response]} />}
      {d.result && <Part title="resultado da ferramenta" items={[d.result]} />}
      {Object.keys(d.attrs ?? {}).length > 0 && (
        <details>
          <summary className="cursor-pointer text-[10px] uppercase tracking-wide text-slate-500">detalhes técnicos</summary>
          <pre className="scroll-thin mt-1 overflow-x-auto font-mono text-[10px] text-slate-400">{JSON.stringify(d.attrs, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function Part({ title, items, collapsed = false }: { title: string; items: TraceDetail["messages"]; collapsed?: boolean }) {
  return (
    <details open={!collapsed}>
      <summary className="cursor-pointer text-[10px] uppercase tracking-wide text-slate-500">{title}</summary>
      <div className="mt-1 space-y-1.5">
        {items.map((m, i) => (
          <div key={i}>
            <div className="text-[10px] text-slate-500">{ROLE[m.role] ?? m.role}{m.name ? ` · ${m.name}` : ""}</div>
            {m.content && <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-slate-300">{m.content}</pre>}
            {m.tool_calls.map((c, n) => (
              <pre key={n} className="scroll-thin overflow-x-auto font-mono text-[11px] text-emerald-300">→ {c.name} {JSON.stringify(c.arguments)}</pre>
            ))}
          </div>
        ))}
      </div>
    </details>
  );
}

const ROLE: Record<string, string> = { system: "instruções", user: "pedido", assistant: "modelo", tool: "ferramenta" };

/** The wall clock of a span, in the founder's timezone: the trace stores UTC. */
function clock(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function k(n: unknown): string {
  const v = Number(n ?? 0);
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
}

function dur(ms: unknown): string {
  const s = Number(ms ?? 0) / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${String(Math.floor(s % 60)).padStart(2, "0")}s` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
}
