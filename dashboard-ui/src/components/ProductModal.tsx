import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProductPhase, ProductView } from "../types";
import { Modal } from "./Modal";
import { Icon } from "./Icon";

/** The product's life cycle (plan 16): the phases with their goal, exit criterion and facts; the
 *  product epics with their progress; the PM's proposals. Moving to a phase is the founder's call. */
export default function ProductModal({ slug, onClose, onBrainstorm }: { slug: string; onClose: () => void; onBrainstorm?: (text: string) => void }) {
  const [view, setView] = useState<ProductView | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.product(slug).then(setView).catch((e) => setNote(String(e))); }, [slug]);
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setNote(null);
    try { const n = await fn(); if (n) setNote(n); setView(await api.product(slug)); } catch (e) { setNote(String(e)); } finally { setBusy(false); }
  };
  if (!view) return <Modal title={<><Icon name="compass" className="mr-1.5 inline h-4 w-4 align-[-2px]" />Produto</>} onClose={onClose}><p className="text-sm text-slate-400">{note ?? "carregando…"}</p></Modal>;
  const current = view.phases.find((p) => p.status === "current");
  return (
    <Modal title={<><Icon name="compass" className="mr-1.5 inline h-4 w-4 align-[-2px]" />Produto · {view.kind_label}</>} onClose={onClose} className="w-[96vw] max-w-5xl">
      <div className="space-y-4 text-sm">
        {note && <p className="text-xs text-amber-300">{note}</p>}
        <section className="space-y-2">
          <h4 className="font-semibold text-slate-100">Fases</h4>
          <div className="flex flex-wrap gap-1">
            {view.phases.map((p) => (
              <span key={p.key} className={`chip ${p.status === "current" ? "bg-violet-800/70 text-violet-100" : p.status === "done" ? "bg-emerald-900/50 text-emerald-200" : "bg-slate-800 text-slate-400"}`}>
                {p.status === "done" ? <Icon name="check" className="mr-1 inline h-3 w-3 align-[-1px]" /> : null}{p.name}{p.cards > 0 ? ` · ${p.percent}%` : ""}
              </span>
            ))}
          </div>
          {current && <PhaseForm key={current.key} slug={slug} phase={current} busy={busy} run={run} />}
          {view.phases.length > 1 && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <button className="btn-ghost" disabled={busy} onClick={() => run(async () => {
                const r = await api.reviewPhase(slug);
                return r.asked ? "O PM acha que o critério foi cumprido: a decisão está na Caixa de Entrada." : `PM: ${r.evidence}${r.missing.length ? ` · falta saber: ${r.missing.join("; ")}` : ""}`;
              })}>O critério de saída já foi cumprido?</button>
              {view.phases.filter((p) => p.status === "planned").slice(0, 1).map((p) => (
                <button key={p.key} className="btn-ghost" disabled={busy} onClick={() => { if (window.confirm(`Passar o produto para a fase ${p.name}?`)) run(async () => { await api.startPhase(slug, p.key); }); }}>Passar para {p.name}</button>
              ))}
            </div>
          )}
        </section>
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="font-semibold text-slate-100">Épicos{current ? ` · ${current.name}` : ""}</h4>
            <button className="btn-ghost text-xs" disabled={busy} onClick={() => run(async () => {
              const r = await api.proposeEpics(slug);
              return r.proposed ? `O PM propôs ${r.proposed} épico(s): decida na Caixa de Entrada.` : "O PM não viu épico novo para esta fase.";
            })}>{busy ? "…" : "Pedir épicos ao PM"}</button>
          </div>
          {view.epics.length === 0 && <p className="text-xs text-slate-400">Nenhum épico ainda. Os épicos nascem do PM, com o seu OK, e viram cards pelo Product Owner na reunião de sprint.</p>}
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {view.epics.map((e) => (
              <div key={e.id} className={`rounded-md border p-2 text-xs ${e.status === "active" ? "border-line" : "border-line/40 opacity-60"}`}>
                <div className="flex items-center gap-2">
                  <span className="text-slate-500">{e.id}</span>
                  <span className="font-medium text-slate-100">{e.name}</span>
                  <span className="ml-auto tabular-nums text-slate-400">{e.done}/{e.cards} cards</span>
                </div>
                {e.goal && <p className="mt-1 text-slate-300">{e.goal}</p>}
                {e.metric && <p className="text-slate-400">Métrica: {e.metric}</p>}
                <div className="mt-2 flex items-center gap-2">
                  {e.status === "active" && onBrainstorm && (
                    <button
                      className="btn-ghost px-2 py-0.5 text-[11px] text-amber-300 hover:text-amber-200"
                      title="Abrir brainstorm focado em funcionalidades para concluir este épico"
                      onClick={() => {
                        const prompt = `Quero fazer um brainstorm focado no épico ativo '${e.name}' (${e.id})${e.goal ? ` · Meta: ${e.goal}` : ""}${e.metric ? ` · Métrica: ${e.metric}` : ""}. Analise o que já temos no produto e no backlog, e proponha a direção e as ideias/funcionalidades que ainda faltam para que este épico seja concluído com sucesso.`;
                        onBrainstorm(prompt);
                      }}
                    >
                      <Icon name="lightbulb" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" /> brainstorm
                    </button>
                  )}
                  {e.status !== "done" && <button className="text-emerald-300 hover:underline" onClick={() => run(async () => { await api.setEpic(slug, e.id, "done"); })}>concluir</button>}
                  {e.status === "active" && <button className="text-slate-400 hover:underline" onClick={() => run(async () => { await api.setEpic(slug, e.id, "dropped"); })}>abandonar</button>}
                  {e.status !== "active" && <button className="text-slate-400 hover:underline" onClick={() => run(async () => { await api.setEpic(slug, e.id, "active"); })}>reativar</button>}
                </div>
              </div>
            ))}
          </div>
        </section>
        {view.discovery && <p className="text-[11px] text-slate-500">A descoberta (Lean Canvas, concorrentes e hipóteses) é pedida no brainstorm, com o botão “Descoberta”.</p>}
      </div>
    </Modal>
  );
}

function PhaseForm({ slug, phase, busy, run }: { slug: string; phase: ProductPhase; busy: boolean; run: (fn: () => Promise<string | void>) => Promise<void> }) {
  const [goal, setGoal] = useState(phase.goal);
  const [exit, setExit] = useState(phase.exit_criterion);
  const [facts, setFacts] = useState(phase.facts);
  const field = "mt-1 w-full rounded-md border border-line bg-ink px-2 py-1 text-sm";
  return (
    <div className="space-y-2 rounded-md border border-line/60 bg-raised/60 p-2">
      <label className="block text-xs text-slate-400">Objetivo da fase {phase.name}
        <input className={field} value={goal} onChange={(e) => setGoal(e.target.value)} />
      </label>
      <label className="block text-xs text-slate-400">Critério de saída (ex.: “10 pessoas usando por 2 semanas”)
        <input className={field} value={exit} onChange={(e) => setExit(e.target.value)} />
      </label>
      {phase.measure && <p className="text-xs text-slate-400">Como medir (Analista de Dados): {phase.measure}</p>}
      <label className="block text-xs text-slate-400">O que você sabe de fora da fábrica (usuários, conversão, receita)
        <textarea className={field} rows={2} value={facts} onChange={(e) => setFacts(e.target.value)} />
      </label>
      <div className="text-right">
        <button className="btn-primary" disabled={busy} onClick={() => run(async () => { await api.setPhase(slug, phase.key, { goal, exit_criterion: exit, facts }); })}>Salvar fase</button>
      </div>
    </div>
  );
}
