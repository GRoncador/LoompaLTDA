import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";
import type { SprintListItem, SprintReport, SprintStoryReport, SprintTotals } from "../types";

// One series, one hue (as in Custos); the burn-up's three series take the validated dark
// categorical slots 1-3 in fixed order (blue, orange, aqua: all checks pass on #111827).
const BAR = "#f59e0b";
const GRID = "#1f2937";
const BURN = [
  { key: "done", label: "Concluídas", color: "#3987e5" },
  { key: "waiting", label: "Aguardando você", color: "#d95926" },
  { key: "working", label: "Em andamento", color: "#199e70" },
] as const;

const STATUS: Record<string, { label: string; cls: string }> = {
  open: { label: "em planejamento", cls: "bg-slate-800 text-slate-300" },
  running: { label: "rodando", cls: "bg-sky-900/60 text-sky-200" },
  closed: { label: "encerrado", cls: "bg-emerald-900/50 text-emerald-200" },
  cancelled: { label: "cancelado", cls: "bg-red-900/50 text-red-200" },
};
const RESULT: Record<string, { label: string; cls: string }> = {
  delivered: { label: "entregue", cls: "text-emerald-300" },
  cancelled: { label: "cancelada", cls: "text-red-300" },
  withdrawn: { label: "devolvida ao backlog", cls: "text-slate-400" },
  waiting: { label: "aguardando você", cls: "text-amber-300" },
  working: { label: "em andamento", cls: "text-sky-300" },
};
const STAGE: Record<string, string> = {
  BACKLOG: "Backlog", SPEC: "Especificação", PLAN: "Planejamento", DEV: "Desenvolvimento", TEST: "Testes", REVIEW: "Revisão", AWAITING_FOUNDER: "Aguardando você", DONE: "Concluída", CANCELLED: "Cancelada",
};
const NOT_MEASURED = "não medido";

const usd = (v: number | null | undefined) => (v == null ? NOT_MEASURED : v >= 1 ? `US$ ${v.toFixed(2)}` : `US$ ${v.toFixed(4)}`);
function dur(s: number | null | undefined): string {
  if (s == null) return NOT_MEASURED;
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

type Tab = "overview" | "stories" | "report";

export default function SprintsModal({ slug, initial, onClose, onOpenStory }: { slug: string; initial?: string | null; onClose: () => void; onOpenStory: (id: string) => void }) {
  const [list, setList] = useState<SprintListItem[] | null>(null);
  const [selected, setSelected] = useState<string | null>(initial ?? null);
  const [report, setReport] = useState<SprintReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [asTable, setAsTable] = useState(false);
  useEffect(() => {
    api.sprints(slug).then((l) => {
      setList(l);
      // the running sprint first, else the last one that started
      setSelected((s) => s ?? (l.find((x) => x.status === "running") ?? [...l].reverse().find((x) => x.started_at) ?? l[l.length - 1])?.id ?? null);
    }).catch((e) => setError(String(e)));
  }, [slug]);
  useEffect(() => {
    if (!selected) return;
    setReport(null);
    api.sprintReport(slug, selected).then(setReport).catch((e) => setError(String(e)));
  }, [slug, selected]);
  const ordered = useMemo(() => [...(list ?? [])].reverse(), [list]);
  return (
    <Modal title="🏁 Sprints" onClose={onClose} className="w-[96vw] max-w-[1400px]">
      {error && <p className="text-sm text-red-300">{error}</p>}
      {!list && !error && <p className="text-sm text-slate-400">carregando…</p>}
      {list && list.length === 0 && <p className="text-sm text-slate-400">Nenhum sprint ainda. Eles começam numa Reunião de Sprint.</p>}
      {list && list.length > 0 && (
        <div className="grid gap-4 md:grid-cols-[16rem_minmax(0,1fr)]">
          <aside className="space-y-1.5">
            {ordered.map((sp) => {
              const st = STATUS[sp.status];
              return (
                <button key={sp.id} onClick={() => { setSelected(sp.id); setTab("overview"); }}
                  className={`w-full rounded-md border p-2 text-left text-xs ${selected === sp.id ? "border-brand bg-brand/10" : "border-line hover:border-slate-500"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-100">{sp.id}</span>
                    <span className={`chip ${st.cls}`}>{st.label}</span>
                  </div>
                  {sp.goal && <div className="mt-0.5 line-clamp-2 text-slate-300">{sp.goal}</div>}
                  <div className="mt-1 text-[11px] text-slate-400">
                    {sp.totals ? `${sp.totals.delivered}/${sp.totals.stories} entregues · ${dur(sp.totals.duration_s)} · ${usd(sp.totals.cost_usd)}` : `${sp.story_ids.length} ${sp.story_ids.length === 1 ? "card" : "cards"} · ainda não começou`}
                  </div>
                </button>
              );
            })}
          </aside>
          <section className="min-w-0">
            {!report ? <p className="text-sm text-slate-400">carregando…</p> : (
              <Detail report={report} list={list} tab={tab} setTab={setTab} asTable={asTable} setAsTable={setAsTable} onOpenStory={onOpenStory} onPick={setSelected} />
            )}
          </section>
        </div>
      )}
    </Modal>
  );
}

function Detail({ report, list, tab, setTab, asTable, setAsTable, onOpenStory, onPick }: {
  report: SprintReport; list: SprintListItem[]; tab: Tab; setTab: (t: Tab) => void; asTable: boolean; setAsTable: (f: (v: boolean) => boolean) => void;
  onOpenStory: (id: string) => void; onPick: (id: string) => void;
}) {
  const sp = report.sprint, t = report.totals, prev = report.previous;
  const started = !!sp.started_at;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h4 className="text-lg font-semibold text-slate-100">{sp.id}</h4>
        <span className={`chip ${STATUS[sp.status].cls}`}>{STATUS[sp.status].label}</span>
        <span className="text-xs text-slate-400">{started ? `${day(sp.started_at)} → ${sp.closed_at ? day(sp.closed_at) : "agora"}` : "ainda não começou"}</span>
        {sp.goal && <span className="w-full text-sm text-slate-300">Meta: {sp.goal}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b border-line text-xs">
        {(["overview", "stories", "report"] as Tab[]).map((k) => (
          <button key={k} onClick={() => setTab(k)} className={`px-3 py-1.5 ${tab === k ? "border-b-2 border-brand text-brand" : "text-slate-400"}`}>
            {k === "overview" ? "Visão geral" : k === "stories" ? `Histórias (${report.stories.length})` : "Relatório"}
          </button>
        ))}
        {tab === "overview" && started && <button className="btn-ghost ml-auto mb-1 text-xs" onClick={() => setAsTable((v) => !v)}>{asTable ? "ver gráficos" : "ver como tabela"}</button>}
      </div>
      {!report.measured.traced && started && (
        <p className="text-[11px] text-slate-500">Este sprint rodou antes do rastro da fábrica: a origem e o tempo das tarefas, as respostas cortadas e o custo informado pelo provedor aparecem como “não medido”.</p>
      )}
      {tab === "overview" && (started ? (
        <div className="space-y-5">
          {report.summary && <p className="rounded-md border border-line bg-ink/60 p-3 text-sm leading-relaxed text-slate-200">{report.summary}</p>}
          <Tiles t={t} prev={prev?.totals ?? null} prevId={prev?.id ?? null} />
          <section>
            <h5 className="mb-1 text-sm font-semibold text-slate-200">Andamento do sprint</h5>
            <p className="mb-2 text-[11px] text-slate-500">Histórias do sprint por situação ao longo do tempo; o que falta até o topo ainda não começou.</p>
            {asTable ? <BurnTable report={report} /> : <BurnUp report={report} />}
          </section>
          <div className="grid gap-5 lg:grid-cols-2">
            <section>
              <h5 className="mb-2 text-sm font-semibold text-slate-200">Custo por história</h5>
              <HBars rows={report.stories.map((s) => ({ key: s.id, label: `${s.id} · ${s.title}`, value: s.cost_usd, note: `${s.calls} consultas` }))} fmt={usd} asTable={asTable} head="Custo" onPick={onOpenStory} />
            </section>
            <section>
              <h5 className="mb-2 text-sm font-semibold text-slate-200">Tempo por história</h5>
              <HBars rows={report.stories.map((s) => ({ key: s.id, label: `${s.id} · ${s.title}`, value: s.wall_s, note: `modelo: ${dur(s.model_s)}` }))} fmt={dur} asTable={asTable} head="Tempo" onPick={onOpenStory} />
            </section>
          </div>
          <section>
            <h5 className="mb-1 text-sm font-semibold text-slate-200">Onde o sprint passou o tempo</h5>
            <p className="mb-2 text-[11px] text-slate-500">Tempo somado de todas as histórias em cada etapa.</p>
            <HBars rows={Object.entries(report.stage_time).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ key: k, label: STAGE[k] ?? k, value: v }))} fmt={dur} asTable={asTable} head="Tempo" />
          </section>
          <Unplanned report={report} onOpenStory={onOpenStory} />
          <Compare list={list} current={sp.id} asTable={asTable} onPick={onPick} />
        </div>
      ) : (
        <p className="text-sm text-slate-400">Montado com {report.stories.length} {report.stories.length === 1 ? "história" : "histórias"}; os números aparecem quando ele começar, por uma Reunião de Sprint.</p>
      ))}
      {tab === "stories" && <StoriesTable stories={report.stories} traced={report.measured.traced} onOpenStory={onOpenStory} />}
      {tab === "report" && (
        <div>
          {!report.saved && sp.status !== "closed" && sp.status !== "cancelled" && <p className="mb-2 text-[11px] text-slate-500">Medido agora: o resumo executivo é escrito quando o sprint termina.</p>}
          <Markdown text={report.markdown} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------ tiles

function Tiles({ t, prev, prevId }: { t: SprintTotals; prev: SprintTotals | null; prevId: string | null }) {
  // lower is better for all of these but deliveries: the arrow says which way, the word says if good
  const tiles: { label: string; value: string; now: number | null; before: number | null | undefined; fmt: (v: number) => string; better: "up" | "down" }[] = [
    { label: "Entregues", value: `${t.delivered} de ${t.stories}`, now: t.delivered, before: prev?.delivered, fmt: (v) => String(v), better: "up" },
    { label: "Duração", value: dur(t.duration_s), now: t.duration_s, before: prev?.duration_s, fmt: dur, better: "down" },
    { label: "Custo das histórias", value: usd(t.cost_usd), now: t.cost_usd, before: prev?.cost_usd, fmt: usd, better: "down" },
    { label: "Custo por entrega", value: t.delivered ? usd(t.cost_per_delivered_usd) : "—", now: t.cost_per_delivered_usd, before: prev?.cost_per_delivered_usd, fmt: usd, better: "down" },
    { label: "Retrabalho", value: String(t.rework), now: t.rework, before: prev?.rework, fmt: (v) => String(v), better: "down" },
    { label: "Esperando você (somado)", value: dur(t.founder_wait_s), now: t.founder_wait_s, before: prev?.founder_wait_s, fmt: dur, better: "down" },
  ];
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {tiles.map((x) => {
          const has = x.now != null && x.before != null;
          const diff = has ? (x.now as number) - (x.before as number) : 0;
          const good = diff === 0 ? null : (diff > 0) === (x.better === "up");
          return (
            <div key={x.label} className="rounded-md border border-line bg-ink/60 p-2">
              <div className="text-[11px] text-slate-400">{x.label}</div>
              <div className="text-lg font-semibold text-slate-100">{x.value}</div>
              {has && (
                <div className="text-[11px] text-slate-400">
                  {diff === 0 ? "igual" : `${diff > 0 ? "▲" : "▼"} ${good ? "melhor" : "pior"}`} · antes {x.fmt(x.before as number)}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {prevId && <p className="mt-1 text-[11px] text-slate-500">Comparado com o {prevId}.</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------- burn-up

function burnSeries(report: SprintReport) {
  return report.timeline.map((p) => ({
    at: p.at,
    done: p.DONE,
    waiting: p.AWAITING_FOUNDER,
    working: p.SPEC + p.DEV + p.TEST,
    todo: p.BACKLOG,
  }));
}

function BurnUp({ report }: { report: SprintReport }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = burnSeries(report);
  const total = report.stories.length;
  if (!pts.length || !total) return <p className="text-xs text-slate-500">Sem histórias.</p>;
  const W = 900, H = 220, left = 32, right = 8, top = 8, bottom = 22;
  const plotW = W - left - right, plotH = H - top - bottom;
  const x = (i: number) => left + (i / (pts.length - 1)) * plotW;
  const y = (v: number) => top + plotH - (v / total) * plotH;
  // stacked from the baseline: done, then waiting, then working
  const layers = BURN.map((s, li) => {
    const lower = pts.map((p) => BURN.slice(0, li).reduce((acc, b) => acc + p[b.key], 0));
    const upper = pts.map((p, i) => lower[i] + p[s.key]);
    const path = `M${pts.map((_, i) => `${x(i)},${y(upper[i])}`).join(" L")} L${pts.map((_, i) => `${x(pts.length - 1 - i)},${y(lower[pts.length - 1 - i])}`).join(" L")} Z`;
    return { ...s, path, upper };
  });
  const ticks = Array.from(new Set([0, Math.round(total / 2), total]));
  const h = hover !== null ? pts[hover] : null;
  return (
    <div>
      <Legend items={BURN.map((b) => ({ label: b.label, color: b.color }))} />
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Andamento do sprint: histórias concluídas, aguardando você e em andamento ao longo do tempo"
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const px = ((e.clientX - r.left) / r.width) * W;
            setHover(Math.max(0, Math.min(pts.length - 1, Math.round(((px - left) / plotW) * (pts.length - 1)))));
          }}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
              <text x={left - 6} y={y(t) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{t}</text>
            </g>
          ))}
          {layers.map((l) => <path key={l.key} d={l.path} fill={l.color} fillOpacity={0.85} stroke="#111827" strokeWidth={2} strokeLinejoin="round" />)}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={top} y2={top + plotH} stroke="#94a3b8" strokeWidth={1} strokeDasharray="3 3" />}
          {[0, Math.floor((pts.length - 1) / 2), pts.length - 1].map((i) => (
            <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"} className="fill-slate-500 text-[10px]">{day(pts[i].at)}</text>
          ))}
        </svg>
        {h && hover !== null && (
          <div className="pointer-events-none absolute top-0 rounded-md border border-line bg-ink px-2 py-1 text-xs shadow" style={{ left: `${Math.min(75, (x(hover) / W) * 100)}%` }}>
            <div className="text-slate-400">{day(h.at)}</div>
            {BURN.map((b) => (
              <div key={b.key} className="flex items-center gap-1.5 text-slate-200"><span className="h-2 w-2 rounded-sm" style={{ background: b.color }} />{b.label}: <b>{h[b.key]}</b></div>
            ))}
            <div className="text-slate-400">Não começadas: {h.todo}</div>
          </div>
        )}
      </div>
    </div>
  );
}

function BurnTable({ report }: { report: SprintReport }) {
  const pts = burnSeries(report).filter((_, i, a) => i % 6 === 0 || i === a.length - 1);
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-slate-500"><tr><th className="py-1">Quando</th><th>Concluídas</th><th>Aguardando você</th><th>Em andamento</th><th>Não começadas</th></tr></thead>
      <tbody className="text-slate-300">{pts.map((p) => <tr key={p.at} className="border-t border-line"><td className="py-1">{day(p.at)}</td><td>{p.done}</td><td>{p.waiting}</td><td>{p.working}</td><td>{p.todo}</td></tr>)}</tbody>
    </table>
  );
}

function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="mb-1 flex flex-wrap gap-3 text-[11px] text-slate-300">
      {items.map((i) => <span key={i.label} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} />{i.label}</span>)}
    </div>
  );
}

// ------------------------------------------------------------------------------- bars

type BarRow = { key: string; label: string; value: number; note?: string };

function HBars({ rows, fmt, asTable, head, onPick }: { rows: BarRow[]; fmt: (v: number) => string; asTable: boolean; head: string; onPick?: (key: string) => void }) {
  const shown = [...rows].sort((a, b) => b.value - a.value);
  const max = Math.max(...shown.map((r) => r.value), 0);
  if (!shown.length || max <= 0) return <p className="text-xs text-slate-500">Nada medido.</p>;
  if (asTable) {
    return (
      <table className="w-full text-left text-xs">
        <thead className="text-slate-500"><tr><th className="py-1">Item</th><th>{head}</th><th /></tr></thead>
        <tbody className="text-slate-300">{shown.map((r) => <tr key={r.key} className="border-t border-line"><td className="py-1">{r.label}</td><td>{fmt(r.value)}</td><td className="text-slate-500">{r.note}</td></tr>)}</tbody>
      </table>
    );
  }
  return (
    <ul className="space-y-1.5">
      {shown.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,13rem)_1fr] items-center gap-2 text-xs" title={`${r.label}: ${fmt(r.value)}${r.note ? ` · ${r.note}` : ""}`}>
          {onPick ? <button className="truncate text-left text-slate-300 hover:text-brand" onClick={() => onPick(r.key)}>{r.label}</button> : <span className="truncate text-slate-300">{r.label}</span>}
          <span className="flex items-center gap-2">
            <span className="h-3 rounded-r" style={{ width: `${Math.max(1, (r.value / max) * 75)}%`, background: BAR }} />
            <span className="whitespace-nowrap text-slate-400">{fmt(r.value)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Sprints side by side, one small chart per measure (never two scales on one axis). */
function Compare({ list, current, asTable, onPick }: { list: SprintListItem[]; current: string; asTable: boolean; onPick: (id: string) => void }) {
  const done = list.filter((s) => s.totals);
  if (done.length < 2) return null;
  const measures: { label: string; get: (t: SprintTotals) => number | null; fmt: (v: number) => string }[] = [
    { label: "Custo por história entregue", get: (t) => t.cost_per_delivered_usd, fmt: usd },
    { label: "Duração", get: (t) => t.duration_s, fmt: dur },
    { label: "Retrabalho", get: (t) => t.rework, fmt: (v) => String(v) },
  ];
  return (
    <section>
      <h5 className="mb-2 text-sm font-semibold text-slate-200">Comparação entre sprints</h5>
      <div className="grid gap-4 md:grid-cols-3">
        {measures.map((m) => (
          <div key={m.label}>
            <div className="mb-1 text-xs text-slate-400">{m.label}</div>
            {asTable ? (
              <table className="w-full text-left text-xs"><tbody className="text-slate-300">{done.map((s) => <tr key={s.id} className="border-t border-line"><td className="py-1">{s.id}</td><td>{m.get(s.totals!) == null ? "—" : m.fmt(m.get(s.totals!) as number)}</td></tr>)}</tbody></table>
            ) : (
              <ColumnChart items={done.map((s) => ({ key: s.id, value: m.get(s.totals!), current: s.id === current }))} fmt={m.fmt} onPick={onPick} />
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function ColumnChart({ items, fmt, onPick }: { items: { key: string; value: number | null; current: boolean }[]; fmt: (v: number) => string; onPick: (id: string) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 300, H = 120, top = 16, bottom = 18;
  const plotH = H - top - bottom;
  const max = Math.max(...items.map((i) => i.value ?? 0), 0) || 1;
  const slot = W / items.length;
  const bw = Math.min(28, slot - 2);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseLeave={() => setHover(null)}>
      <line x1={0} x2={W} y1={top + plotH} y2={top + plotH} stroke={GRID} />
      {items.map((it, i) => {
        const h = it.value == null ? 0 : (it.value / max) * plotH;
        const x = i * slot + (slot - bw) / 2;
        const r = Math.min(4, bw / 2, h);
        const d = h > 0 ? `M${x},${top + plotH} V${top + plotH - h + r} Q${x},${top + plotH - h} ${x + r},${top + plotH - h} H${x + bw - r} Q${x + bw},${top + plotH - h} ${x + bw},${top + plotH - h + r} V${top + plotH} Z` : "";
        const showLabel = it.current || hover === i;
        return (
          <g key={it.key} className="cursor-pointer" onMouseEnter={() => setHover(i)} onClick={() => onPick(it.key)}>
            <rect x={i * slot} y={0} width={slot} height={H} fill="transparent" />
            {d && <path d={d} fill={BAR} opacity={it.current || hover === i ? 1 : 0.45} />}
            {showLabel && <text x={x + bw / 2} y={top + plotH - h - 4} textAnchor="middle" className="fill-slate-300 text-[10px]">{it.value == null ? "—" : fmt(it.value)}</text>}
            <text x={x + bw / 2} y={H - 4} textAnchor="middle" className={`text-[10px] ${it.current ? "fill-slate-200" : "fill-slate-500"}`}>{it.key}</text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------- lists, tables

function Unplanned({ report, onOpenStory }: { report: SprintReport; onOpenStory: (id: string) => void }) {
  const un = report.unplanned, later = report.later;
  const tasks = un.tasks_added;
  const ORIGIN: Record<string, string> = { replan: "replanejamento", preflight: "pré-checagem de risco", founder: "ajuste pedido por você", inspector: "correção pedida pelo Inspector" };
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <section>
        <h5 className="mb-1 text-sm font-semibold text-slate-200">O que não estava previsto</h5>
        <ul className="list-disc space-y-1 pl-5 text-xs text-slate-300">
          {un.joined.map((j) => <li key={j.id}><button className="text-brand hover:underline" onClick={() => onOpenStory(j.id)}>{j.id}</button> · {j.title} entrou no meio do sprint ({j.how}).</li>)}
          {tasks == null ? <li className="text-slate-500">Tarefas acrescentadas depois do plano: {NOT_MEASURED}.</li>
            : Object.entries(tasks).map(([o, n]) => <li key={o}>{n} {n === 1 ? "tarefa acrescentada" : "tarefas acrescentadas"} depois do plano: {ORIGIN[o] ?? o}.</li>)}
          {un.extra_review_rounds > 0 && <li>{un.extra_review_rounds} {un.extra_review_rounds === 1 ? "rodada extra" : "rodadas extras"} de revisão do Inspector.</li>}
          {un.spec_rejections > 0 && <li>{un.spec_rejections} {un.spec_rejections === 1 ? "especificação devolvida" : "especificações devolvidas"} pelo Product Owner.</li>}
          {un.replans > 0 && <li>{un.replans} {un.replans === 1 ? "replanejamento" : "replanejamentos"}.</li>}
          {un.restarts > 0 && <li>{un.restarts} {un.restarts === 1 ? "história recomeçada" : "histórias recomeçadas"} do zero.</li>}
        </ul>
      </section>
      <section>
        <h5 className="mb-1 text-sm font-semibold text-slate-200">O que ficou para depois</h5>
        <ul className="list-disc space-y-1 pl-5 text-xs text-slate-300">
          {later.withdrawn.map((w) => <li key={w.id}>{w.id} · {w.title}: devolvida ao backlog.</li>)}
          {later.cards.length > 0 && <li>{later.cards.length} {later.cards.length === 1 ? "card novo" : "cards novos"} no backlog durante o sprint ({later.cards.filter((c) => c.origin === "kaizen").length} do Kaizen).</li>}
          {Object.values(later.findings).reduce((a, b) => a + b, 0) > 0 && <li>{Object.values(later.findings).reduce((a, b) => a + b, 0)} achados do Kaizen sobre o produto.</li>}
          {later.duplicates > 0 && <li>{later.duplicates} achados repetidos apontados para cards que já existiam.</li>}
          {!later.withdrawn.length && !later.cards.length && <li className="text-slate-500">Nada novo entrou no backlog.</li>}
        </ul>
      </section>
    </div>
  );
}

function StoriesTable({ stories, traced, onOpenStory }: { stories: SprintStoryReport[]; traced: boolean; onOpenStory: (id: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            {["História", "Resultado", "Etapa", "Tempo", "Modelo", "Consultas", "Tokens", "Custo", "Tentativas", "Bloqueios", "Respostas suas", "Tarefas"].map((h) => <th key={h} className="whitespace-nowrap py-1 pr-3">{h}</th>)}
          </tr>
        </thead>
        <tbody className="text-slate-300">
          {stories.map((s) => {
            const r = RESULT[s.result];
            const blocks = Object.values(s.blocks).reduce((a, b) => a + b, 0);
            return (
              <tr key={s.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3">
                  <button className="text-left hover:text-brand" onClick={() => onOpenStory(s.id)}><span className="text-slate-500">{s.id}</span> {s.title}</button>
                  {!s.planned && <div className="text-[10px] text-amber-300">entrou no meio · {s.joined_how}</div>}
                </td>
                <td className={`whitespace-nowrap pr-3 ${r.cls}`}>{r.label}</td>
                <td className="whitespace-nowrap pr-3">{STAGE[s.stage] ?? s.stage.toLowerCase()}</td>
                <td className="whitespace-nowrap pr-3" title={Object.entries(s.stage_s).map(([k, v]) => `${STAGE[k] ?? k}: ${dur(v)}`).join("\n")}>{dur(s.wall_s)}</td>
                <td className="whitespace-nowrap pr-3">{dur(s.model_s)}</td>
                <td className="pr-3">{s.calls}</td>
                <td className="whitespace-nowrap pr-3">{((s.input_tokens + s.output_tokens) / 1000).toFixed(0)} mil</td>
                <td className="whitespace-nowrap pr-3">{usd(s.cost_usd)}</td>
                <td className="whitespace-nowrap pr-3">{s.retries + s.restarts}{s.escalations ? ` (+${s.escalations} subida)` : ""}</td>
                <td className="pr-3">{blocks}</td>
                <td className="pr-3">{s.founder_answers}</td>
                <td className="whitespace-nowrap pr-3">{s.tasks == null ? <span className="text-slate-500">{traced ? "—" : NOT_MEASURED}</span> : `${s.tasks.done}${s.tasks.unfinished ? ` (${s.tasks.unfinished} não terminadas)` : ""}`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------- markdown

/** The report's markdown (headings, paragraphs, lists, tables, bold, italics): the same text
 *  as the file and `loompa sprint report`. Small on purpose: the report uses nothing more. */
function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (l.startsWith("#")) {
      const level = l.match(/^#+/)![0].length;
      const cls = level === 1 ? "text-base font-semibold text-slate-100" : "mt-3 text-sm font-semibold text-slate-200";
      blocks.push(<div key={i} className={cls}>{inline(l.replace(/^#+\s*/, ""))}</div>);
      i++;
    } else if (l.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!/^\|\s*-/.test(lines[i])) rows.push(lines[i].split("|").slice(1, -1).map((c) => c.trim()));
        i++;
      }
      blocks.push(
        <div key={i} className="overflow-x-auto"><table className="w-full text-left text-xs">
          <thead className="text-slate-500"><tr>{rows[0].map((c, k) => <th key={k} className="py-1 pr-3">{inline(c)}</th>)}</tr></thead>
          <tbody className="text-slate-300">{rows.slice(1).map((r, k) => <tr key={k} className="border-t border-line">{r.map((c, j) => <td key={j} className="py-1 pr-3">{inline(c)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
    } else if (l.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) items.push(lines[i++].slice(2));
      blocks.push(<ul key={i} className="list-disc space-y-1 pl-5 text-xs text-slate-300">{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</ul>);
    } else {
      blocks.push(<p key={i} className="text-sm leading-relaxed text-slate-300">{inline(l)}</p>);
      i++;
    }
  }
  return <div className="space-y-2">{blocks}</div>;
}

function inline(t: string): React.ReactNode[] {
  return t.split(/(\*\*[^*]+\*\*|_[^_]+_)/g).map((p, k) =>
    p.startsWith("**") ? <b key={k} className="text-slate-100">{p.slice(2, -2)}</b> : p.startsWith("_") && p.endsWith("_") && p.length > 2 ? <i key={k} className="text-slate-400">{p.slice(1, -1)}</i> : p,
  );
}
