import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Modal } from "./Modal";

type Row = { key: string; cost_usd: number; calls: number; input_tokens?: number; output_tokens?: number };
type Day = { day: string; cost_usd: number; calls: number };
type Finance = {
  period: { name: string; totals: { cost_usd: number; calls: number }; by_model: Row[]; by_agent: Row[]; by_role: Row[] };
  by_day: Day[];
  suggestions: string[];
};

const BAR = "#f59e0b"; // brand amber: one series, one hue
const GRID = "#1f2937"; // `line`, one step off the panel surface

const usd = (v: number) => (v >= 1 ? `US$ ${v.toFixed(2)}` : `US$ ${v.toFixed(4)}`);

/** Last 30 UTC days, oldest first, with the days nothing ran filled as zero. */
function lastDays(rows: Day[], n = 30): Day[] {
  const byDay = new Map(rows.map((r) => [r.day, r]));
  const out: Day[] = [];
  const today = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push(byDay.get(key) ?? { day: key, cost_usd: 0, calls: 0 });
  }
  return out;
}

/** Clean tick step for a max value: 1, 2 or 5 times a power of ten. */
function niceMax(max: number): { top: number; step: number } {
  if (max <= 0) return { top: 0.01, step: 0.005 };
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  return { top: Math.ceil(max / step) * step, step };
}

/** A column rounded at its data end (top), square at the baseline. */
function columnPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

function DailyChart({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 900, H = 220, left = 48, bottom = 22, top = 8;
  const plotW = W - left - 8, plotH = H - top - bottom;
  const { top: yMax, step } = niceMax(Math.max(...days.map((d) => d.cost_usd)));
  const slot = plotW / days.length;
  const bw = Math.min(24, slot - 2); // 2px surface gap between neighbours, never wider than 24
  const y = (v: number) => top + plotH - (v / yMax) * plotH;
  const ticks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, i) => i * step);
  const h = hover !== null ? days[hover] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Custo por dia nos últimos 30 dias" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={W - 8} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
            <text x={left - 6} y={y(t) + 3} textAnchor="end" className="fill-slate-500 text-[10px]">{t < 0.01 && t > 0 ? t.toFixed(3) : t.toFixed(2)}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = left + i * slot + (slot - bw) / 2;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              {/* hit target: the whole slot, taller than the mark */}
              <rect x={left + i * slot} y={top} width={slot} height={plotH} fill="transparent" />
              <path d={columnPath(x, y(d.cost_usd), bw, top + plotH - y(d.cost_usd))} fill={BAR} opacity={hover === null || hover === i ? 1 : 0.45} />
            </g>
          );
        })}
        {[0, Math.floor(days.length / 2), days.length - 1].map((i) => (
          <text key={i} x={left + i * slot + slot / 2} y={H - 6} textAnchor="middle" className="fill-slate-500 text-[10px]">{days[i].day.slice(8, 10)}/{days[i].day.slice(5, 7)}</text>
        ))}
      </svg>
      {h && hover !== null && (
        <div className="pointer-events-none absolute top-0 rounded-md border border-line bg-ink px-2 py-1 text-xs shadow" style={{ left: `${Math.min(80, ((left + hover * slot) / W) * 100)}%` }}>
          <div className="text-slate-400">{h.day.slice(8, 10)}/{h.day.slice(5, 7)}</div>
          <div className="font-semibold text-slate-100">{usd(h.cost_usd)}</div>
          <div className="text-slate-400">{h.calls} consultas</div>
        </div>
      )}
    </div>
  );
}

function Bars({ rows, label }: { rows: Row[]; label: (k: string) => string }) {
  const shown = rows.filter((r) => r.cost_usd > 0).slice(0, 8);
  const max = Math.max(...shown.map((r) => r.cost_usd), 0);
  if (!shown.length) return <p className="text-xs text-slate-500">Nada gasto neste período.</p>;
  return (
    <ul className="space-y-1.5">
      {shown.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(0,9rem)_1fr] items-center gap-2 text-xs" title={`${label(r.key)}: ${usd(r.cost_usd)} em ${r.calls} consultas`}>
          <span className="truncate text-slate-300">{label(r.key)}</span>
          <span className="flex items-center gap-2">
            <span className="h-3 rounded-r" style={{ width: `${Math.max(2, (r.cost_usd / max) * 75)}%`, background: BAR }} />
            <span className="whitespace-nowrap text-slate-400">{usd(r.cost_usd)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Table({ rows, head }: { rows: { k: string; cost: number; calls: number }[]; head: string }) {
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-slate-500"><tr><th className="py-1">{head}</th><th>Custo</th><th>Consultas</th></tr></thead>
      <tbody className="text-slate-300">
        {rows.map((r) => <tr key={r.k} className="border-t border-line"><td className="py-1">{r.k}</td><td>{usd(r.cost)}</td><td>{r.calls}</td></tr>)}
      </tbody>
    </table>
  );
}

const ROLE_LABEL: Record<string, string> = {
  master: "Master", product: "Spec", product_owner: "Product Owner", architect: "Architect", worker: "Worker",
  inspector: "Inspector", deployer: "Deployer", analyst: "Analyst", kaizen: "Kaizen", ops: "Ops",
};

export default function FinanceModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [data, setData] = useState<Finance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asTable, setAsTable] = useState(false);
  useEffect(() => { api.finance(slug).then(setData).catch((e) => setError(String(e))); }, [slug]);
  const days = useMemo(() => lastDays(data?.by_day ?? []), [data]);
  const period = data?.period.name === "weekly" ? "nesta semana" : "neste mês";
  return (
    <Modal title="💰 Custos" onClose={onClose} wide>
      {error && <p className="text-sm text-red-300">{error}</p>}
      {!data && !error && <p className="text-sm text-slate-400">carregando…</p>}
      {data && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-baseline gap-6">
            <div><div className="text-2xl font-semibold text-slate-100">{usd(data.period.totals.cost_usd)}</div><div className="text-xs text-slate-400">{period} · {data.period.totals.calls} consultas</div></div>
            <div><div className="text-2xl font-semibold text-slate-100">{usd(days.reduce((s, d) => s + d.cost_usd, 0))}</div><div className="text-xs text-slate-400">últimos 30 dias</div></div>
            <button className="btn-ghost ml-auto text-xs" onClick={() => setAsTable((v) => !v)}>{asTable ? "ver gráficos" : "ver como tabela"}</button>
          </div>
          <section>
            <h4 className="mb-1 text-sm font-semibold text-slate-200">Custo por dia · últimos 30 dias</h4>
            {asTable
              ? <Table head="Dia" rows={days.filter((d) => d.calls > 0).map((d) => ({ k: d.day, cost: d.cost_usd, calls: d.calls }))} />
              : <DailyChart days={days} />}
          </section>
          <div className="grid gap-5 md:grid-cols-2">
            <section>
              <h4 className="mb-2 text-sm font-semibold text-slate-200">Por papel · {period}</h4>
              {asTable
                ? <Table head="Papel" rows={data.period.by_role.map((r) => ({ k: ROLE_LABEL[r.key] ?? r.key, cost: r.cost_usd, calls: r.calls }))} />
                : <Bars rows={data.period.by_role} label={(k) => ROLE_LABEL[k] ?? k} />}
            </section>
            <section>
              <h4 className="mb-2 text-sm font-semibold text-slate-200">Por modelo · {period}</h4>
              {asTable
                ? <Table head="Modelo" rows={data.period.by_model.map((r) => ({ k: r.key, cost: r.cost_usd, calls: r.calls }))} />
                : <Bars rows={data.period.by_model} label={(k) => k.split("/").pop() ?? k} />}
            </section>
          </div>
          {data.suggestions.length > 0 && (
            <section>
              <h4 className="mb-1 text-sm font-semibold text-slate-200">Sugestões do Finance Loompa</h4>
              <ul className="list-disc space-y-1 pl-5 text-xs text-slate-300">{data.suggestions.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </section>
          )}
        </div>
      )}
    </Modal>
  );
}
