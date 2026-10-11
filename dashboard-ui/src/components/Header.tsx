import type { EngineState, FactoryRef, InFlight, Overview } from "../types";
import logo from "../assets/loompa-logo.png";

export default function Header(props: {
  factories: FactoryRef[]; slug: string | null; overview: Overview | null; connected: boolean; pending: number;
  onSwitch: (slug: string) => void; onNewFactory: () => void; onEngine: (action: "start" | "pause" | "stop") => void; onSettings: () => void; onFinance: () => void; onSprints: () => void; onFactory: () => void;
  onDesign: () => void; designPending: number;
}) {
  const { factories, slug, overview, connected, pending } = props;
  const fin = overview?.finance;
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-2">
      <div className="flex items-center gap-2">
        <img src={logo} alt="" className="h-7 w-7" />
        <span className="font-pixel text-[11px] tracking-wider text-brand">LOOMPA LTDA</span>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <span className="hidden text-slate-400 2xl:inline">Empresa ativa</span>
        <select className="rounded-md border border-line bg-ink px-2 py-1 text-sm" value={slug ?? ""} onChange={(e) => props.onSwitch(e.target.value)}>
          {factories.length === 0 && <option value="">(nenhuma)</option>}
          {factories.map((f) => <option key={f.slug} value={f.slug} disabled={!f.exists}>{f.name}{f.engine ? " ●" : ""}</option>)}
        </select>
      </label>
      <button className="btn-ghost" onClick={props.onNewFactory}>+ Nova Fábrica</button>
      <div className="mx-auto" />
      {overview && (
        <>
          <span className="chip bg-slate-800 text-slate-300">{overview.factory.mode}</span>
          {overview.factory.dry_run && <span className="chip bg-amber-900/60 text-amber-200">simulação</span>}
          {fin && (
            <button className={`rounded-md px-1.5 py-0.5 text-sm hover:bg-line ${fin.warn ? "text-amber-300" : "text-slate-300"}`} title="Custos por dia, papel e modelo" onClick={props.onFinance}>
              💰 US$ {fin.today_usd.toFixed(2)} hoje · {fin.period_usd.toFixed(2)}/{fin.cap_usd.toFixed(2)} {fin.period === "weekly" ? "na semana" : "no mês"}
            </button>
          )}
          <button className="btn-ghost" title="Histórico de sprints: andamento, custo, tempo e relatórios" onClick={props.onSprints}>🏁 Sprints</button>
          <button className="btn-ghost" title="Telas para aprovar, base visual e referências" onClick={props.onDesign}>🎨 Design{props.designPending > 0 ? ` (${props.designPending})` : ""}</button>
          <button className="btn-ghost" title="O que a fábrica achou sobre si mesma: problemas do Loompa, não do produto" onClick={props.onFactory}>🏭 Fábrica</button>
          <span className="text-sm" title="Decisões aguardando você">📬 {pending}</span>
          <EngineButtons state={overview.factory.engine_state ?? (overview.factory.engine ? "running" : "stopped")} inFlight={overview.factory.in_flight ?? []} onEngine={props.onEngine} />
          <button className="btn-ghost" title="Provedores, modelos, chaves e orçamento" onClick={props.onSettings}>⚙ Configurações</button>
        </>
      )}
      <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} title={connected ? "tempo real conectado" : "reconectando…"} />
    </header>
  );
}

/** Play and pause take turns; stop sits beside them, enabled only while something is in flight
 *  (running, or pausing while the calls in flight finish) and always confirmed first. */
function EngineButtons({ state, inFlight, onEngine }: { state: EngineState; inFlight: InFlight[]; onEngine: (a: "start" | "pause" | "stop") => void }) {
  const playing = state === "running";
  const busy = (state === "running" || state === "pausing") && inFlight.length > 0;
  const stop = () => {
    const what = inFlight.map((f) => `• ${f.story} — há ${Math.max(1, Math.round(f.minutes))} min${f.doing ? ` (${f.doing})` : ""}`).join("\n");
    const ok = window.confirm(
      `Parar a esteira agora?\n\nIsto interrompe o que está em andamento, inclusive as chamadas aos modelos no meio, e esse trabalho será refeito quando a esteira voltar:\n${what}\n\nPara esperar as chamadas terminarem sem perder nada, use Pausar.`,
    );
    if (ok) onEngine("stop");
  };
  const label = { running: "⏸ Pausar", pausing: "▶ Retomar", paused: "▶ Retomar", stopped: "▶ Ligar esteira" }[state];
  const title = {
    running: "Não começa nada novo; as chamadas em andamento terminam e cada história para onde estiver, sem perder trabalho",
    pausing: `Pausando: ${inFlight.length} história(s) terminando a chamada em andamento. Retomar cancela a pausa`,
    paused: "Pausada: nada rodando. Retomar segue de onde cada história parou",
    stopped: "Liga a esteira",
  }[state];
  return (
    <span className="flex items-center gap-1">
      {state === "pausing" && <span className="chip bg-amber-900/60 text-amber-200" title={title}>pausando…</span>}
      {state === "paused" && <span className="chip bg-slate-800 text-slate-300">pausada</span>}
      <button className={playing ? "btn-ghost" : "btn-primary"} title={title} onClick={() => onEngine(playing ? "pause" : "start")}>{label}</button>
      <button className="btn-ghost disabled:opacity-40" disabled={!busy} title={busy ? "Interrompe tudo agora (pede confirmação)" : "Nada em andamento"} onClick={stop}>⏹ Parar</button>
    </span>
  );
}
