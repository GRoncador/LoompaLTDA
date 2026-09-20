import { useEffect, useState } from "react";
import { api } from "../api";
import { ProviderIcon, cleanModelId } from "./ProviderIcon";

const CLUSTER_LABELS: Record<string, string> = {
  strategy: "🏛️ Estratégia & Produto (50% INTEL · 30% CODE · 20% AGENTIC)",
  engineering: "⚙️ Engenharia de Código (60% CODE · 30% AGENTIC · 10% INTEL)",
  routine: "📋 Rotina & Suporte (55% AGENTIC · 30% INTEL · 15% CODE)",
};

export default function AgentDrawer({ slug, name, onClose, onOpenStory }: { slug: string; name: string; onClose: () => void; onOpenStory: (id: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.agent(slug, name).then(setData).catch(() => setData(null)); const t = setInterval(() => api.agent(slug, name).then(setData).catch(() => {}), 5000); return () => clearInterval(t); }, [slug, name]);
  return (
    <Drawer title={name} onClose={onClose}>
      {!data ? <p className="text-sm text-slate-400">carregando…</p> : (
        <div className="space-y-3 text-sm">
          <Row k="Papel" v={data.role} />
          {data.cluster && (
            <Row k="Cluster" v={<span className="font-semibold text-slate-200">{CLUSTER_LABELS[data.cluster] || data.cluster}</span>} />
          )}
          <Row k="Estado" v={<span className={`chip ${data.state === "BLOCKED" ? "bg-red-900/60 text-red-200" : data.state === "WORKING" ? "bg-emerald-900/60 text-emerald-200" : data.state === "TESTING" ? "bg-sky-900/60 text-sky-200" : "bg-slate-800 text-slate-300"}`}>{data.state}</span>} />
          <Row k="Modelo ativo" v={
            data.model ? (
              <span className="flex items-center gap-1.5 font-mono text-cyan-300" title={data.model}>
                <ProviderIcon provider="openrouter" model={data.model} className="w-4 h-4 flex-shrink-0" />
                <span>{cleanModelId(data.model)}</span>
              </span>
            ) : "—"
          } />
          <Row k="Tier" v={
            <select className="rounded-md border border-line bg-ink px-2 py-0.5 text-sm" value={data.tier || "tier2"} disabled={!data.role || saving}
              onChange={async (e) => { setSaving(true); try { await api.updateSettings(slug, { roles: { [data.role]: e.target.value } }); setData(await api.agent(slug, name)); } finally { setSaving(false); } }}>
              {(data.tiers ?? ["tier1", "tier2", "tier3"]).map((t: string) => <option key={t} value={t}>{t}</option>)}
            </select>} />
          
          {data.matrix ? (
            <div className="rounded-md border border-line/70 bg-ink/40 p-2.5 space-y-2">
              <div className="text-xs font-semibold text-slate-400">Matriz de Modelos do Cluster ({CLUSTER_LABELS[data.cluster] || data.cluster})</div>
              {(["tier1", "tier2", "tier3"] as const).map((t) => {
                const cands = data.matrix[t] || [];
                const isSelected = data.tier === t;
                return (
                  <div key={t} className={`rounded p-1.5 text-xs ${isSelected ? "border border-brand/50 bg-brand/10" : "border border-line/40 bg-slate-900/40"}`}>
                    <div className="font-medium text-slate-300 flex items-center justify-between">
                      <span className={isSelected ? "text-brand font-semibold" : ""}>{t.toUpperCase()}{isSelected ? " (Selecionado)" : ""}</span>
                      <span className="text-[10px] text-slate-500">{cands.length} modelo(s)</span>
                    </div>
                    {cands.length > 0 ? (
                      <ol className="list-decimal pl-4 mt-1 space-y-1 font-mono text-[11px] text-slate-300">
                        {cands.map((c: any, i: number) => (
                          <li key={`${c.provider}/${c.model}-${i}`} className="flex items-center gap-1.5" title={c.model}>
                            <ProviderIcon provider={c.provider} model={c.model} className="w-3.5 h-3.5 flex-shrink-0" />
                            <span>{cleanModelId(c.model)}</span>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <span className="text-[11px] text-slate-500 italic">nenhum modelo configurado</span>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <Row k="Modelos" v={(data.candidates ?? []).length ? <ol className="list-decimal pl-4 text-xs text-slate-300">{data.candidates.map((c: any) => <li key={`${c.provider}/${c.model}`}>{c.provider} / {c.model}</li>)}</ol> : <span className="text-slate-500">nenhum modelo neste tier</span>} />
          )}

          <Row k="Atividade" v={data.detail || "—"} />
          {data.story && <Row k="História" v={<button className="text-brand hover:underline" onClick={() => onOpenStory(data.story.id)}>{data.story.id} · {data.story.title}</button>} />}
          {data.worktree && <Row k="Worktree" v={<code className="text-xs text-slate-400">{data.worktree}</code>} />}
          <div className="rounded-md border border-line bg-ink/60 p-3">
            <div className="mb-1 text-xs font-semibold text-slate-400">Telemetria Financeira (Backend)</div>
            <Usage label="Hoje" u={data.today} />
            <Usage label="Mês" u={data.month} />
          </div>
        </div>
      )}
    </Drawer>
  );
}

function Usage({ label, u }: { label: string; u: any }) {
  if (!u) return <div className="text-xs text-slate-500">{label}: sem consultas</div>;
  return <div className="text-xs">{label}: <b>US$ {Number(u.cost_usd).toFixed(4)}</b> · {u.calls} consultas · {u.input_tokens} in / {u.output_tokens} out tokens</div>;
}

export function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex gap-3"><span className="w-28 shrink-0 text-slate-500">{k}</span><span className="min-w-0 flex-1 break-words">{v}</span></div>;
}

export function Drawer({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/40" onClick={onClose}>
      <aside className="scroll-thin h-full w-full max-w-lg overflow-y-auto border-l border-line bg-panel p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold">{title}</h3>
          <button className="text-slate-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        {children}
      </aside>
    </div>
  );
}
