import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Candidate, ModelMatrix, ModelPick, ModelProposalDTO, ProbeResult, Settings, SettingsPatch } from "../types";
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
    <Modal title="⚙ Configurações da fábrica" onClose={onClose} className="max-w-6xl xl:max-w-7xl 2xl:max-w-[1400px] w-full">
      {!s ? <p className="text-sm text-slate-400">{err ?? "carregando…"}</p> : (
        <div className="scroll-thin max-h-[82vh] space-y-4 overflow-y-auto pr-1 text-sm">
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

const ROLE_DISPLAY: Record<string, string> = {
  master: "Master (COO)",
  architect: "Architect (Tech Lead)",
  product: "Spec Loompa (BDD)",
  product_owner: "Product Owner (PO)",
  analyst: "Analyst (Pesquisa)",
  worker: "Worker (Engenharia)",
  inspector: "Inspector (QA Judge)",
  deployer: "Deployer (Entrega)",
  compliance: "Compliance",
  metrics: "Metrics",
  storyteller: "Storyteller",
};

// Deterministic backend engine services ($0 AI) excluded from AI LLM role mapping
const BACKEND_SERVICES = new Set(["ops", "finance", "kaizen"]);

function renderModelCard(
  m: ModelPick,
  tierTarget: string,
  tier1Ceiling: number,
  onSelect: (id: string) => void,
  onClose: () => void
) {
  const isOverCeiling = tierTarget === "tier1" && m.price > tier1Ceiling;
  return (
    <div
      key={m.id}
      className={`flex items-center justify-between gap-2 rounded-md border p-2 text-xs transition-all ${
        isOverCeiling
          ? "border-amber-900/50 bg-amber-950/20 hover:border-amber-700/60"
          : "border-line/70 bg-slate-800/40 hover:border-brand/60 hover:bg-slate-800/80"
      }`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <ProviderIcon provider={m.vendor || m.id} model={m.id} className="w-4 h-4 flex-shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold text-slate-200 truncate flex items-center gap-1.5">
            <span className="truncate">{m.name}</span>
            {isOverCeiling && (
              <span
                className="text-[10px] text-amber-300 bg-amber-950/90 px-1.5 py-0.2 rounded border border-amber-800/70 font-mono"
                title={`Preço ($${m.price.toFixed(2)}) excede o teto configurado para Tier 1 ($${tier1Ceiling.toFixed(2)})`}
              >
                ⚠️ &gt; teto (${tier1Ceiling.toFixed(2)})
              </span>
            )}
          </div>
          <div className="text-[11px] font-mono text-slate-400 truncate">{m.id}</div>
          <div className="text-[10px] text-slate-400 mt-0.5 flex flex-wrap gap-x-2">
            {m.coding != null && (
              <span>
                Cod: <strong className="text-slate-300">{m.coding}</strong>
              </span>
            )}
            {m.agentic != null && (
              <span>
                Agt: <strong className="text-slate-300">{m.agentic}</strong>
              </span>
            )}
            {m.intelligence != null && (
              <span>
                Int: <strong className="text-slate-300">{m.intelligence}</strong>
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2.5 flex-shrink-0 text-right">
        <div>
          <div className="flex items-center justify-end gap-1.5">
            <span className="font-bold text-amber-300 text-sm">
              ★ {m.score ?? m.quality}
            </span>
            {(() => {
              const cb = m.cost_benefit ?? (m.price > 0 && (m.score ?? m.quality) ? Math.round(((m.score ?? m.quality) / m.price) * 10) / 10 : null);
              return cb != null ? (
                <span
                  className="text-[10px] text-cyan-300 bg-cyan-950/70 px-1.5 py-0.5 rounded border border-cyan-800/60 font-mono font-medium"
                  title={`Custo-Benefício: ${cb} pontos de benchmark por US$ 1M tokens`}
                >
                  {cb} pts/$
                </span>
              ) : null;
            })()}
          </div>
          <div className="text-[11px] font-mono text-slate-300">
            {m.price === 0 ? (
              <span className="text-emerald-400 font-semibold">GRÁTIS</span>
            ) : (
              `$${m.price.toFixed(2)}/M`
            )}
          </div>
        </div>
        <button
          className="btn-primary text-xs py-1 px-2.5 font-semibold"
          onClick={() => {
            onSelect(m.id);
            onClose();
          }}
        >
          Escolher
        </button>
      </div>
    </div>
  );
}

function OpenRouterModelPickerModal({
  isOpen,
  onClose,
  onSelect,
  tierTarget,
  clusterTarget,
  tier1Ceiling,
  catalog,
  modelsMap,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (modelId: string) => void;
  tierTarget: string;
  clusterTarget?: string;
  tier1Ceiling: number;
  catalog: ModelProposalDTO | null;
  modelsMap: Record<string, ModelPick>;
}) {
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"strategy" | "engineering" | "routine" | "all">(
    (clusterTarget as any) || "strategy"
  );

  if (!isOpen) return null;

  const filterPick = (m: ModelPick) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      m.name.toLowerCase().includes(q) ||
      m.id.toLowerCase().includes(q) ||
      (m.vendor || "").toLowerCase().includes(q)
    );
  };

  const allList = catalog?.all_models || Object.values(modelsMap);
  const allFiltered = allList.filter(filterPick);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-4xl xl:max-w-5xl flex-col rounded-xl border border-brand/50 bg-ink p-4 shadow-2xl">
        <div className="flex items-center justify-between border-b border-line pb-2 mb-3">
          <div>
            <h3 className="font-semibold text-slate-100 flex items-center gap-2 text-sm">
              <span>⚡</span> Selecionar Modelo OpenRouter
              <span className="chip bg-brand/20 text-brand text-xs font-mono">{tierTarget}</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Escolha a partir da lista categorizada por cluster e tier com notas da Artificial Analysis.
            </p>
          </div>
          <button className="btn-ghost text-xs py-1" onClick={onClose}>
            ✕ Fechar
          </button>
        </div>

        {/* Search & Tabs */}
        <div className="space-y-2 mb-3">
          <input
            type="text"
            className="w-full rounded-md border border-line bg-slate-900 px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:border-brand focus:outline-none"
            placeholder="Buscar por nome, id ou fabricante (ex: grok, glm, gemini, qwen, openai)..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />

          <div className="flex flex-wrap gap-1.5 border-b border-line/60 pb-2">
            {[
              { key: "strategy", label: "🏛️ Estratégia & Produto" },
              { key: "engineering", label: "⚙️ Engenharia de Código" },
              { key: "routine", label: "📋 Rotina & Suporte" },
              { key: "all", label: `🌐 Todos (${allFiltered.length})` },
            ].map((t) => (
              <button
                key={t.key}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                  activeTab === t.key
                    ? "bg-brand text-ink font-semibold"
                    : "bg-slate-800/80 text-slate-300 hover:bg-slate-700"
                }`}
                onClick={() => setActiveTab(t.key as any)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content list */}
        <div className="scroll-thin flex-1 overflow-y-auto space-y-3 pr-1">
          {activeTab === "all" ? (
            <div className="space-y-1.5">
              {allFiltered.length === 0 ? (
                <p className="text-xs text-slate-500 py-4 text-center">
                  Nenhum modelo encontrado com os termos pesquisados.
                </p>
              ) : (
                allFiltered.map((m) =>
                  renderModelCard(m, tierTarget, tier1Ceiling, onSelect, onClose)
                )
              )}
            </div>
          ) : (
            <>
              {(["tier1", "tier2", "tier3"] as const).map((tKey) => {
                const clusterKey = activeTab as "strategy" | "engineering" | "routine";
                const clusterPicks = catalog?.clusters?.[clusterKey]?.[tKey] || [];
                const filtered = clusterPicks.filter(filterPick);
                if (filtered.length === 0) return null;

                const tierTitle =
                  tKey === "tier1"
                    ? "Tier 1 — Alta Cognição / Raciocínio Profundo"
                    : tKey === "tier2"
                    ? "Tier 2 — Custo-Benefício / Execução e Código"
                    : "Tier 3 — Gratuito / Tarefas Leves e Contingência";

                return (
                  <div
                    key={tKey}
                    className="space-y-1.5 rounded-lg border border-line/60 bg-slate-900/40 p-2.5"
                  >
                    <div className="text-xs font-semibold text-slate-300 mb-1 flex items-center justify-between">
                      <span>{tierTitle}</span>
                      <span className="text-[10px] text-slate-500">{filtered.length} modelo(s)</span>
                    </div>
                    <div className="space-y-1">
                      {filtered.map((m) =>
                        renderModelCard(m, tierTarget, tier1Ceiling, onSelect, onClose)
                      )}
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const CLUSTERS_CONFIG = [
  {
    key: "strategy",
    label: "Estratégia & Produto",
    icon: "🏛️",
    roles: ["Master", "Architect", "Spec Loompa", "Product Owner", "Analyst"],
    description: "Cognição elevada, raciocínio aprofundado, decomposição de histórias e arquitetura",
  },
  {
    key: "engineering",
    label: "Engenharia de Código",
    icon: "⚙️",
    roles: ["Worker (Dev)", "Inspector (QA Judge)"],
    description: "Codificação, refatoração, implementação de testes e julgamento de qualidade",
  },
  {
    key: "routine",
    label: "Rotina & Suporte",
    icon: "📋",
    roles: ["Deployer", "Storyteller", "Compliance", "Metrics"],
    description: "Automação contínua, documentação, git worktrees e tarefas rotineiras",
  },
] as const;

const TIERS_CONFIG = [
  {
    key: "tier1",
    label: "Tier 1",
    sublabel: "Alta Cognição / Raciocínio",
    badgeColor: "text-amber-300 border-amber-800/60 bg-amber-950/40",
  },
  {
    key: "tier2",
    label: "Tier 2",
    sublabel: "Custo-Benefício / Execução Ágil",
    badgeColor: "text-cyan-300 border-cyan-800/60 bg-cyan-950/40",
  },
  {
    key: "tier3",
    label: "Tier 3",
    sublabel: "Rotina / Custo $0 ou Ultra-Leve",
    badgeColor: "text-emerald-300 border-emerald-800/60 bg-emerald-950/40",
  },
] as const;

const MAX_MODELS_PER_TIER = 6;

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
  const [matrix, setMatrix] = useState<ModelMatrix>(() => {
    if (s.matrix && Object.keys(s.matrix).length > 0) {
      return s.matrix;
    }
    return {
      strategy: {
        tier1: s.tiers?.tier1 || [],
        tier2: s.tiers?.tier2 || [],
        tier3: s.tiers?.tier3 || [],
      },
      engineering: {
        tier1: s.tiers?.tier1 || [],
        tier2: s.tiers?.tier2 || [],
        tier3: s.tiers?.tier3 || [],
      },
      routine: {
        tier1: s.tiers?.tier1 || [],
        tier2: s.tiers?.tier2 || [],
        tier3: s.tiers?.tier3 || [],
      },
    };
  });
  const [roles, setRoles] = useState<Record<string, string>>(s.roles);
  const [tier1Ceiling, setTier1Ceiling] = useState<number>(s.models?.tier1_ceiling ?? 5.0);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [proposal, setProposal] = useState<ModelProposalDTO | null>(null);
  const [catalog, setCatalog] = useState<ModelProposalDTO | null>(null);
  const [editingRaw, setEditingRaw] = useState<Record<string, boolean>>({});
  const [pickerTarget, setPickerTarget] = useState<{ cluster: string; tier: string; index?: number } | null>(null);

  useEffect(() => {
    if (s.matrix && Object.keys(s.matrix).length > 0) {
      setMatrix(s.matrix);
    } else if (s.tiers) {
      setMatrix({
        strategy: {
          tier1: s.tiers?.tier1 || [],
          tier2: s.tiers?.tier2 || [],
          tier3: s.tiers?.tier3 || [],
        },
        engineering: {
          tier1: s.tiers?.tier1 || [],
          tier2: s.tiers?.tier2 || [],
          tier3: s.tiers?.tier3 || [],
        },
        routine: {
          tier1: s.tiers?.tier1 || [],
          tier2: s.tiers?.tier2 || [],
          tier3: s.tiers?.tier3 || [],
        },
      });
    }
    setRoles(s.roles);
    if (s.models?.tier1_ceiling != null) {
      setTier1Ceiling(s.models.tier1_ceiling);
    }
  }, [s]);

  // Load cached catalog on mount (using monthly local cache)
  useEffect(() => {
    api.modelsCatalog(slug)
      .then((cat) => {
        if (cat && (cat.clusters || cat.all_models)) {
          setCatalog(cat);
        }
      })
      .catch(() => {});
  }, [slug]);

  // Unified models lookup dictionary for benchmarks and prices
  const modelsMap = useMemo(() => {
    const map: Record<string, ModelPick> = {};
    const sources = [catalog, proposal];
    for (const src of sources) {
      if (!src) continue;
      if (src.all_models) {
        for (const m of src.all_models) {
          map[m.id] = m;
          if (m.id.startsWith("~")) map[m.id.slice(1)] = m;
        }
      }
      for (const picks of Object.values(src.summary || {})) {
        for (const m of picks) {
          map[m.id] = m;
          if (m.id.startsWith("~")) map[m.id.slice(1)] = m;
        }
      }
      if (src.clusters) {
        for (const cluster of Object.values(src.clusters)) {
          if (!cluster) continue;
          for (const picks of Object.values(cluster)) {
            for (const m of picks || []) {
              map[m.id] = m;
              if (m.id.startsWith("~")) map[m.id.slice(1)] = m;
            }
          }
        }
      }
    }
    return map;
  }, [catalog, proposal]);

  const providers = s.providers.map((p) => p.name);

  const setCandidate = (cluster: string, tier: string, i: number, patch: Partial<Candidate>) => {
    const list = matrix[cluster]?.[tier] || [];
    const updated = list.map((c: Candidate, j: number) => (j === i ? { ...c, ...patch } : c));
    setMatrix({
      ...matrix,
      [cluster]: {
        ...(matrix[cluster] || {}),
        [tier]: updated,
      },
    });
  };

  const moveCandidate = (cluster: string, tier: string, i: number, delta: number) => {
    const list = [...(matrix[cluster]?.[tier] || [])];
    const j = i + delta;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    setMatrix({
      ...matrix,
      [cluster]: {
        ...(matrix[cluster] || {}),
        [tier]: list,
      },
    });
  };

  const removeCandidate = (cluster: string, tier: string, i: number) => {
    const list = (matrix[cluster]?.[tier] || []).filter((_: Candidate, j: number) => j !== i);
    setMatrix({
      ...matrix,
      [cluster]: {
        ...(matrix[cluster] || {}),
        [tier]: list,
      },
    });
  };

  const addCandidate = (cluster: string, tier: string, modelId: string, provider: string = "openrouter") => {
    const list = matrix[cluster]?.[tier] || [];
    if (list.length >= MAX_MODELS_PER_TIER) {
      setSyncNote(`Limite de ${MAX_MODELS_PER_TIER} modelos por tier atingido.`);
      return;
    }
    if (list.some((c: Candidate) => c.model === modelId)) {
      setSyncNote(`O modelo ${modelId} já está na lista deste tier.`);
      return;
    }
    setMatrix({
      ...matrix,
      [cluster]: {
        ...(matrix[cluster] || {}),
        [tier]: [...list, { provider, model: modelId }],
      },
    });
    setSyncNote(`✔ Modelo ${modelId} adicionado ao ${tier}! Clique em "Salvar modelos" para gravar.`);
  };

  const dirty =
    JSON.stringify(matrix) !== JSON.stringify(s.matrix) ||
    JSON.stringify(roles) !== JSON.stringify(s.roles) ||
    tier1Ceiling !== (s.models?.tier1_ceiling ?? 5.0);

  const handleSyncOrRecalc = async (forceRefresh: boolean) => {
    setSyncing(true);
    setSyncError(null);
    setSyncNote(null);
    try {
      const p = await api.previewModelSync(slug, {
        tier1_ceiling: tier1Ceiling,
        force_refresh: forceRefresh,
      });
      setProposal(p);
      setCatalog(p);
      if (forceRefresh) {
        setSyncNote("✔ Catálogo OpenRouter sincronizado e cache local atualizado para este mês!");
      } else if (!p.changed) {
        setSyncNote("✔ Os modelos atuais atendem aos critérios de custo e qualidade sob o teto definido.");
      }
    } catch (e) {
      setSyncError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  const fillFromProposal = () => {
    if (!proposal || !proposal.clusters) return;
    const newMatrix: ModelMatrix = {
      strategy: { tier1: [], tier2: [], tier3: [] },
      engineering: { tier1: [], tier2: [], tier3: [] },
      routine: { tier1: [], tier2: [], tier3: [] },
    };
    for (const cKey of ["strategy", "engineering", "routine"] as const) {
      for (const tKey of ["tier1", "tier2", "tier3"] as const) {
        newMatrix[cKey][tKey] = (proposal.clusters?.[cKey]?.[tKey] || []).map((p) => ({
          provider: "openrouter",
          model: p.id,
        }));
      }
    }
    setMatrix(newMatrix);
    setSyncNote("✔ Matriz proposta carregada no editor! Revise os modelos e clique em 'Salvar modelos'.");
    setProposal(null);
  };

  const sendToInbox = async () => {
    if (!proposal) return;
    setSyncing(true);
    try {
      const res = await api.applyModelSync(slug, true, tier1Ceiling);
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
      await api.applyModelSync(slug, false, tier1Ceiling);
      setSyncNote("✔ Matriz oficial de modelos e tabela de preços atualizadas com sucesso!");
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
      {/* Header with OpenRouter sync and Tier 1 Cost Ceiling */}
      <div className="rounded-lg border border-line bg-slate-900/60 p-3 space-y-3">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
          <div>
            <div className="font-semibold text-slate-100 flex items-center gap-2">
              <span>Matriz de Modelos & Clusters</span>
              <span className="chip bg-brand/20 text-brand text-[10px]">OpenRouter</span>
              <span className="text-[10px] text-slate-400 font-normal bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700">
                Cache mensal ativo
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Rankeia os modelos por clusters de inteligência usando índices compostos da Artificial Analysis.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn-ghost flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold"
              disabled={syncing}
              onClick={() => handleSyncOrRecalc(false)}
              title="Recalcula a proposta com o teto de custo atual usando o catálogo do cache local"
            >
              <span>🔄</span>
              <span>Recalcular Matriz</span>
            </button>
            <button
              className="btn-primary flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold"
              disabled={syncing}
              onClick={() => handleSyncOrRecalc(true)}
              title="Baixa a versão mais recente do catálogo OpenRouter e atualiza o cache mensal"
            >
              {syncing ? (
                <>
                  <span className="inline-block animate-spin">⟳</span>
                  <span>Sincronizando…</span>
                </>
              ) : (
                <>
                  <span>⚡</span>
                  <span>Sincronizar Catálogo (OpenRouter)</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Tier 1 Ceiling Configuration */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-line/60 bg-ink/30 px-2 py-1.5 rounded">
          <div className="flex items-center gap-2">
            <label htmlFor="tier1-ceiling-input" className="text-xs font-medium text-amber-300 flex items-center gap-1">
              <span>🎯</span> Teto de Custo Tier 1 (US$/1M tokens):
            </label>
            <input
              id="tier1-ceiling-input"
              type="number"
              min={0.1}
              step={0.5}
              className="w-24 rounded border border-line bg-ink px-2 py-0.5 text-xs font-mono font-bold text-amber-300"
              value={tier1Ceiling}
              onChange={(e) => setTier1Ceiling(Number(e.target.value))}
            />
          </div>
          <span className="text-[11px] text-slate-400">
            Filtra modelos cujo custo combinado (3 entrada : 1 saída) ultrapassa este valor no Tier 1 (raciocínio).
          </span>
        </div>
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

      {/* Proposal preview card (if sync or preview action was taken) */}
      {proposal && (
        <div className="rounded-lg border border-brand/60 bg-slate-900/95 p-3.5 space-y-3 shadow-xl">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 border-b border-line pb-2.5">
            <div>
              <span className="font-semibold text-brand text-sm flex items-center gap-1.5">
                <span>🎯</span> Proposta de Atualização da Matriz de Modelos
              </span>
              <span className="text-xs text-slate-400 block mt-0.5">
                {proposal.eligible} de {proposal.considered} modelos qualificados (
                {proposal.mode === "alias" ? "apelidos -latest" : "versões fixas"}). Teto Tier 1: US${" "}
                {tier1Ceiling.toFixed(2)}/M.
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn-primary text-xs font-semibold py-1 px-3 flex items-center gap-1.5 shadow"
                onClick={applyImmediately}
                title="Aplica a matriz proposta como a configuração oficial imediatamente"
              >
                <span>✔</span> Atualizar Matriz Oficial
              </button>
              <button
                className="btn-ghost text-xs py-1 px-2.5 flex items-center gap-1"
                onClick={fillFromProposal}
                title="Carrega os modelos propostos no editor abaixo para revisão antes de salvar"
              >
                <span>✏️</span> Carregar no Editor
              </button>
              <button
                className="btn-ghost text-xs py-1 px-2.5 flex items-center gap-1"
                onClick={sendToInbox}
                title="Envia para a Caixa de Entrada do Founder como proposta formal assíncrona"
              >
                <span>📬</span> Enviar ao Inbox
              </button>
              <button className="btn-ghost text-xs py-1 px-2 text-slate-400 hover:text-white" onClick={() => setProposal(null)}>
                ✕ Fechar
              </button>
            </div>
          </div>

          {/* Diffs alert */}
          <div className="text-xs space-y-1 bg-ink/50 p-2.5 rounded border border-line">
            {proposal.added.length > 0 && (
              <div>
                <span className="text-emerald-400 font-semibold">Modelos sugeridos:</span>{" "}
                {proposal.added.join(", ")}
              </div>
            )}
            {proposal.removed.length > 0 && (
              <div>
                <span className="text-rose-400 font-semibold">Modelos substituídos:</span>{" "}
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
                Os modelos configurados atualmente já são a melhor escolha do catálogo sob o teto de US$ {tier1Ceiling.toFixed(2)}.
              </div>
            )}
          </div>

          {/* Matriz Proposta com destaque NOVO */}
          {proposal.clusters && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs border-b border-line/40 pb-1">
                <span className="font-semibold text-slate-200">Matriz Proposta (Valores Sugeridos)</span>
                <span className="text-[11px] text-emerald-400">
                  Modelos com tag <span className="bg-emerald-500/20 text-emerald-300 font-bold px-1 py-0.2 rounded border border-emerald-500/40 text-[10px]">NOVO</span> são recomendações que não estão na sua matriz
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {CLUSTERS_CONFIG.map((col) => {
                  const clusterProp = proposal.clusters?.[col.key];
                  return (
                    <div key={col.key} className="rounded-md border border-line/70 bg-ink/70 p-2 text-xs space-y-2">
                      <div className="font-semibold text-slate-200 border-b border-line/40 pb-1 flex items-center justify-between">
                        <span className="flex items-center gap-1.5">
                          <span>{col.icon}</span>
                          <span>{col.label}</span>
                        </span>
                      </div>
                      <div className="space-y-2">
                        {TIERS_CONFIG.map((t) => {
                          const picks = clusterProp?.[t.key as "tier1" | "tier2" | "tier3"] || [];
                          const currentCands = matrix[col.key]?.[t.key] || [];
                          return (
                            <div key={t.key} className="rounded bg-slate-800/60 p-1.5 border border-slate-700/50 space-y-1">
                              <div className="flex items-center justify-between text-[10px]">
                                <span className={`font-semibold px-1 py-0.2 rounded border ${t.badgeColor}`}>
                                  {t.label}
                                </span>
                                <span className="text-[9px] text-slate-400">{picks.length} modelo(s)</span>
                              </div>
                              {picks.length > 0 ? (
                                <div className="space-y-1">
                                  {picks.map((pick) => {
                                    const isNew = !currentCands.some(
                                      (c: Candidate) => c.model === pick.id || c.model.replace(/^~/, "") === pick.id.replace(/^~/, "")
                                    );
                                    return (
                                      <div key={pick.id} className="flex items-center justify-between gap-1.5 rounded bg-slate-900/70 px-1.5 py-1 border border-line/40">
                                        <div className="min-w-0 flex-1 flex items-center gap-1.5">
                                          <ProviderIcon provider={pick.vendor || pick.id} model={pick.id} className="w-3.5 h-3.5 flex-shrink-0" />
                                          <span className="font-mono text-[11px] text-cyan-300 truncate" title={pick.id}>
                                            {pick.name}
                                          </span>
                                          {isNew && (
                                            <span className="bg-emerald-500/20 text-emerald-300 font-bold text-[9px] border border-emerald-500/40 px-1 py-0 rounded flex-shrink-0 animate-pulse">
                                              NOVO
                                            </span>
                                          )}
                                        </div>
                                        <div className="flex items-center gap-1.5 flex-shrink-0 text-right font-mono text-[10px]">
                                          <span className="font-bold text-amber-300">★ {pick.score ?? pick.quality}</span>
                                          <span className="text-slate-300">{pick.price === 0 ? "GRÁTIS" : `$${pick.price.toFixed(2)}/M`}</span>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : (
                                <span className="text-slate-500 italic text-[10px]">nenhum sob o teto</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Matriz Oficial de Execução (3 Clusters x 3 Tiers) */}
      <div className="space-y-3 rounded-lg border border-line p-3.5 bg-slate-900/40">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-1 border-b border-line pb-2">
          <div>
            <div className="font-semibold text-xs text-slate-100 flex items-center gap-2">
              <span>Matriz Oficial de Execução (3 Clusters × 3 Tiers)</span>
              <span className="text-[10px] text-brand bg-brand/10 border border-brand/30 px-1.5 py-0.2 rounded font-mono">
                Fonte de verdade ativa
              </span>
            </div>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Cada agente busca no seu cluster o tier da tarefa. Se o modelo falhar ou faltar cota, cai na ordem para o próximo da lista.
            </p>
          </div>
          <span className="text-[10px] text-slate-500">
            Até {MAX_MODELS_PER_TIER} modelos de fallback por célula
          </span>
        </div>

        {/* 3 Colunas para os 3 Clusters */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {CLUSTERS_CONFIG.map((col) => {
            const clusterMatrix = matrix[col.key] || { tier1: [], tier2: [], tier3: [] };
            return (
              <div key={col.key} className="rounded-lg border border-line/80 bg-ink/70 p-2.5 text-xs space-y-2.5 flex flex-col justify-between">
                <div>
                  {/* Cluster Header - SEM NENHUM badge de Padrão Tier */}
                  <div className="border-b border-line/40 pb-2 mb-2">
                    <div className="font-semibold text-slate-100 flex items-center gap-1.5 text-xs">
                      <span className="text-sm">{col.icon}</span>
                      <span>{col.label}</span>
                    </div>
                    <div className="text-[10px] text-slate-400 mt-0.5 truncate" title={col.roles.join(", ")}>
                      Loompas: {col.roles.join(", ")}
                    </div>
                  </div>

                  {/* 3 Tiers dentro deste Cluster */}
                  <div className="space-y-2.5">
                    {TIERS_CONFIG.map((t) => {
                      const candidates = clusterMatrix[t.key as "tier1" | "tier2" | "tier3"] || [];
                      return (
                        <div key={t.key} className="rounded-md bg-slate-900/60 p-2 border border-slate-700/60 space-y-1.5">
                          {/* Cabeçalho da célula */}
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <span className={`font-semibold px-1.5 py-0.2 rounded border text-[10px] ${t.badgeColor}`}>
                                {t.label}
                              </span>
                              <span className="text-[10px] text-slate-400 truncate max-w-[130px]" title={t.sublabel}>
                                {t.sublabel}
                              </span>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <span className="text-[9px] text-slate-500 font-mono">
                                {candidates.length}/{MAX_MODELS_PER_TIER}
                              </span>
                              {candidates.length < MAX_MODELS_PER_TIER && (
                                <button
                                  type="button"
                                  className="btn-ghost py-0 px-1 text-[10px] text-brand hover:text-white"
                                  onClick={() => setPickerTarget({ cluster: col.key, tier: t.key })}
                                  title={`Adicionar modelo ao ${col.label} > ${t.label}`}
                                >
                                  + modelo
                                </button>
                              )}
                            </div>
                          </div>

                          {/* Lista ordenada de candidatos */}
                          {candidates.length > 0 ? (
                            <div className="space-y-1.5">
                              {candidates.map((c: Candidate, i: number) => {
                                const info = modelsMap[c.model] || modelsMap[c.model.replace(/^~/, "")];
                                const isOverCeiling = t.key === "tier1" && info && info.price > tier1Ceiling;
                                const isRaw = editingRaw[`${col.key}:${t.key}:${i}`];

                                return (
                                  <div key={i} className="rounded border border-line/60 bg-slate-950/70 p-1.5 text-xs space-y-1">
                                    <div className="flex items-center gap-1.5 min-w-0">
                                      <span className="w-3 text-[10px] text-slate-500 font-mono flex-shrink-0">{i + 1}</span>
                                      <ProviderIcon provider={c.provider} model={c.model} className="w-3.5 h-3.5 flex-shrink-0 text-slate-300" />
                                      
                                      {c.provider === "openrouter" && !isRaw ? (
                                        <div className="flex-1 flex items-center gap-1 min-w-0">
                                          <button
                                            type="button"
                                            className="flex-1 flex items-center justify-between text-left rounded border border-line bg-ink px-1.5 py-0.5 text-[11px] text-slate-200 hover:border-brand/70 transition-colors min-w-0"
                                            onClick={() => setPickerTarget({ cluster: col.key, tier: t.key, index: i })}
                                            title="Clique para trocar o modelo"
                                          >
                                            <span className="truncate font-mono font-medium text-cyan-300">
                                              {c.model || <span className="text-slate-500 italic">Escolher…</span>}
                                            </span>
                                            <span className="text-slate-400 text-[9px] ml-1 flex-shrink-0">▾</span>
                                          </button>
                                          <button
                                            type="button"
                                            className="btn-ghost py-0.5 px-1 text-[10px]"
                                            title="Editar ID manualmente"
                                            onClick={() => setEditingRaw({ ...editingRaw, [`${col.key}:${t.key}:${i}`]: true })}
                                          >
                                            ✏️
                                          </button>
                                        </div>
                                      ) : (
                                        <div className="flex-1 flex items-center gap-1 min-w-0">
                                          <input
                                            className="flex-1 rounded border border-line bg-ink px-1.5 py-0.5 text-[11px] font-mono text-slate-200 min-w-0"
                                            value={c.model}
                                            placeholder="id do modelo"
                                            onChange={(e) => setCandidate(col.key, t.key, i, { model: e.target.value })}
                                          />
                                          {c.provider === "openrouter" && (
                                            <button
                                              type="button"
                                              className="btn-ghost py-0.5 px-1 text-[10px]"
                                              title="Escolher da lista guiada"
                                              onClick={() => {
                                                setEditingRaw({ ...editingRaw, [`${col.key}:${t.key}:${i}`]: false });
                                                setPickerTarget({ cluster: col.key, tier: t.key, index: i });
                                              }}
                                            >
                                              📋
                                            </button>
                                          )}
                                        </div>
                                      )}

                                      <div className="flex items-center gap-0.5 flex-shrink-0">
                                        <button
                                          type="button"
                                          className="btn-ghost py-0 px-1 text-[10px] disabled:opacity-30"
                                          disabled={i === 0}
                                          onClick={() => moveCandidate(col.key, t.key, i, -1)}
                                          title="Mover para cima (prioridade maior)"
                                        >
                                          ↑
                                        </button>
                                        <button
                                          type="button"
                                          className="btn-ghost py-0 px-1 text-[10px] disabled:opacity-30"
                                          disabled={i === candidates.length - 1}
                                          onClick={() => moveCandidate(col.key, t.key, i, 1)}
                                          title="Mover para baixo (fallback posterior)"
                                        >
                                          ↓
                                        </button>
                                        <button
                                          type="button"
                                          className="btn-ghost py-0 px-1 text-[10px] text-rose-400 hover:text-rose-200"
                                          onClick={() => removeCandidate(col.key, t.key, i)}
                                          title="Remover modelo"
                                        >
                                          ✕
                                        </button>
                                      </div>
                                    </div>

                                    {/* Metadados e benchmarks */}
                                    {info ? (
                                      <div className="ml-4 flex flex-wrap items-center gap-1.5 text-[10px]">
                                        <span className="font-bold text-amber-300">★ {info.score ?? info.quality}</span>
                                        {(() => {
                                          const cb = info.cost_benefit ?? (info.price > 0 && (info.score ?? info.quality) ? Math.round(((info.score ?? info.quality) / info.price) * 10) / 10 : null);
                                          return cb != null ? (
                                            <span className="text-cyan-300 bg-cyan-950/70 px-1 py-0 rounded border border-cyan-800/50 font-mono">
                                              {cb} pts/$
                                            </span>
                                          ) : null;
                                        })()}
                                        <span className="font-mono text-slate-300">
                                          {info.price === 0 ? <span className="text-emerald-400 font-semibold">GRÁTIS</span> : `$${info.price.toFixed(2)}/M`}
                                        </span>
                                        {isOverCeiling && (
                                          <span className="text-amber-300 bg-amber-950/80 px-1 py-0 rounded border border-amber-800 font-semibold" title={`Acima do teto de Tier 1 ($${tier1Ceiling.toFixed(2)})`}>
                                            ⚠️ &gt; teto
                                          </span>
                                        )}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="text-center py-2 text-[10px] text-slate-500 italic">
                              Nenhum modelo configurado.{" "}
                              <button
                                type="button"
                                className="text-brand hover:underline"
                                onClick={() => setPickerTarget({ cluster: col.key, tier: t.key })}
                              >
                                + adicionar
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Mapeamento de Papel -> Tier */}
      <div className="rounded-md border border-line p-2.5 bg-slate-900/30">
        <div className="mb-1 font-semibold text-xs text-slate-200">
          Papel → Tier Padrão{" "}
          <span className="text-xs font-normal text-slate-400">
            — define qual tier da matriz o agente usa para tarefas padrão (tarefas complexas escalam para Tier 1)
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
          {Object.entries(roles)
            .filter(([role]) => !BACKEND_SERVICES.has(role))
            .map(([role, tier]) => (
              <label
                key={role}
                className="flex items-center justify-between gap-2 text-xs bg-slate-900/60 p-1.5 rounded border border-line/40"
              >
                <span className="truncate font-medium text-slate-200" title={role}>
                  {ROLE_DISPLAY[role] || role}
                </span>
                <select
                  className={select}
                  value={tier}
                  onChange={(e) => setRoles({ ...roles, [role]: e.target.value })}
                >
                  {["tier1", "tier2", "tier3"].map((t) => (
                    <option key={t} value={t}>{t}</option>
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
              matrix,
              tiers: {
                tier1: (matrix.strategy?.tier1 || matrix.engineering?.tier1 || []).filter((c: Candidate) => c.model.trim()),
                tier2: (matrix.engineering?.tier2 || matrix.strategy?.tier2 || []).filter((c: Candidate) => c.model.trim()),
                tier3: (matrix.routine?.tier3 || []).filter((c: Candidate) => c.model.trim()),
              },
              roles,
              tier1_ceiling: tier1Ceiling,
            })
          }
        >
          Salvar modelos
        </button>
      </div>

      {/* Modal de seleção de modelo interativo */}
      {pickerTarget && (
        <OpenRouterModelPickerModal
          isOpen={Boolean(pickerTarget)}
          onClose={() => setPickerTarget(null)}
          onSelect={(modelId) => {
            if (pickerTarget.index !== undefined) {
              setCandidate(pickerTarget.cluster, pickerTarget.tier, pickerTarget.index, {
                model: modelId,
                provider: "openrouter",
              });
            } else {
              addCandidate(pickerTarget.cluster, pickerTarget.tier, modelId, "openrouter");
            }
            setPickerTarget(null);
          }}
          clusterTarget={pickerTarget.cluster}
          tierTarget={pickerTarget.tier}
          tier1Ceiling={tier1Ceiling}
          catalog={proposal || catalog}
          modelsMap={modelsMap}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------- budget

function BudgetPanel({ s, onSave }: { s: Settings; onSave: (p: SettingsPatch) => Promise<void> }) {
  const [b, setB] = useState(s.budget);
  const [par, setPar] = useState(s.schedule.max_parallel);
  const [tier1Ceil, setTier1Ceil] = useState(s.models?.tier1_ceiling ?? 5.0);

  useEffect(() => {
    setB(s.budget);
    setPar(s.schedule.max_parallel);
    setTier1Ceil(s.models?.tier1_ceiling ?? 5.0);
  }, [s]);

  return (
    <div className="space-y-3">
      <label className="block">
        Teto de custo por modelo Tier 1 (US$/1M tokens)
        <input
          type="number"
          min={0.1}
          step={0.5}
          className={input}
          value={tier1Ceil}
          onChange={(e) => setTier1Ceil(Number(e.target.value))}
        />
        <span className="text-[11px] text-slate-400 block mt-0.5">
          Limite superior de custo combinado (3 entrada : 1 saída) para modelos de raciocínio profundo (Tier 1). Modelos mais caros que este valor são desqualificados do Tier 1.
        </span>
      </label>
      <label className="block">
        Teto mensal (US$)
        <input
          type="number"
          min={0}
          step={5}
          className={input}
          value={b.monthly_cap_usd}
          onChange={(e) => setB({ ...b, monthly_cap_usd: Number(e.target.value) })}
        />
      </label>
      <label className="block">
        Alerta em (fração do teto)
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          className={input}
          value={b.warn_at_fraction}
          onChange={(e) => setB({ ...b, warn_at_fraction: Number(e.target.value) })}
        />
      </label>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={b.hard_stop}
          onChange={(e) => setB({ ...b, hard_stop: e.target.checked })}
        />{" "}
        Pausar a esteira ao atingir o teto
      </label>
      <label className="block">
        Histórias em paralelo
        <input
          type="number"
          min={1}
          max={32}
          className={input}
          value={par}
          onChange={(e) => setPar(Number(e.target.value))}
        />
      </label>
      <p className="text-xs text-slate-500">
        O custo exato é acompanhado no painel de cada provedor; aqui é uma estimativa por tokens.
      </p>
      <div className="text-right">
        <button
          className="btn-primary"
          onClick={() =>
            onSave({
              budget: b,
              max_parallel: par,
              tier1_ceiling: tier1Ceil,
            })
          }
        >
          Salvar orçamento
        </button>
      </div>
    </div>
  );
}
