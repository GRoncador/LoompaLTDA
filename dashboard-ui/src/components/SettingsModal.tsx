import { useEffect, useState } from "react";
import { api } from "../api";
import type { Candidate, ProbeResult, Settings, SettingsPatch } from "../types";
import { Modal } from "./MeetingModal";

const input = "w-full rounded-md border border-line bg-ink px-2 py-1 text-sm";
const select = "rounded-md border border-line bg-ink px-2 py-1 text-sm";

export default function SettingsModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [tab, setTab] = useState<"providers" | "models" | "budget">("providers");
  const load = () => api.settings(slug).then(setS).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, [slug]);
  const save = async (patch: SettingsPatch) => {
    setErr(null);
    try { const r = await api.updateSettings(slug, patch); setS(r.settings); setNotes(r.changes); } catch (e) { setErr(String(e)); }
  };
  return (
    <Modal title="⚙ Configurações da fábrica" onClose={onClose}>
      {!s ? <p className="text-sm text-slate-400">{err ?? "carregando…"}</p> : (
        <div className="scroll-thin max-h-[75vh] space-y-4 overflow-y-auto pr-1 text-sm">
          <div className="flex gap-2 border-b border-line pb-2">
            {(["providers", "models", "budget"] as const).map((t) => (
              <button key={t} className={tab === t ? "btn-primary" : "btn-ghost"} onClick={() => setTab(t)}>
                {t === "providers" ? "Provedores e chaves" : t === "models" ? "Modelos e papéis" : "Orçamento"}
              </button>
            ))}
          </div>
          {err && <p className="text-xs text-red-300">{err}</p>}
          {notes.length > 0 && <p className="text-xs text-emerald-300">✔ {notes.join(" · ")}</p>}
          {tab === "providers" && <ProvidersPanel slug={slug} s={s} onSave={save} />}
          {tab === "models" && <ModelsPanel s={s} onSave={save} />}
          {tab === "budget" && <BudgetPanel s={s} onSave={save} />}
          <p className="text-[11px] text-slate-500">
            Chaves ficam só em <code>{s.secrets_files.hub}</code> (todas as fábricas) ou <code>{s.secrets_files.factory}</code> (esta fábrica). Nunca entram no config.yaml, no git ou em logs. Mudanças valem na próxima chamada, sem reiniciar.
          </p>
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------- providers + keys

export function ProvidersPanel({ slug, s, onSave, onlyNeeded }: { slug: string; s: Settings; onSave: (p: SettingsPatch) => Promise<void>; onlyNeeded?: boolean }) {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<"hub" | "factory">("hub");
  const [probe, setProbe] = useState<Record<string, ProbeResult | "…">>({});
  const [tavilyKey, setTavilyKey] = useState("");
  const used = new Set(Object.values(s.tiers).flat().map((c) => c.provider));
  const providers = onlyNeeded ? s.providers.filter((p) => used.has(p.name)) : s.providers;

  const test = async (name: string) => {
    setProbe((p) => ({ ...p, [name]: "…" }));
    try { setProbe((p) => ({ ...p, [name]: undefined as any })); const r = await api.testProvider(slug, name); setProbe((p) => ({ ...p, [name]: r })); }
    catch (e) { setProbe((p) => ({ ...p, [name]: { name, ok: false, detail: String(e), model: "", latency_ms: 0 } })); }
  };
  const saveKeys = async () => {
    const patch: SettingsPatch = { providers: {}, tools: {} };
    for (const [name, v] of Object.entries(keys)) if (v.trim()) patch.providers![name] = { api_key: v.trim(), scope };
    if (tavilyKey.trim()) patch.tools!.tavily = { api_key: tavilyKey.trim(), scope };
    await onSave(patch);
    setKeys({}); setTavilyKey("");
  };
  const dirty = Object.values(keys).some((v) => v.trim()) || tavilyKey.trim();
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-slate-400">Preset:</span>
        {s.presets.map((p) => (
          <button key={p.key} className={s.preset === p.key ? "btn-primary" : "btn-ghost"} title={p.description} onClick={() => onSave({ preset: p.key })}>{p.label}</button>
        ))}
        <span className="text-xs text-slate-500">{s.presets.find((p) => p.key === s.preset)?.description ?? "escolha um preset ou edite os tiers na aba Modelos"}</span>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-400">Guardar novas chaves em
        <select className={select} value={scope} onChange={(e) => setScope(e.target.value as any)}>
          <option value="hub">hub (todas as fábricas)</option>
          <option value="factory">só esta fábrica</option>
        </select>
      </label>
      <table className="w-full text-xs">
        <thead className="text-slate-500"><tr><th className="text-left">provedor</th><th className="text-left">chave</th><th className="text-left">nova chave</th><th></th></tr></thead>
        <tbody>
          {providers.map((p) => {
            const r = probe[p.name];
            return (
              <tr key={p.name} className="border-t border-line/60 align-top">
                <td className="py-1.5 pr-2">
                  <div className="font-medium text-slate-200">{p.label}{used.has(p.name) && <span className="ml-1 text-brand" title="usado nos tiers">●</span>}</div>
                  <div className="text-slate-500">{p.used_by.join(", ") || "não usado nos tiers"}</div>
                  {p.console_url && <a className="text-brand hover:underline" href={p.console_url} target="_blank" rel="noreferrer">criar chave ↗</a>}
                </td>
                <td className="py-1.5 pr-2">
                  <span className={p.key.configured ? "text-emerald-300" : "text-amber-300"}>{p.key.label}</span>
                  {p.key.source && <span className="ml-1 text-slate-500">({p.key.source})</span>}
                  {r && r !== "…" && <div className={r.ok ? "text-emerald-300" : "text-red-300"}>{r.ok ? "✔" : "✘"} {r.detail}{r.latency_ms ? ` · ${r.latency_ms} ms` : ""}</div>}
                  {r === "…" && <div className="text-slate-400">testando…</div>}
                </td>
                <td className="py-1.5 pr-2">
                  {p.needs_key ? <input type="password" autoComplete="off" placeholder={p.api_key_env} className={input} value={keys[p.name] ?? ""} onChange={(e) => setKeys({ ...keys, [p.name]: e.target.value })} /> : <span className="text-slate-500">sem chave</span>}
                </td>
                <td className="py-1.5 text-right whitespace-nowrap">
                  <button className="btn-ghost" onClick={() => test(p.name)} disabled={p.needs_key && !p.key.configured}>Testar</button>
                  {p.key.configured && p.needs_key && <button className="btn-ghost ml-1" title="remover chave" onClick={() => onSave({ providers: { [p.name]: { clear_key: true } } })}>✕</button>}
                </td>
              </tr>
            );
          })}
          <tr className="border-t border-line/60 align-top">
            <td className="py-1.5 pr-2">
              <div className="font-medium text-slate-200">Tavily (busca web do Analyst)</div>
              <a className="text-brand hover:underline" href={s.tools.tavily.console_url} target="_blank" rel="noreferrer">criar chave ↗</a>
            </td>
            <td className="py-1.5 pr-2">
              <span className={s.tools.tavily.key.configured ? "text-emerald-300" : "text-amber-300"}>{s.tools.tavily.key.label}</span>
              {probe.tavily && probe.tavily !== "…" && <div className={probe.tavily.ok ? "text-emerald-300" : "text-red-300"}>{probe.tavily.ok ? "✔" : "✘"} {probe.tavily.detail}</div>}
            </td>
            <td className="py-1.5 pr-2"><input type="password" autoComplete="off" placeholder={s.tools.tavily.api_key_env} className={input} value={tavilyKey} onChange={(e) => setTavilyKey(e.target.value)} /></td>
            <td className="py-1.5 text-right whitespace-nowrap">
              <button className="btn-ghost" onClick={() => test("tavily")} disabled={!s.tools.tavily.key.configured}>Testar</button>
              {s.tools.tavily.key.configured && <button className="btn-ghost ml-1" onClick={() => onSave({ tools: { tavily: { clear_key: true } } })}>✕</button>}
            </td>
          </tr>
        </tbody>
      </table>
      <div className="text-right"><button className="btn-primary" disabled={!dirty} onClick={saveKeys}>Guardar chaves</button></div>
    </div>
  );
}

// ------------------------------------------------------------------- tiers + roles

function ModelsPanel({ s, onSave }: { s: Settings; onSave: (p: SettingsPatch) => Promise<void> }) {
  const [tiers, setTiers] = useState<Record<string, Candidate[]>>(s.tiers);
  const [roles, setRoles] = useState<Record<string, string>>(s.roles);
  useEffect(() => { setTiers(s.tiers); setRoles(s.roles); }, [s]);
  const providers = s.providers.map((p) => p.name);
  const setRow = (tier: string, i: number, patch: Partial<Candidate>) => setTiers({ ...tiers, [tier]: tiers[tier].map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const move = (tier: string, i: number, d: number) => {
    const arr = [...tiers[tier]]; const j = i + d; if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]]; setTiers({ ...tiers, [tier]: arr });
  };
  const dirty = JSON.stringify(tiers) !== JSON.stringify(s.tiers) || JSON.stringify(roles) !== JSON.stringify(s.roles);
  return (
    <div className="space-y-4">
      {Object.entries(tiers).map(([tier, cands]) => (
        <div key={tier} className="rounded-md border border-line p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-semibold">{tier} <span className="text-xs font-normal text-slate-500">— candidatos em ordem; o roteador cai para o próximo em cota/erro</span></span>
            <button className="btn-ghost" onClick={() => setTiers({ ...tiers, [tier]: [...cands, { provider: providers[0] ?? "", model: "" }] })}>+ modelo</button>
          </div>
          {cands.map((c, i) => (
            <div key={i} className="mb-1 flex items-center gap-1">
              <span className="w-4 text-xs text-slate-500">{i + 1}</span>
              <select className={select} value={c.provider} onChange={(e) => setRow(tier, i, { provider: e.target.value })}>{providers.map((p) => <option key={p}>{p}</option>)}</select>
              <input className={input} value={c.model} placeholder="id do modelo no provedor" onChange={(e) => setRow(tier, i, { model: e.target.value })} />
              <button className="btn-ghost" onClick={() => move(tier, i, -1)}>↑</button>
              <button className="btn-ghost" onClick={() => move(tier, i, 1)}>↓</button>
              <button className="btn-ghost" onClick={() => setTiers({ ...tiers, [tier]: cands.filter((_, j) => j !== i) })}>✕</button>
            </div>
          ))}
        </div>
      ))}
      <div className="rounded-md border border-line p-2">
        <div className="mb-1 font-semibold">Papel → tier <span className="text-xs font-normal text-slate-500">— papéis novos usam tier2 por padrão</span></div>
        <div className="grid grid-cols-2 gap-1 md:grid-cols-3">
          {Object.entries(roles).map(([role, tier]) => (
            <label key={role} className="flex items-center justify-between gap-2 text-xs"><span>{role}</span>
              <select className={select} value={tier} onChange={(e) => setRoles({ ...roles, [role]: e.target.value })}>{Object.keys(tiers).map((t) => <option key={t}>{t}</option>)}</select>
            </label>
          ))}
        </div>
      </div>
      <div className="text-right"><button className="btn-primary" disabled={!dirty} onClick={() => onSave({ tiers: Object.fromEntries(Object.entries(tiers).map(([t, cs]) => [t, cs.filter((c) => c.model.trim())])), roles })}>Salvar modelos</button></div>
    </div>
  );
}

// ------------------------------------------------------------------------- budget

function BudgetPanel({ s, onSave }: { s: Settings; onSave: (p: SettingsPatch) => Promise<void> }) {
  const [b, setB] = useState(s.budget);
  const [par, setPar] = useState(s.schedule.max_parallel);
  useEffect(() => { setB(s.budget); setPar(s.schedule.max_parallel); }, [s]);
  return (
    <div className="space-y-3">
      <label className="block">Teto mensal (US$)<input type="number" min={0} step={5} className={input} value={b.monthly_cap_usd} onChange={(e) => setB({ ...b, monthly_cap_usd: Number(e.target.value) })} /></label>
      <label className="block">Alerta em (fração do teto)<input type="number" min={0} max={1} step={0.05} className={input} value={b.warn_at_fraction} onChange={(e) => setB({ ...b, warn_at_fraction: Number(e.target.value) })} /></label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={b.hard_stop} onChange={(e) => setB({ ...b, hard_stop: e.target.checked })} /> Pausar a esteira ao atingir o teto</label>
      <label className="block">Histórias em paralelo<input type="number" min={1} max={32} className={input} value={par} onChange={(e) => setPar(Number(e.target.value))} /></label>
      <p className="text-xs text-slate-500">O custo exato é acompanhado no painel de cada provedor; aqui é uma estimativa por tokens.</p>
      <div className="text-right"><button className="btn-primary" onClick={() => onSave({ budget: b, max_parallel: par })}>Salvar orçamento</button></div>
    </div>
  );
}
