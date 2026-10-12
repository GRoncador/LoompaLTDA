import type { EngineState, FactoryRef, InFlight, Overview } from "../types";
import logo from "../assets/loompa-logo.png";

export default function Header(props: {
  factories: FactoryRef[]; slug: string | null; overview: Overview | null; connected: boolean;
  onSwitch: (slug: string) => void; onNewFactory: () => void; onEngine: (action: "start" | "pause" | "stop") => void; onSettings: () => void; onFinance: () => void; onSprints: () => void; onFactory: () => void;
  onDesign: () => void; designPending: number; onGrowth?: () => void;
}) {
  const { factories, slug, overview, connected } = props;
  const fin = overview?.finance;
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-2">
      {/* the logo opens what the Loompa found about itself: it is the Loompa's, not a factory's */}
      <button className="flex items-center gap-2 rounded-md px-1 hover:bg-line disabled:hover:bg-transparent" disabled={!slug} title="O que o Loompa achou sobre si mesmo: ajustes e correções do próprio Loompa" onClick={props.onFactory}>
        <img src={logo} alt="" className="h-7 w-7" />
        <span className="font-pixel text-[11px] tracking-wider text-brand">LOOMPA LTDA</span>
      </button>
      <label className="flex items-center gap-2 text-sm">
        <span className="hidden text-slate-400 2xl:inline">Empresa ativa</span>
        {/* the last option opens the new-factory wizard instead of switching to it */}
        <select className="rounded-md border border-line bg-ink px-2 py-1 text-sm" value={slug ?? ""} onChange={(e) => (e.target.value === NEW ? props.onNewFactory() : props.onSwitch(e.target.value))}>
          {factories.length === 0 && <option value="">(nenhuma)</option>}
          {factories.map((f) => <option key={f.slug} value={f.slug} disabled={!f.exists}>{f.name}{f.engine ? " ●" : ""}</option>)}
          <option value={NEW}>+ Nova fábrica…</option>
        </select>
      </label>
      {overview && (
        <>
          {/* only the gear: same size and hover as the budget item beside it */}
          <button className="flex items-center rounded-md px-1.5 py-0.5 text-sm text-slate-300 hover:bg-line" aria-label="Configurações" title="Configurações: provedores, modelos, chaves e orçamento" onClick={props.onSettings}>⚙️</button>
          {fin && (
            <button
              className={`flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-sm hover:bg-line ${fin.warn ? "text-amber-300" : "text-slate-300"}`}
              title={`Gasto de hoje: US$ ${fin.today_usd.toFixed(2)}\n${fin.period === "weekly" ? "Na semana" : "No mês"}: US$ ${fin.period_usd.toFixed(2)} de US$ ${fin.cap_usd.toFixed(2)}\nClique para ver custos por dia, papel e modelo`}
              onClick={props.onFinance}
            >
              💰 ${fin.today_usd.toFixed(2)}
              <BudgetRing used={fin.cap_usd > 0 ? fin.period_usd / fin.cap_usd : 0} warn={fin.warn} />
            </button>
          )}
          {overview.factory.dry_run && <span className="chip bg-amber-900/60 text-amber-200">simulação</span>}
        </>
      )}
      {/* the right side: sprints and design, then stop, then play/pause at the far edge */}
      <div className="ml-auto flex items-center gap-3">
        {overview && (
          <>
            <button className="btn-ghost" title="Histórico de sprints: andamento, custo, tempo e relatórios" onClick={props.onSprints}>🏁 Sprints</button>
            <button className="btn-ghost" title="Telas para aprovar, base visual e referências" onClick={props.onDesign}>🎨 Design{props.designPending > 0 ? ` (${props.designPending})` : ""}</button>
            {props.onGrowth && <button className="btn-ghost" title="Plano de marketing, textos, e-mails, posts e anúncios para você revisar" onClick={props.onGrowth}>📣 Growth</button>}
            <EngineButtons state={overview.factory.engine_state ?? (overview.factory.engine ? "running" : "stopped")} inFlight={overview.factory.in_flight ?? []} onEngine={props.onEngine} />
          </>
        )}
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} title={connected ? "tempo real conectado" : "reconectando…"} />
      </div>
    </header>
  );
}

const NEW = "__new__";

/** How much of the period's budget is spent, as a small ring with the percentage beside it:
 *  the exact amounts are in the tooltip and in the costs panel. */
function BudgetRing({ used, warn }: { used: number; warn: boolean }) {
  const pct = Math.round(Math.min(Math.max(used, 0), 1) * 100);
  const r = 6;
  const c = 2 * Math.PI * r;
  const color = warn || pct >= 80 ? "#fbbf24" : "#34d399";
  return (
    <span className="flex items-center gap-1 text-xs text-slate-400" aria-label={`${pct}% do orçamento usado`}>
      <svg viewBox="0 0 16 16" className="h-4 w-4 -rotate-90">
        <circle cx="8" cy="8" r={r} fill="none" stroke="#334155" strokeWidth="2.5" />
        <circle cx="8" cy="8" r={r} fill="none" stroke={color} strokeWidth="2.5" strokeDasharray={`${(c * pct) / 100} ${c}`} strokeLinecap="round" />
      </svg>
      {pct}%
    </span>
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
      <button className="btn-ghost disabled:opacity-40" disabled={!busy} title={busy ? "Interrompe tudo agora (pede confirmação)" : "Nada em andamento"} onClick={stop}>⏹ Parar</button>
      <button className={playing ? "btn-ghost" : "btn-primary"} title={title} onClick={() => onEngine(playing ? "pause" : "start")}>{label}</button>
    </span>
  );
}
