import type { FactoryRef, Overview } from "../types";

export default function Header(props: {
  factories: FactoryRef[]; slug: string | null; overview: Overview | null; connected: boolean; pending: number;
  onSwitch: (slug: string) => void; onNewFactory: () => void; onMeeting: () => void; onToggleEngine: () => void; onSettings: () => void;
}) {
  const { factories, slug, overview, connected, pending } = props;
  const fin = overview?.finance;
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-2">
      <div className="flex items-center gap-2">
        <span className="text-xl">🏭</span>
        <span className="font-pixel text-[11px] tracking-wider text-brand">LOOMPA LTDA · HQ</span>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <span className="text-slate-400">Empresa ativa</span>
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
            <span className={`text-sm ${fin.warn ? "text-amber-300" : "text-slate-300"}`} title="Custo hoje / mês / teto">
              💰 US$ {fin.today_usd.toFixed(2)} hoje · {fin.month_usd.toFixed(2)}/{fin.cap_usd.toFixed(0)}
            </span>
          )}
          <span className="text-sm" title="Decisões aguardando você">📬 {pending}</span>
          <button className={overview.factory.engine ? "btn-ghost" : "btn-primary"} onClick={props.onToggleEngine}>
            {overview.factory.engine ? "⏸ Pausar esteira" : "▶ Ligar esteira"}
          </button>
          <button className="btn-primary" onClick={props.onMeeting}>☀️ Reunião matinal</button>
          <button className="btn-ghost" title="Provedores, modelos, chaves e orçamento" onClick={props.onSettings}>⚙ Configurações</button>
        </>
      )}
      <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} title={connected ? "tempo real conectado" : "reconectando…"} />
    </header>
  );
}
