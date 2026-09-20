import { useEffect, useState } from "react";
import { api } from "../api";
import type { Candidate, ModelProposalDTO, ProbeResult, Settings, SettingsPatch } from "../types";
import { Modal } from "./Modal";
import { ProviderIcon } from "./ProviderIcon";

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
          {tab === "models" && <ModelsPanel slug={slug} s={s} onSave={save} onReload={load} />}
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

function ModelsPanel({
  slug,
  s,
  onSave,
  onReload,
}: {
  slug: string;
  s: Settings;
  onSave: (p: SettingsPatch) => Promise<void>;
  onReload: () => void;
}) {
  const [tiers, setTiers] = useState<Record<string, Candidate[]>>(s.tiers);
  const [roles, setRoles] = useState<Record<string, string>>(s.roles);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [proposal, setProposal] = useState<ModelProposalDTO | null>(null);

  useEffect(() => {
    setTiers(s.tiers);
    setRoles(s.roles);
  }, [s]);

  const providers = s.providers.map((p) => p.name);
  const setRow = (tier: string, i: number, patch: Partial<Candidate>) =>
    setTiers({ ...tiers, [tier]: tiers[tier].map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const move = (tier: string, i: number, d: number) => {
    const arr = [...tiers[tier]];
    const j = i + d;
    if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    setTiers({ ...tiers, [tier]: arr });
  };
  const dirty =
    JSON.stringify(tiers) !== JSON.stringify(s.tiers) ||
    JSON.stringify(roles) !== JSON.stringify(s.roles);

  const handleSync = async () => {
    setSyncing(true);
    setSyncError(null);
    setSyncNote(null);
    try {
      const p = await api.previewModelSync(slug);
      setProposal(p);
      if (!p.changed) {
        setSyncNote("✔ Os modelos atuais já são a melhor lista do catálogo sob os critérios de custo e qualidade.");
      }
    } catch (e) {
      setSyncError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  const fillFromProposal = () => {
    if (!proposal) return;
    setTiers(proposal.tiers);
    setSyncNote("Campos preenchidos com os modelos propostos! Revise e clique em 'Salvar modelos'.");
    setProposal(null);
  };

  const sendToInbox = async () => {
    if (!proposal) return;
    setSyncing(true);
    try {
      const res = await api.applyModelSync(slug, true);
      setSyncNote(res.message || "✔ Proposta enviada à Caixa de Entrada para aprovação do Founder.");
      setProposal(null);
    } catch (e) {
      setSyncError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  const applyImmediately = async () => {
    if (!proposal) return;
    setSyncing(true);
    try {
      await api.applyModelSync(slug, false);
      setSyncNote("✔ Modelos e tabela de preços atualizados com sucesso!");
      setProposal(null);
      onReload();
    } catch (e) {
      setSyncError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header with OpenRouter sync */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 rounded-lg border border-line bg-slate-900/60 p-3">
        <div>
          <div className="font-semibold text-slate-100 flex items-center gap-2">
            <span>Matriz de Modelos & Clusters</span>
            <span className="chip bg-brand/20 text-brand text-[10px]">OpenRouter</span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Rankeia os modelos por clusters de inteligência usando índices compostos da Artificial Analysis.
          </p>
        </div>
        <button
          className="btn-primary flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold"
          disabled={syncing}
          onClick={handleSync}
        >
          {syncing ? (
            <>
              <span className="inline-block animate-spin">⟳</span>
              <span>Sincronizando…</span>
            </>
          ) : (
            <>
              <span>⚡</span>
              <span>Sincronizar Modelos (OpenRouter)</span>
            </>
          )}
        </button>
      </div>

      {syncError && (
        <p className="text-xs text-red-300 rounded border border-red-900/50 bg-red-950/40 p-2">
          ✘ {syncError}
        </p>
      )}
      {syncNote && (
        <p className="text-xs text-emerald-300 rounded border border-emerald-900/50 bg-emerald-950/40 p-2">
          {syncNote}
        </p>
      )}

      {/* Proposal preview card */}
      {proposal && (
        <div className="rounded-lg border border-brand/40 bg-slate-900/90 p-3 space-y-3 shadow-lg">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <div>
              <span className="font-semibold text-brand text-sm flex items-center gap-1.5">
                <span>🎯</span> Proposta de Otimização de Modelos
              </span>
              <span className="text-xs text-slate-400 block">
                {proposal.eligible} de {proposal.considered} modelos qualificados ({proposal.mode === "alias" ? "apelidos -latest" : "versões fixas"}).
              </span>
            </div>
            <button className="btn-ghost text-xs py-1" onClick={() => setProposal(null)}>
              ✕ Fechar
            </button>
          </div>

          {/* 3 Clusters x 3 Tiers */}
          {proposal.clusters && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {/* Cluster 1: Estratégia */}
              <div className="rounded-md border border-line/70 bg-ink/60 p-2 text-xs">
                <div className="font-semibold text-slate-200 mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-1">
                    <span>🏛️</span>
                    <span>Estratégia</span>
                  </span>
                  <span className="text-[10px] text-brand/80 font-normal">Padrão: Tier 1</span>
                </div>
                <div className="space-y-1.5">
                  {(["tier1", "tier2", "tier3"] as const).map((tKey) => {
                    const pick = proposal.clusters?.strategy?.[tKey]?.[0];
                    const label =
                      tKey === "tier1"
                        ? "Tier 1 (Alta Cognição)"
                        : tKey === "tier2"
                        ? "Tier 2 (Custo-Benefício)"
                        : "Tier 3 (Gratuito)";
                    return (
                      <div key={tKey} className="rounded bg-slate-800/60 p-1.5 border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 font-medium">{label}</div>
                        {pick ? (
                          <div className="flex items-center justify-between mt-0.5">
                            <span
                              className="flex items-center gap-1.5 font-mono text-[11px] text-cyan-300 truncate max-w-[135px]"
                              title={pick.id}
                            >
                              <ProviderIcon
                                provider={pick.vendor || pick.id}
                                model={pick.id}
                                className="w-3.5 h-3.5 flex-shrink-0"
                              />
                              <span className="truncate">{pick.name}</span>
                            </span>
                            <div className="text-right whitespace-nowrap pl-1">
                              <span
                                className="font-bold text-amber-300 ml-1 cursor-help"
                                title={`Índice Estratégia (50% Int | 30% Cod | 20% Agt) - Coding: ${pick.coding ?? "-"} | Agentic: ${pick.agentic ?? "-"} | Intel: ${pick.intelligence ?? "-"}`}
                              >
                                {pick.score ?? pick.quality}
                              </span>
                              <span className="text-[10px] text-slate-400 block">${pick.price.toFixed(2)}/M</span>
                            </div>
                          </div>
                        ) : (
                          <span className="text-slate-500 italic text-[11px]">nenhum</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Cluster 2: Engenharia */}
              <div className="rounded-md border border-line/70 bg-ink/60 p-2 text-xs">
                <div className="font-semibold text-slate-200 mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-1">
                    <span>⚙️</span>
                    <span>Engenharia</span>
                  </span>
                  <span className="text-[10px] text-brand/80 font-normal">Padrão: Tier 2</span>
                </div>
                <div className="space-y-1.5">
                  {(["tier1", "tier2", "tier3"] as const).map((tKey) => {
                    const pick = proposal.clusters?.engineering?.[tKey]?.[0];
                    const label =
                      tKey === "tier1"
                        ? "Tier 1 (Escalação Bugs)"
                        : tKey === "tier2"
                        ? "Tier 2 (Padrão Dev)"
                        : "Tier 3 (Gratuito)";
                    return (
                      <div key={tKey} className="rounded bg-slate-800/60 p-1.5 border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 font-medium">{label}</div>
                        {pick ? (
                          <div className="flex items-center justify-between mt-0.5">
                            <span
                              className="flex items-center gap-1.5 font-mono text-[11px] text-cyan-300 truncate max-w-[135px]"
                              title={pick.id}
                            >
                              <ProviderIcon
                                provider={pick.vendor || pick.id}
                                model={pick.id}
                                className="w-3.5 h-3.5 flex-shrink-0"
                              />
                              <span className="truncate">{pick.name}</span>
                            </span>
                            <div className="text-right whitespace-nowrap pl-1">
                              <span
                                className="font-bold text-amber-300 ml-1 cursor-help"
                                title={`Índice Engenharia (60% Cod | 30% Agt | 10% Int) - Coding: ${pick.coding ?? "-"} | Agentic: ${pick.agentic ?? "-"} | Intel: ${pick.intelligence ?? "-"}`}
                              >
                                {pick.score ?? pick.quality}
                              </span>
                              <span className="text-[10px] text-slate-400 block">${pick.price.toFixed(2)}/M</span>
                            </div>
                          </div>
                        ) : (
                          <span className="text-slate-500 italic text-[11px]">nenhum</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Cluster 3: Rotina */}
              <div className="rounded-md border border-line/70 bg-ink/60 p-2 text-xs">
                <div className="font-semibold text-slate-200 mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-1">
                    <span>📋</span>
                    <span>Rotina & Pesquisa</span>
                  </span>
                  <span className="text-[10px] text-emerald-400 font-normal">Padrão: Tier 3</span>
                </div>
                <div className="space-y-1.5">
                  {(["tier1", "tier2", "tier3"] as const).map((tKey) => {
                    const pick = proposal.clusters?.routine?.[tKey]?.[0];
                    const label =
                      tKey === "tier1"
                        ? "Tier 1 (Pesquisa Pesada)"
                        : tKey === "tier2"
                        ? "Tier 2 (Custo-Benefício)"
                        : "Tier 3 (Padrão Custo $0)";
                    return (
                      <div key={tKey} className="rounded bg-slate-800/60 p-1.5 border border-slate-700/50">
                        <div className="text-[10px] text-slate-400 font-medium">{label}</div>
                        {pick ? (
                          <div className="flex items-center justify-between mt-0.5">
                            <span
                              className="flex items-center gap-1.5 font-mono text-[11px] text-cyan-300 truncate max-w-[135px]"
                              title={pick.id}
                            >
                              <ProviderIcon
                                provider={pick.vendor || pick.id}
                                model={pick.id}
                                className="w-3.5 h-3.5 flex-shrink-0"
                              />
                              <span className="truncate">{pick.name}</span>
                            </span>
                            <div className="text-right whitespace-nowrap pl-1">
                              <span
                                className="font-bold text-amber-300 ml-1 cursor-help"
                                title={`Índice Rotina (55% Agt | 30% Int | 15% Cod) - Coding: ${pick.coding ?? "-"} | Agentic: ${pick.agentic ?? "-"} | Intel: ${pick.intelligence ?? "-"}`}
                              >
                                {pick.score ?? pick.quality}
                              </span>
                              <span className="text-[10px] text-slate-400 block">${pick.price.toFixed(2)}/M</span>
                            </div>
                          </div>
                        ) : (
                          <span className="text-slate-500 italic text-[11px]">nenhum</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* Diffs alert */}
          <div className="text-xs space-y-1 bg-ink/40 p-2 rounded border border-line">
            {proposal.added.length > 0 && (
              <div>
                <span className="text-emerald-400 font-semibold">Entram:</span>{" "}
                {proposal.added.join(", ")}
              </div>
            )}
            {proposal.removed.length > 0 && (
              <div>
                <span className="text-rose-400 font-semibold">Saem:</span>{" "}
                {proposal.removed.join(", ")}
              </div>
            )}
            {proposal.repriced.length > 0 && (
              <div>
                <span className="text-amber-400 font-semibold">Preços atualizados:</span>{" "}
                {proposal.repriced.join(", ")}
              </div>
            )}
            {proposal.expiring.length > 0 && (
              <div>
                <span className="text-orange-400 font-semibold">Descontinuados em breve:</span>{" "}
                {proposal.expiring.join(", ")}
              </div>
            )}
            {!proposal.changed && (
              <div className="text-slate-400">
                Os modelos configurados atualmente já são a melhor escolha do catálogo.
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
            <button
              className="btn-ghost text-xs"
              onClick={fillFromProposal}
              title="Preenche os modelos nos campos abaixo para você revisar e editar antes de salvar"
            >
              ✏️ Preencher nos Tiers
            </button>
            <button
              className="btn-ghost text-xs"
              onClick={sendToInbox}
              title="Envia para a Caixa de Entrada do Founder como proposta formal assíncrona"
            >
              📬 Enviar ao Inbox
            </button>
            <button
              className="btn-primary text-xs font-semibold"
              onClick={applyImmediately}
              title="Aplica imediatamente aos modelos e preços da fábrica"
            >
              ✔ Aplicar Agora
            </button>
          </div>
        </div>
      )}

      {/* Manual Tiers configuration */}
      {Object.entries(tiers).map(([tier, cands]) => (
        <div key={tier} className="rounded-md border border-line p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-semibold flex items-center gap-1.5">
              <span>{tier}</span>
              <span className="text-xs font-normal text-slate-500">
                — candidatos em ordem; o roteador cai para o próximo em cota/erro
              </span>
            </span>
            <button
              className="btn-ghost text-xs py-0.5"
              onClick={() =>
                setTiers({
                  ...tiers,
                  [tier]: [...cands, { provider: providers[0] ?? "", model: "" }],
                })
              }
            >
              + modelo
            </button>
          </div>
          {cands.map((c, i) => (
            <div key={i} className="mb-1 flex items-center gap-1.5">
              <span className="w-4 text-xs text-slate-500">{i + 1}</span>
              <ProviderIcon
                provider={c.provider}
                model={c.model}
                className="w-4 h-4 flex-shrink-0 text-slate-300"
              />
              <select
                className={select}
                value={c.provider}
                onChange={(e) => setRow(tier, i, { provider: e.target.value })}
              >
                {providers.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
              <input
                className={input}
                value={c.model}
                placeholder="id do modelo no provedor (ex: ~z-ai/glm-latest)"
                onChange={(e) => setRow(tier, i, { model: e.target.value })}
              />
              <button className="btn-ghost" onClick={() => move(tier, i, -1)}>
                ↑
              </button>
              <button className="btn-ghost" onClick={() => move(tier, i, 1)}>
                ↓
              </button>
              <button
                className="btn-ghost"
                onClick={() => setTiers({ ...tiers, [tier]: cands.filter((_, j) => j !== i) })}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      ))}

      <div className="rounded-md border border-line p-2">
        <div className="mb-1 font-semibold">
          Papel → tier{" "}
          <span className="text-xs font-normal text-slate-500">
            — papéis novos usam tier2 por padrão
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1 md:grid-cols-3">
          {Object.entries(roles).map(([role, tier]) => (
            <label key={role} className="flex items-center justify-between gap-2 text-xs">
              <span>{role}</span>
              <select
                className={select}
                value={tier}
                onChange={(e) => setRoles({ ...roles, [role]: e.target.value })}
              >
                {Object.keys(tiers).map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </div>
      <div className="text-right">
        <button
          className="btn-primary"
          disabled={!dirty}
          onClick={() =>
            onSave({
              tiers: Object.fromEntries(
                Object.entries(tiers).map(([t, cs]) => [t, cs.filter((c) => c.model.trim())])
              ),
              roles,
            })
          }
        >
          Salvar modelos
        </button>
      </div>
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
