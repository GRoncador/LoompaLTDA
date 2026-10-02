import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { FactoryFinding, FactoryScan } from "../types";
import { Modal } from "./Modal";

const SEVERITY: Record<string, { label: string; cls: string }> = {
  high: { label: "alta", cls: "bg-red-900/60 text-red-200" },
  medium: { label: "média", cls: "bg-amber-900/60 text-amber-200" },
  low: { label: "baixa", cls: "bg-slate-800 text-slate-300" },
};
const IMPACT: Record<string, string> = { calls: "chamadas", minutes: "min", usd: "US$" };

/** The factory's self-diagnosis (Fase 8.4): problems of Loompa itself, measured in code and kept
 *  in the hub. Not the product's improvements (those are Kaizen cards in the backlog). */
export default function FactoryModal({ slug, onClose, onOpenSprint, onOpenStory }: {
  slug: string; onClose: () => void; onOpenSprint: (id: string) => void; onOpenStory: (id: string) => void;
}) {
  const [status, setStatus] = useState<"open" | "all">("open");
  const [findings, setFindings] = useState<FactoryFinding[] | null>(null);
  const [scans, setScans] = useState<FactoryScan[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { const r = await api.factoryHealth(status); setFindings(r.findings); setScans(r.scans); setErr(null); } catch (e) { setErr(String(e)); }
  }, [status]);
  useEffect(() => { load(); }, [load]);

  const scan = async () => {
    setBusy(true); setNote(null);
    try {
      const r = await api.scanFactory(slug);
      setNote(`${r.found} achados nos últimos 7 dias: ${r.new.length} novos, ${r.back.length} voltaram, ${r.confirmed.length} correções confirmadas.`);
      await load();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const sprints = [...new Set(scans.filter((s) => s.factory === slug && s.sprint_id).map((s) => s.sprint_id as string))].sort();

  return (
    <Modal wide title="🏭 Fábrica — o que o Loompa achou sobre si mesmo" onClose={onClose}>
      <p className="text-xs text-slate-400">
        Problemas da própria fábrica (não do produto), medidos em código a cada sprint: cortes, repetições, esperas, integração, custo.
        Não viram cards no backlog: leve-os a uma sessão de desenvolvimento do Loompa (<code>loompa factory-health --export</code>).
        Um sinal que some depois de uma correção confirma a correção.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <button className={status === "open" ? "btn-primary" : "btn-ghost"} onClick={() => setStatus("open")}>Abertos</button>
        <button className={status === "all" ? "btn-primary" : "btn-ghost"} onClick={() => setStatus("all")}>Todos</button>
        <div className="mx-auto" />
        {sprints.length > 0 && (
          <span className="text-slate-500">relatórios:{" "}
            {sprints.map((id) => <button key={id} className="ml-1 text-sky-300 hover:underline" onClick={() => onOpenSprint(id)}>{id}</button>)}
          </span>
        )}
        <button className="btn-ghost" disabled={busy} title="Procura os sinais nos últimos 7 dias desta fábrica" onClick={scan}>{busy ? "procurando…" : "🔎 Procurar agora"}</button>
      </div>
      {note && <p className="mt-2 text-xs text-emerald-300">{note}</p>}
      {err && <p className="mt-2 text-xs text-red-300">{err}</p>}
      <ul className="mt-3 space-y-2">
        {findings === null && <li className="text-xs text-slate-500">carregando…</li>}
        {findings?.length === 0 && <li className="text-xs text-slate-500">Nenhum achado {status === "open" ? "aberto" : ""}. A próxima varredura roda quando um sprint fechar.</li>}
        {findings?.map((f) => <FindingCard key={f.signature} f={f} slug={slug} onChanged={load} onOpenSprint={onOpenSprint} onOpenStory={onOpenStory} />)}
      </ul>
    </Modal>
  );
}

function FindingCard({ f, slug, onChanged, onOpenSprint, onOpenStory }: {
  f: FactoryFinding; slug: string; onChanged: () => void; onOpenSprint: (id: string) => void; onOpenStory: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [commit, setCommit] = useState("");
  const sev = SEVERITY[f.severity] ?? SEVERITY.low;
  const resolved = f.status === "resolved";
  const here = f.factories.includes(slug);
  return (
    <li className={`rounded-md border border-line bg-panel p-2 text-xs ${resolved ? "opacity-70" : ""}`}>
      <button className="w-full text-left" onClick={() => setOpen(!open)}>
        <div className="flex flex-wrap items-center gap-1 text-[10px]">
          <span className={`chip ${sev.cls}`}>{sev.label}</span>
          <span className="chip bg-slate-800 text-slate-400">{f.area}</span>
          {!!f.provisional && <span className="chip bg-indigo-900/50 text-indigo-200" title="Limiar ainda não calibrado: lê o rastro, que a Sprint 1 não tinha">provisório</span>}
          {resolved && <span className="chip bg-emerald-900/50 text-emerald-200">resolvido {f.resolved_commit}</span>}
          <span className="text-slate-500">visto em {f.seen_in}</span>
          <span className="ml-auto flex items-center gap-0.5" title="Varreduras, a mais recente à direita: ● visto · ○ ausente">
            {f.trend.map((t, i) => <span key={i} className={t.seen ? "text-amber-300" : "text-slate-600"} title={`${t.sprint_id ?? "período"} · ${t.factory}`}>{t.seen ? "●" : "○"}</span>)}
          </span>
        </div>
        <div className="mt-1 font-medium text-slate-100">{f.title}</div>
        <div className="text-slate-400">{f.detail}</div>
        {Object.keys(f.impact).length > 0 && (
          <div className="mt-0.5 text-slate-500">impacto: {Object.entries(f.impact).map(([k, v]) => `${k === "usd" ? `US$ ${v.toFixed(2)}` : `${v} ${IMPACT[k] ?? k}`}`).join(" · ")}</div>
        )}
      </button>
      {open && (
        <div className="mt-2 space-y-1 border-t border-white/10 pt-2">
          {f.hypothesis && <p className="text-sky-200"><span className="text-slate-500">hipótese do Ops:</span> {f.hypothesis}</p>}
          {f.fix && <p className="text-sky-200"><span className="text-slate-500">correção sugerida (hipótese):</span> {f.fix}</p>}
          <div className="text-slate-500">evidência:</div>
          <ul className="space-y-0.5 text-slate-400">
            {f.evidence.slice(0, 8).map((ev, i) => {
              const story = typeof ev.story === "string" ? ev.story : null;
              const rest = Object.entries(ev).filter(([k]) => k !== "story" && k !== "command");
              return (
                <li key={i} className="font-mono text-[10px]">
                  {story && here ? <button className="text-sky-300 hover:underline" onClick={() => onOpenStory(story)}>{story}</button> : story}
                  {rest.length > 0 && ` ${rest.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" ")}`}
                  {typeof ev.command === "string" && ev.command && <span className="ml-1 text-slate-500">→ {ev.command}</span>}
                </li>
              );
            })}
          </ul>
          {f.trend.some((t) => t.sprint_id && t.factory === slug) && (
            <div className="text-slate-500">sprints:{" "}
              {f.trend.filter((t) => t.sprint_id && t.factory === slug).map((t, i) => (
                <button key={i} className={`ml-1 hover:underline ${t.seen ? "text-amber-300" : "text-slate-500"}`} onClick={() => onOpenSprint(t.sprint_id as string)}>{t.sprint_id}{t.seen ? "" : " (ausente)"}</button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            {resolved ? (
              <button className="btn-ghost text-[11px]" onClick={async () => { await api.reopenFinding(f.signature); onChanged(); }}>Reabrir</button>
            ) : (
              <>
                <input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="commit do Loompa que corrigiu" className="w-48 rounded border border-line bg-ink px-2 py-0.5 text-[11px]" />
                <button className="btn-ghost text-[11px]" disabled={!commit.trim()} onClick={async () => { await api.resolveFinding(f.signature, commit.trim()); onChanged(); }}>Marcar resolvido</button>
              </>
            )}
            <code className="ml-auto text-[10px] text-slate-600">{f.signature}</code>
          </div>
        </div>
      )}
    </li>
  );
}
