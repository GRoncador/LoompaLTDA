import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { BudgetPeriod, Candidate, ClusterName, ModelMatrix, ModelPick, ModelProposalDTO, ProbeResult, ProviderInfo, RoleTaskInfo, Settings, SettingsPatch } from "../types";
import { Modal } from "./Modal";
import { PROVIDER_NAMES, ProviderBadge, ProviderIcon, cleanModelName, cleanModelId } from "./ProviderIcon";

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
    <Modal title="⚙ Configurações da fábrica" onClose={onClose} className="w-[96vw] max-w-[1600px]">
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
  const used = new Set(Object.values(s.matrix ?? {}).flatMap((t) => Object.values(t).flat()).map((c) => c.provider));
  const providers = onlyNeeded ? s.providers.filter((p) => used.has(p.name)) : s.providers;

  const test = async (name: string) => {
    setProbe((p) => ({ ...p, [name]: "…" }));
    try { const r = await api.testProvider(slug, name); setProbe((p) => ({ ...p, [name]: r })); }
    catch (e) { setProbe((p) => ({ ...p, [name]: { name, ok: false, detail: String(e), model: "", latency_ms: 0 } })); }
  };
  const saveKeys = async () => {
    const patch: SettingsPatch = { providers: {}, tools: {} };
    for (const [name, v] of Object.entries(keys)) if (v.trim()) patch.providers![name] = { api_key: v.trim(), scope };
    if (tavilyKey.trim()) patch.tools!.tavily = { api_key: tavilyKey.trim(), scope };
    await onSave(patch);
    setKeys({}); setTavilyKey("");
  };
  const dirty = Object.values(keys).some((v) => v.trim()) || Boolean(tavilyKey.trim());

  /** One row of the list. Tavily is the same shape with a different subtitle: it is a search API,
   * not a model provider, and saying so in place is clearer than a footnote. */
  const row = (opts: {
    name: string; label: string; note: string; recommended?: boolean;
    configured: boolean; keyLabel: string; source: string | null; env: string;
    consoleUrl: string; modelsUrl?: string; needsKey: boolean;
    value: string; onChange: (v: string) => void; onClear?: () => void;
  }) => {
    const r = probe[opts.name];
    return (
      <div key={opts.name} className="grid grid-cols-1 gap-2 border-t border-line/60 py-2.5 md:grid-cols-[minmax(190px,1.1fr)_minmax(150px,1fr)_minmax(190px,1.2fr)_auto] md:items-center">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 font-medium text-slate-200">
            <ProviderBadge provider={opts.name} className="h-4 w-4" />
            {opts.recommended && (
              <span className="rounded border border-brand/40 bg-brand/15 px-1.5 py-0 text-[10px] font-semibold text-brand">recomendado</span>
            )}
          </div>
          <div className="mt-0.5 text-[11px] text-slate-500">{opts.note}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px]">
            {opts.consoleUrl && <a className="text-brand hover:underline" href={opts.consoleUrl} target="_blank" rel="noreferrer">criar chave ↗</a>}
            {opts.modelsUrl && <a className="text-slate-400 hover:text-brand hover:underline" href={opts.modelsUrl} target="_blank" rel="noreferrer">modelos disponíveis ↗</a>}
          </div>
        </div>

        <div className="min-w-0 text-xs">
          <span className={opts.configured ? "text-emerald-300" : "text-amber-300"}>{opts.keyLabel}</span>
          {opts.source && <span className="ml-1 text-slate-500">({opts.source})</span>}
          {r === "…" && <div className="text-slate-400">testando…</div>}
          {r && r !== "…" && (
            <div className={r.ok ? "text-emerald-300" : "text-red-300"}>
              {r.ok ? "✔" : "✘"} {r.detail}{r.latency_ms ? ` · ${r.latency_ms} ms` : ""}
            </div>
          )}
        </div>

        <div className="min-w-0">
          {opts.needsKey
            ? <input type="password" autoComplete="off" placeholder={opts.env} className={input} value={opts.value} onChange={(e) => opts.onChange(e.target.value)} />
            : <span className="text-xs text-slate-500">não precisa de chave</span>}
        </div>

        <div className="flex items-center gap-1 md:justify-end">
          <button className="btn-ghost whitespace-nowrap text-xs" onClick={() => test(opts.name)} disabled={opts.needsKey && !opts.configured}>Testar</button>
          {opts.configured && opts.needsKey && opts.onClear && (
            <button className="btn-ghost px-2 text-xs text-rose-300 hover:text-rose-200" title="remover chave" onClick={opts.onClear}>✕</button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400">
          Uma chave da OpenRouter já alcança todos os modelos. As outras são opcionais — use a de quem você já tem conta.
        </p>
        <label className="flex items-center gap-2 text-xs text-slate-400">Guardar novas chaves em
          <select className={select} value={scope} onChange={(e) => setScope(e.target.value as "hub" | "factory")}>
            <option value="hub">hub (todas as fábricas)</option>
            <option value="factory">só esta fábrica</option>
          </select>
        </label>
      </div>

      <div className="rounded-lg border border-line bg-slate-900/40 px-3 pb-2">
        {providers.map((p) =>
          row({
            name: p.name,
            label: p.label,
            note: PROVIDER_NOTE[p.name] ?? "modelos de IA",
            recommended: p.recommended,
            configured: p.key.configured,
            keyLabel: p.key.label,
            source: p.key.source,
            env: p.api_key_env,
            consoleUrl: p.console_url,
            modelsUrl: p.models_url,
            needsKey: p.needs_key,
            value: keys[p.name] ?? "",
            onChange: (v) => setKeys({ ...keys, [p.name]: v }),
            onClear: () => onSave({ providers: { [p.name]: { clear_key: true } } }),
          })
        )}
        {!onlyNeeded && row({
          name: "tavily",
          label: "Tavily",
          note: "API de pesquisa na web — não é provedor de modelos",
          configured: s.tools.tavily.key.configured,
          keyLabel: s.tools.tavily.key.label,
          source: s.tools.tavily.key.source,
          env: s.tools.tavily.api_key_env,
          consoleUrl: s.tools.tavily.console_url,
          needsKey: true,
          value: tavilyKey,
          onChange: setTavilyKey,
          onClear: () => onSave({ tools: { tavily: { clear_key: true } } }),
        })}
      </div>

      <div className="text-right"><button className="btn-primary" disabled={!dirty} onClick={saveKeys}>Guardar chaves</button></div>
    </div>
  );
}

const PROVIDER_NOTE: Record<string, string> = {
  openrouter: "uma chave para todos os modelos, com teto de gastos no painel deles",
  gemini: "modelos Gemini, do Google",
  anthropic: "modelos Claude, da Anthropic",
  openai: "modelos GPT, da OpenAI",
  xai: "modelos Grok, da xAI",
  deepseek: "modelos DeepSeek",
  ollama: "modelos rodando na sua própria máquina",
};

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

const CLUSTERS_CONFIG = [
  {
    key: "strategy",
    label: "Estratégia & Produto",
    icon: "🏛️",
    roles: ["Master", "Architect", "Spec Loompa", "Product Owner", "Analyst"],
    weights: [
      { label: "INTEL", pct: "50%", color: "text-purple-300 border-purple-800/50 bg-purple-950/40" },
      { label: "CODE", pct: "30%", color: "text-blue-300 border-blue-800/50 bg-blue-950/40" },
      { label: "AGENTIC", pct: "20%", color: "text-emerald-300 border-emerald-800/50 bg-emerald-950/40" },
    ],
  },
  {
    key: "engineering",
    label: "Engenharia de Código",
    icon: "⚙️",
    roles: ["Worker (Dev)", "Inspector (QA Judge)"],
    weights: [
      { label: "CODE", pct: "60%", color: "text-blue-300 border-blue-800/50 bg-blue-950/40" },
      { label: "AGENTIC", pct: "30%", color: "text-emerald-300 border-emerald-800/50 bg-emerald-950/40" },
      { label: "INTEL", pct: "10%", color: "text-purple-300 border-purple-800/50 bg-purple-950/40" },
    ],
  },
  {
    key: "routine",
    label: "Rotina & Suporte",
    icon: "📋",
    roles: ["Deployer", "Storyteller", "Compliance", "Metrics"],
    weights: [
      { label: "AGENTIC", pct: "55%", color: "text-emerald-300 border-emerald-800/50 bg-emerald-950/40" },
      { label: "INTEL", pct: "30%", color: "text-purple-300 border-purple-800/50 bg-purple-950/40" },
      { label: "CODE", pct: "15%", color: "text-blue-300 border-blue-800/50 bg-blue-950/40" },
    ],
  },
] as const;

// What every role shares when the cluster split is off: the intelligence index alone, the one the
// catalogue publishes for far more models (no weights to show).
const GENERAL_CONFIG = {
  key: "general",
  label: "Cluster geral",
  icon: "🧩",
  roles: ["Todos os Loompas"],
  weights: [],
} as const;

type ClusterConfig = {
  key: string;
  label: string;
  icon: string;
  roles: readonly string[];
  weights: readonly { label: string; pct: string; color: string }[];
};

const CLUSTER_ICON: Record<string, string> = {
  strategy: "🏛️",
  engineering: "⚙️",
  routine: "📋",
  general: "🧩",
};

const TIERS_CONFIG = [
  {
    key: "tier1",
    label: "Tier 1",
    sublabel: "Alta Cognição / Raciocínio",
    rankedBy: "maior nota do cluster, abaixo do teto de custo",
    badgeColor: "text-amber-300 border-amber-800/60 bg-amber-950/40",
  },
  {
    key: "tier2",
    label: "Tier 2",
    sublabel: "Custo-Benefício / Execução Ágil",
    rankedBy: "melhor custo-benefício (pontos por US$ 1M de tokens) entre os que atingem o piso de nota",
    badgeColor: "text-cyan-300 border-cyan-800/60 bg-cyan-950/40",
  },
  {
    key: "tier3",
    label: "Tier 3",
    sublabel: "Rotina / Custo US$ 0",
    rankedBy: "maior nota entre os modelos gratuitos",
    badgeColor: "text-emerald-300 border-emerald-800/60 bg-emerald-950/40",
  },
] as const;

type TierKey = "tier1" | "tier2" | "tier3";

const MAX_MODELS_PER_TIER = 6;
const NO_OPENROUTER_HINT =
  "A lista de modelos e a sugestão inteligente vêm da OpenRouter. Configure a chave da OpenRouter na aba " +
  "“Provedores e chaves” para liberar o catálogo completo e a recomendação automática.";

/** The score a model gets in one cluster, and the points it buys per dollar. */
function clusterScore(m: ModelPick, cluster: ClusterName): number | null {
  return m.scores?.[cluster] ?? m.score ?? m.quality ?? null;
}
function clusterValue(m: ModelPick, cluster: ClusterName): number | null {
  const declared = m.cost_benefits?.[cluster];
  if (declared != null) return declared;
  const s = clusterScore(m, cluster);
  return s != null && m.price > 0 ? Math.round((s / m.price) * 10) / 10 : null;
}
function isFree(m: ModelPick): boolean {
  return m.free ?? m.price === 0;
}
function priceLabel(price: number): string {
  return price === 0 ? "GRÁTIS" : `$${price.toFixed(2)}/M`;
}

/** A provider picker that shows a mark and a name while open, and only the mark once chosen —
 * a native <select> cannot draw an SVG, and the marks are what make a dense row readable. */
function ProviderSelect({
  value,
  providers,
  onChange,
}: {
  value: string;
  providers: ProviderInfo[];
  onChange: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);
  const known = providers.some((p) => p.name === value);
  return (
    <span className="relative flex-shrink-0">
      <button
        type="button"
        className={`flex items-center gap-0.5 rounded border px-1 py-0.5 transition-colors ${known ? "border-line bg-ink hover:border-brand/70" : "border-amber-800/70 bg-amber-950/30"}`}
        aria-label={`Provedor: ${PROVIDER_NAMES[value] ?? value}`}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
      >
        <ProviderBadge provider={value} nameless className="h-3.5 w-3.5" />
        <span className="text-[8px] text-slate-500">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 min-w-[190px] rounded-md border border-line bg-ink p-1 shadow-xl" onClick={(e) => e.stopPropagation()}>
          {providers.length === 0 ? (
            <p className="p-1.5 text-[10px] text-slate-400">Nenhum provedor com chave configurada.</p>
          ) : (
            providers.map((p) => (
              <button
                key={p.name}
                type="button"
                className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] transition-colors ${p.name === value ? "bg-brand/15 text-brand" : "text-slate-200 hover:bg-slate-800"}`}
                onClick={() => { onChange(p.name); setOpen(false); }}
              >
                <ProviderBadge provider={p.name} className="h-3.5 w-3.5" />
              </button>
            ))
          )}
        </div>
      )}
    </span>
  );
}

/** The three benchmark indices, always in the same order and colours. */
function Benchmarks({ m }: { m: ModelPick }) {
  const cells: [string, number | null | undefined, string][] = [
    ["Int", m.intelligence, "text-purple-300"],
    ["Cod", m.coding, "text-blue-300"],
    ["Agt", m.agentic, "text-emerald-300"],
  ];
  return (
    <span className="flex flex-wrap gap-x-2 font-mono text-[10px] text-slate-500">
      {cells.map(([label, v, color]) =>
        v == null ? null : (
          <span key={label}>
            {label} <strong className={color}>{v.toFixed(1)}</strong>
          </span>
        )
      )}
    </span>
  );
}

/** The cluster's own number, carrying the cluster's emoji so several stay distinguishable. */
function ClusterScore({ cluster, m, mode }: { cluster: ClusterName; m: ModelPick; mode: "score" | "value" }) {
  const v = mode === "value" ? clusterValue(m, cluster) : clusterScore(m, cluster);
  if (v == null) return null;
  return (
    <span
      className={`flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold ${
        mode === "value"
          ? "border-cyan-800/60 bg-cyan-950/70 text-cyan-300"
          : cluster === "general"
            ? "border-purple-800/60 bg-purple-950/60 text-purple-300"
            : "border-amber-800/60 bg-amber-950/60 text-amber-300"
      }`}
      title={
        mode === "value"
          ? "Custo-benefício: pontos de benchmark por US$ 1M de tokens"
          : cluster === "general"
            ? "Nota de inteligência"
            : "Nota deste cluster"
      }
    >
      <span aria-hidden="true">{CLUSTER_ICON[cluster]}</span>
      {mode === "value" ? `${v} pts/$` : v.toFixed(1)}
    </span>
  );
}

// --------------------------------------------------------------- the model picker

type SortKey = "score" | "value" | "price" | "new" | "name";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "score", label: "nota" },
  { key: "value", label: "custo-benefício" },
  { key: "price", label: "preço" },
  { key: "new", label: "lançamento" },
  { key: "name", label: "nome" },
];

const RECOMMENDED_PER_TIER = 12;

/** The list one tier of one cluster offers, ranked the way that tier is meant to be read:
 * Tier 1 by the cluster's score, Tier 2 by what a dollar buys among the models within the ceiling
 * that reach `floor` of Tier 1's best (the same rule as `_value_tier` in the catalogue), Tier 3 by
 * score among the free ones. The catalogue's own picks (one per vendor) are flagged, not the only
 * thing offered. */
function recommendedFor(all: ModelPick[], cluster: ClusterName, tier: TierKey, ceiling: number, floor: number): ModelPick[] {
  // `eligible === false` already excludes every alias; the explicit check says why out loud.
  const eligible = all.filter((m) => m.eligible !== false && !m.alias);
  if (tier === "tier3") {
    return eligible
      .filter(isFree)
      .sort((a, b) => (clusterScore(b, cluster) ?? 0) - (clusterScore(a, cluster) ?? 0))
      .slice(0, RECOMMENDED_PER_TIER);
  }
  const under = eligible.filter((m) => !isFree(m) && m.price > 0 && m.price <= ceiling);
  if (tier === "tier1") {
    return under
      .sort((a, b) => (clusterScore(b, cluster) ?? 0) - (clusterScore(a, cluster) ?? 0))
      .slice(0, RECOMMENDED_PER_TIER);
  }
  const best = Math.max(0, ...under.map((m) => clusterScore(m, cluster) ?? 0));
  return under
    .filter((m) => (clusterScore(m, cluster) ?? 0) >= floor * best)
    .sort((a, b) => (clusterValue(b, cluster) ?? 0) - (clusterValue(a, cluster) ?? 0))
    .slice(0, RECOMMENDED_PER_TIER);
}

function sortModels(list: ModelPick[], by: SortKey, cluster: ClusterName | null): ModelPick[] {
  const ranked = [...list];
  const score = (m: ModelPick) => (cluster ? clusterScore(m, cluster) : m.score ?? m.quality) ?? -1;
  const value = (m: ModelPick) => (cluster ? clusterValue(m, cluster) : m.cost_benefit) ?? -1;
  ranked.sort((a, b) => {
    switch (by) {
      case "value": return value(b) - value(a);
      case "price": return a.price - b.price;
      case "new": return (b.created ?? "").localeCompare(a.created ?? "");
      case "name": return a.name.localeCompare(b.name);
      default: return score(b) - score(a);
    }
  });
  return ranked;
}

function ModelRow({
  m,
  cluster,
  mode,
  suggested,
  overCeiling,
  onSelect,
}: {
  m: ModelPick;
  /** null in the extended search with no cluster filter: then only the raw benchmarks show. */
  cluster: ClusterName | null;
  mode: "score" | "value";
  suggested?: boolean;
  overCeiling?: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className={`flex items-center justify-between gap-2 rounded-md border p-2 text-xs transition-colors ${
        overCeiling
          ? "border-amber-900/50 bg-amber-950/20 hover:border-amber-700/60"
          : "border-line/70 bg-slate-800/40 hover:border-brand/60 hover:bg-slate-800/80"
      }`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <ProviderIcon provider={m.vendor || m.id} model={m.id} className="h-4 w-4 flex-shrink-0 text-slate-300" />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 truncate font-semibold text-slate-200">
            <span className="truncate">{m.name}</span>
            {suggested && (
              <span className="flex-shrink-0 rounded border border-emerald-500/40 bg-emerald-500/20 px-1 text-[9px] font-bold text-emerald-300">
                sugerido
              </span>
            )}
            {m.alias && (
              <span
                className="flex-shrink-0 rounded border border-violet-700/60 bg-violet-950/50 px-1 text-[9px] text-violet-300"
                title={`Apelido: segue sozinho a versão nova do fabricante${m.alias_target ? ` (hoje: ${m.alias_target})` : ""}. Nunca é recomendado — some se você quiser essa atualização automática.`}
              >
                apelido
              </span>
            )}
            {!m.alias && m.eligible === false && m.excluded && (
              <span className="flex-shrink-0 rounded border border-slate-700 bg-slate-900 px-1 text-[9px] text-slate-400" title={`Fora do ranking: ${m.excluded}`}>
                fora do ranking
              </span>
            )}
            {overCeiling && (
              <span className="flex-shrink-0 rounded border border-amber-800/70 bg-amber-950/90 px-1 font-mono text-[9px] text-amber-300">
                acima do teto
              </span>
            )}
          </div>
          <div className="truncate font-mono text-[11px] text-slate-400">{m.id}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2">
            <Benchmarks m={m} />
            {m.created && <span className="font-mono text-[10px] text-slate-600">{m.created.slice(0, 7)}</span>}
          </div>
        </div>
      </div>

      <div className="flex flex-shrink-0 items-center gap-2">
        <div className="flex flex-col items-end gap-0.5">
          {cluster && <ClusterScore cluster={cluster} m={m} mode={mode} />}
          <span className={`font-mono text-[11px] ${m.price === 0 ? "font-semibold text-emerald-400" : "text-slate-300"}`}>
            {priceLabel(m.price)}
          </span>
        </div>
        <button className="btn-primary px-2.5 py-1 text-xs font-semibold" onClick={() => onSelect(m.id)}>
          Escolher
        </button>
      </div>
    </div>
  );
}

function ModelPickerModal({
  onClose,
  onSelect,
  tierTarget,
  clusterTarget,
  tier1Ceiling,
  tier2Floor,
  catalog,
  clusters,
}: {
  onClose: () => void;
  onSelect: (modelId: string) => void;
  tierTarget: TierKey;
  clusterTarget: ClusterName;
  tier1Ceiling: number;
  tier2Floor: number;
  catalog: ModelProposalDTO | null;
  clusters: ClusterConfig[];
}) {
  const [extended, setExtended] = useState(false);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<"all" | "pinned" | "alias" | "free">("all");
  const [sort, setSort] = useState<SortKey>(tierTarget === "tier2" ? "value" : "score");
  const [cluster, setCluster] = useState<ClusterName | "all">(clusterTarget);

  const all = catalog?.all_models ?? [];
  const tier = TIERS_CONFIG.find((t) => t.key === tierTarget)!;
  const mode: "score" | "value" = tierTarget === "tier2" ? "value" : "score";
  const suggested = new Set(
    (catalog?.clusters?.[clusterTarget]?.[tierTarget] ?? []).map((m) => m.id)
  );

  const matches = (m: ModelPick) => {
    if (kind === "pinned" && m.alias) return false;
    if (kind === "alias" && !m.alias) return false;
    if (kind === "free" && !isFree(m)) return false;
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q) || (m.vendor ?? "").toLowerCase().includes(q);
  };

  const recommended = recommendedFor(all, clusterTarget, tierTarget, tier1Ceiling, tier2Floor).filter(matches);
  const activeCluster: ClusterName | null = cluster === "all" ? null : cluster;
  const extendedList = sortModels(all.filter(matches), sort, activeCluster);

  const pick = (id: string) => { onSelect(id); onClose(); };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-xl border border-brand/50 bg-ink p-4 shadow-2xl">
        <div className="mb-3 flex items-start justify-between gap-2 border-b border-line pb-2">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <span aria-hidden="true">{CLUSTER_ICON[clusterTarget]}</span>
              {extended ? "Todos os modelos da OpenRouter" : `Modelos recomendados · ${tier.label}`}
              <span className="chip bg-brand/20 font-mono text-xs text-brand">{tier.sublabel}</span>
            </h3>
            <p className="mt-0.5 text-xs text-slate-400">
              {extended
                ? "O catálogo inteiro, inclusive apelidos -latest e o que a recomendação descartou. Filtre por cluster para ver a nota dele."
                : `Ordenados por ${tier.rankedBy}${tierTarget === "tier2" ? ` (${Math.round(tier2Floor * 100)}% da maior nota do Tier 1)` : ""}. A recomendação usa só versões fixas — apelidos -latest ficam em “mais modelos”.`}
            </p>
          </div>
          <button className="btn-ghost py-1 text-xs" onClick={onClose}>✕ Fechar</button>
        </div>

        <div className="mb-3 space-y-2">
          <input
            type="text"
            className="w-full rounded-md border border-line bg-slate-900 px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:border-brand focus:outline-none"
            placeholder="Buscar por fabricante, nome ou id (ex.: grok, glm, gemini, qwen)…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          {extended && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px]">
              <span className="flex items-center gap-1">
                <span className="text-slate-500">Nota do cluster:</span>
                <select className={select} value={cluster} onChange={(e) => setCluster(e.target.value as ClusterName | "all")}>
                  {clusters.map((c) => (
                    <option key={c.key} value={c.key}>{c.icon} {c.label}</option>
                  ))}
                  <option value="all">sem filtro (só os benchmarks)</option>
                </select>
              </span>
              <span className="flex items-center gap-1">
                <span className="text-slate-500">Ordenar por:</span>
                <select className={select} value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                  {SORTS.map((o) => (
                    <option key={o.key} value={o.key} disabled={!activeCluster && (o.key === "score" || o.key === "value")}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </span>
              <span className="flex items-center gap-1">
                <span className="text-slate-500">Tipo:</span>
                <select className={select} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                  <option value="all">todos</option>
                  <option value="pinned">só versões fixas</option>
                  <option value="alias">só apelidos (-latest)</option>
                  <option value="free">só gratuitos</option>
                </select>
              </span>
              <span className="text-slate-500">{extendedList.length} modelo(s)</span>
            </div>
          )}
        </div>

        <div className="scroll-thin flex-1 space-y-1.5 overflow-y-auto pr-1">
          {(extended ? extendedList : recommended).length === 0 ? (
            <p className="py-6 text-center text-xs text-slate-500">
              {all.length === 0
                ? "Nenhum catálogo carregado. Use “Atualizar lista de modelos OpenRouter”."
                : "Nenhum modelo encontrado com os termos pesquisados."}
            </p>
          ) : (
            (extended ? extendedList : recommended).map((m) => (
              <ModelRow
                key={m.id}
                m={m}
                cluster={extended ? activeCluster : clusterTarget}
                mode={extended ? (sort === "value" ? "value" : "score") : mode}
                suggested={!extended && suggested.has(m.id)}
                overCeiling={tierTarget === "tier1" && m.price > tier1Ceiling}
                onSelect={pick}
              />
            ))
          )}
        </div>

        <div className="mt-3 border-t border-line pt-2 text-center">
          <button
            className="text-xs text-brand hover:underline"
            onClick={() => { setExtended((e) => !e); setSearch(""); setKind("all"); }}
          >
            {extended
              ? "← voltar aos recomendados deste tier"
              : `mais modelos — buscar nos ${all.length} modelos do catálogo →`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- the models tab

function emptyMatrix(clusters: string[]): ModelMatrix {
  return Object.fromEntries(clusters.map((c) => [c, { tier1: [], tier2: [], tier3: [] }]));
}

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
  const [matrix, setMatrix] = useState<ModelMatrix>(s.matrix ?? {});
  const [roles, setRoles] = useState<Record<string, string>>(s.roles);
  const [roleTasks, setRoleTasks] = useState<Record<string, string>>(
    Object.fromEntries((s.role_tasks ?? []).map((t) => [t.key, t.tier]))
  );
  const [clustersEnabled, setClustersEnabled] = useState(s.models?.clusters_enabled ?? true);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [proposal, setProposal] = useState<ModelProposalDTO | null>(null);
  const [catalog, setCatalog] = useState<ModelProposalDTO | null>(null);
  const [pickerTarget, setPickerTarget] = useState<{ cluster: ClusterName; tier: TierKey; index?: number } | null>(null);
  const [probes, setProbes] = useState<Record<string, ProbeResult | "…">>({});

  const ceiling = s.models?.tier1_ceiling ?? 1.25;
  const openrouterReady = Boolean(s.providers.find((p) => p.name === "openrouter")?.key.configured);
  const withKeys = s.providers.filter((p) => p.key.configured);
  const activeClusters: ClusterConfig[] = clustersEnabled ? [...CLUSTERS_CONFIG] : [GENERAL_CONFIG];

  useEffect(() => {
    setMatrix(s.matrix ?? {});
    setRoles(s.roles);
    setRoleTasks(Object.fromEntries((s.role_tasks ?? []).map((t) => [t.key, t.tier])));
    setClustersEnabled(s.models?.clusters_enabled ?? true);
  }, [s]);

  // The catalogue is read from the local cache; only the founder's button goes to the network.
  useEffect(() => {
    if (!openrouterReady) return;
    api.modelsCatalog(slug)
      .then((cat) => { if (cat && (cat.clusters || cat.all_models)) setCatalog(cat); })
      .catch(() => {});
  }, [slug, openrouterReady]);

  /** Everything the screen knows about a model id, from whichever list carried it. */
  const modelsMap = useMemo(() => {
    const map: Record<string, ModelPick> = {};
    for (const src of [catalog, proposal]) {
      if (!src) continue;
      const lists: ModelPick[][] = [src.all_models ?? [], ...Object.values(src.summary ?? {})];
      for (const cluster of Object.values(src.clusters ?? {})) {
        if (cluster) lists.push(...Object.values(cluster));
      }
      for (const list of lists) for (const m of list ?? []) map[m.id] = m;
    }
    return map;
  }, [catalog, proposal]);

  const cell = (cluster: string, tier: string): Candidate[] => matrix[cluster]?.[tier] ?? [];
  const writeCell = (cluster: string, tier: string, list: Candidate[]) =>
    setMatrix({ ...matrix, [cluster]: { ...(matrix[cluster] ?? {}), [tier]: list } });

  const setCandidate = (cluster: string, tier: string, i: number, patch: Partial<Candidate>) =>
    writeCell(cluster, tier, cell(cluster, tier).map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const moveCandidate = (cluster: string, tier: string, i: number, delta: number) => {
    const list = [...cell(cluster, tier)];
    const j = i + delta;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    writeCell(cluster, tier, list);
  };
  const removeCandidate = (cluster: string, tier: string, i: number) =>
    writeCell(cluster, tier, cell(cluster, tier).filter((_, j) => j !== i));
  const addCandidate = (cluster: string, tier: string, model: string, provider: string) => {
    const list = cell(cluster, tier);
    if (list.length >= MAX_MODELS_PER_TIER) return setSyncNote(`Limite de ${MAX_MODELS_PER_TIER} modelos por tier atingido.`);
    if (model && list.some((c) => c.model === model)) return setSyncNote(`O modelo ${model} já está neste tier.`);
    writeCell(cluster, tier, [...list, { provider, model }]);
  };

  const dirty =
    JSON.stringify(matrix) !== JSON.stringify(s.matrix ?? {}) ||
    JSON.stringify(roles) !== JSON.stringify(s.roles) ||
    JSON.stringify(roleTasks) !== JSON.stringify(Object.fromEntries((s.role_tasks ?? []).map((t) => [t.key, t.tier]))) ||
    clustersEnabled !== (s.models?.clusters_enabled ?? true);

  const runSync = async (forceRefresh: boolean) => {
    if (!openrouterReady) { setSyncError(NO_OPENROUTER_HINT); setSyncNote(null); return; }
    setSyncing(true); setSyncError(null); setSyncNote(null);
    try {
      const p = await api.previewModelSync(slug, { tier1_ceiling: ceiling, force_refresh: forceRefresh });
      setCatalog(p);
      if (forceRefresh) {
        const warned = p.price_warnings ?? [];
        setSyncNote(
          `✔ Lista de modelos atualizada: ${p.all_models?.length ?? 0} modelos no catálogo.` +
          (warned.length ? ` Aviso na Caixa de Entrada: ${warned.join(", ")} ficou mais caro.` : "")
        );
      } else {
        setProposal(p);
        if (!p.changed) setSyncNote("✔ Os modelos atuais já são a melhor escolha sob o teto definido.");
      }
    } catch (e) {
      setSyncError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  const testCell = async (cluster: string, tier: string, i: number, c: Candidate) => {
    const key = `${cluster}:${tier}:${i}`;
    setProbes((p) => ({ ...p, [key]: "…" }));
    try {
      const r = await api.testModel(slug, c.provider, c.model);
      setProbes((p) => ({ ...p, [key]: r }));
    } catch (e) {
      setProbes((p) => ({ ...p, [key]: { name: c.provider, ok: false, detail: String(e), model: c.model, latency_ms: 0 } }));
    }
  };

  const fillFromProposal = () => {
    if (!proposal?.clusters) return;
    const next = emptyMatrix(activeClusters.map((c) => c.key));
    for (const col of activeClusters) {
      for (const t of TIERS_CONFIG) {
        next[col.key][t.key] = (proposal.clusters[col.key as ClusterName]?.[t.key] ?? []).map((p) => ({
          provider: "openrouter",
          model: p.id,
        }));
      }
    }
    setMatrix({ ...matrix, ...next });
    setSyncNote("✔ Sugestão carregada no editor. Revise e clique em “Salvar modelos”.");
    setProposal(null);
  };

  const applyImmediately = async () => {
    setSyncing(true);
    try {
      await api.applyModelSync(slug, false, ceiling);
      setSyncNote("✔ Matriz oficial de modelos e tabela de preços atualizadas.");
      setProposal(null);
      onReload();
    } catch (e) { setSyncError(String(e)); } finally { setSyncing(false); }
  };

  const sendToInbox = async () => {
    setSyncing(true);
    try {
      const res = await api.applyModelSync(slug, true, ceiling);
      setSyncNote(res.message || "✔ Sugestão enviada à Caixa de Entrada para sua aprovação.");
      setProposal(null);
    } catch (e) { setSyncError(String(e)); } finally { setSyncing(false); }
  };

  const save = () =>
    onSave({
      matrix,
      roles,
      role_tasks: roleTasks,
      clusters_enabled: clustersEnabled,
    });

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------------ actions */}
      <div className="space-y-3 rounded-lg border border-line bg-slate-900/60 p-3">
        <div className="flex flex-col items-start justify-between gap-2 sm:flex-row sm:items-center">
          <div>
            <div className="font-semibold text-slate-100">Matriz de modelos</div>
            <p className="mt-0.5 text-xs text-slate-400">
              Cada Loompa busca no seu cluster o tier da tarefa. Se um modelo falhar ou faltar cota, cai para o próximo da lista.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={`flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold ${openrouterReady ? "btn-ghost" : "btn-ghost opacity-50"}`}
              disabled={syncing}
              onClick={() => runSync(true)}
              title="Baixa o catálogo da OpenRouter de novo e grava os modelos e preços no cache local"
            >
              <span aria-hidden="true">{syncing ? "⟳" : "📡"}</span>
              Atualizar lista de modelos OpenRouter
            </button>
            <button
              className={`flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold ${openrouterReady ? "btn-primary" : "btn-ghost opacity-50"}`}
              disabled={syncing}
              onClick={() => runSync(false)}
              title="Rankeia o catálogo por cluster e tier e propõe uma matriz sob o teto de custo"
            >
              <span aria-hidden="true">{syncing ? "⟳" : "✨"}</span>
              {syncing ? "Calculando…" : "Sugestão inteligente"}
            </button>
          </div>
        </div>

        {!openrouterReady && (
          <p className="rounded border border-amber-900/50 bg-amber-950/30 p-2 text-[11px] text-amber-200">
            {NO_OPENROUTER_HINT}
          </p>
        )}

        <label className="flex cursor-pointer items-start gap-2 border-t border-line/60 pt-2 text-xs">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={clustersEnabled}
            onChange={(e) => setClustersEnabled(e.target.checked)}
          />
          <span>
            <span className="font-medium text-slate-200">Separar modelos por cluster de agentes</span>
            <span className="mt-0.5 block text-slate-400">
              Ligado, cada grupo de Loompas tem a sua lista, pesada para o que ele faz. Desligado, todos usam um
              <strong className="text-slate-300"> cluster geral</strong> com os mesmos três tiers, ranqueado só pela nota de
              inteligência — a que existe para mais modelos, então nenhum bom modelo fica de fora por falta das outras notas.
            </span>
          </span>
        </label>
      </div>

      {syncError && <p className="rounded border border-red-900/50 bg-red-950/40 p-2 text-xs text-red-300">✘ {syncError}</p>}
      {syncNote && <p className="rounded border border-emerald-900/50 bg-emerald-950/40 p-2 text-xs text-emerald-300">{syncNote}</p>}

      {/* ------------------------------------------------------------ suggestion */}
      {proposal && (
        <div className="space-y-3 rounded-lg border border-brand/60 bg-slate-900/95 p-3.5 shadow-xl">
          <div className="flex flex-col items-start justify-between gap-2 border-b border-line pb-2.5 sm:flex-row sm:items-center">
            <div>
              <span className="flex items-center gap-1.5 text-sm font-semibold text-brand">
                <span aria-hidden="true">✨</span> Sugestão para a matriz de modelos
              </span>
              <span className="mt-0.5 block text-xs text-slate-400">
                {proposal.eligible} de {proposal.considered} modelos qualificados, só versões fixas.
                Teto do Tier 1: US$ {ceiling.toFixed(2)}/M (ajustável na aba Orçamento).
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn-primary px-3 py-1 text-xs font-semibold" onClick={applyImmediately}>✔ Aplicar agora</button>
              <button className="btn-ghost px-2.5 py-1 text-xs" onClick={fillFromProposal}>✏️ Carregar no editor</button>
              <button className="btn-ghost px-2.5 py-1 text-xs" onClick={sendToInbox}>📬 Enviar ao Inbox</button>
              <button className="btn-ghost px-2 py-1 text-xs text-slate-400 hover:text-white" onClick={() => setProposal(null)}>✕</button>
            </div>
          </div>
          <div className="space-y-1 rounded border border-line bg-ink/50 p-2.5 text-xs">
            {proposal.added.length > 0 && <div><span className="font-semibold text-emerald-400">Entram:</span> {proposal.added.join(", ")}</div>}
            {proposal.removed.length > 0 && <div><span className="font-semibold text-rose-400">Saem:</span> {proposal.removed.join(", ")}</div>}
            {proposal.repriced.length > 0 && <div><span className="font-semibold text-amber-400">Preços atualizados:</span> {proposal.repriced.join(", ")}</div>}
            {proposal.expiring.length > 0 && <div><span className="font-semibold text-orange-400">Descontinuados em breve:</span> {proposal.expiring.join(", ")}</div>}
            {!proposal.changed && <div className="text-slate-400">Os modelos configurados já são a melhor escolha sob o teto atual.</div>}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------ the matrix */}
      <div className={`grid grid-cols-1 gap-3 ${clustersEnabled ? "lg:grid-cols-3" : ""}`}>
        {activeClusters.map((col) => (
          <div key={col.key} className="space-y-2.5 rounded-lg border border-line/80 bg-ink/70 p-2.5 text-xs">
            <div className="space-y-1 border-b border-line/40 pb-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-100">
                <span className="text-sm" aria-hidden="true">{col.icon}</span>
                {col.label}
              </div>
              <div className="text-[10px] text-slate-400">Loompas: {col.roles.join(", ")}</div>
              {col.weights.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 pt-0.5 text-[10px]">
                  <span className="font-sans text-slate-500">Peso:</span>
                  {col.weights.map((w) => (
                    <span key={w.label} className={`rounded border px-1 font-mono text-[9px] ${w.color}`}>{w.pct} {w.label}</span>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2.5">
              {TIERS_CONFIG.map((t) => {
                const candidates = cell(col.key, t.key);
                return (
                  <div key={t.key} className="space-y-1.5 rounded-md border border-slate-700/60 bg-slate-900/60 p-2">
                    <div className="flex items-center justify-between">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <span className={`rounded border px-1.5 text-[10px] font-semibold ${t.badgeColor}`}>{t.label}</span>
                        <span className="truncate text-[10px] text-slate-400">{t.sublabel}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[9px] text-slate-500">{candidates.length}/{MAX_MODELS_PER_TIER}</span>
                        {candidates.length < MAX_MODELS_PER_TIER && (
                          <button
                            type="button"
                            className="btn-ghost px-1 py-0 text-[10px] text-brand hover:text-white"
                            onClick={() => addCandidate(col.key, t.key, "", withKeys[0]?.name ?? "openrouter")}
                          >
                            + modelo
                          </button>
                        )}
                      </div>
                    </div>

                    {candidates.length === 0 ? (
                      <div className="py-2 text-center text-[10px] italic text-slate-500">
                        Nenhum modelo.{" "}
                        <button type="button" className="text-brand hover:underline" onClick={() => addCandidate(col.key, t.key, "", withKeys[0]?.name ?? "openrouter")}>
                          + adicionar
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {candidates.map((c, i) => {
                          const info = modelsMap[c.model];
                          const provider = s.providers.find((p) => p.name === c.provider);
                          const overCeiling = t.key === "tier1" && info && info.price > ceiling;
                          const probeKey = `${col.key}:${t.key}:${i}`;
                          const probe = probes[probeKey];
                          return (
                            <div key={i} className="space-y-1 rounded border border-line/60 bg-slate-950/70 p-1.5">
                              <div className="flex min-w-0 items-center gap-1.5">
                                <span className="w-3 flex-shrink-0 font-mono text-[10px] text-slate-500">{i + 1}</span>
                                <ProviderSelect
                                  value={c.provider}
                                  providers={withKeys}
                                  onChange={(name) => setCandidate(col.key, t.key, i, { provider: name, model: "" })}
                                />
                                {c.provider === "openrouter" ? (
                                  <button
                                    type="button"
                                    className="flex min-w-0 flex-1 items-center justify-between rounded border border-line bg-ink px-1.5 py-0.5 text-left text-[11px] text-slate-200 transition-colors hover:border-brand/70"
                                    onClick={() => setPickerTarget({ cluster: col.key as ClusterName, tier: t.key, index: i })}
                                    title={info ? info.name : c.model || "Escolher um modelo"}
                                  >
                                    <span className="truncate font-mono font-medium text-cyan-300">
                                      {c.model
                                        ? info?.name ? cleanModelName(info.name) : cleanModelId(c.model)
                                        : <span className="italic text-slate-500">Escolher…</span>}
                                    </span>
                                    <span className="ml-1 flex-shrink-0 text-[9px] text-slate-400">▾</span>
                                  </button>
                                ) : (
                                  <input
                                    className="min-w-0 flex-1 rounded border border-line bg-ink px-1.5 py-0.5 font-mono text-[11px] text-slate-200"
                                    value={c.model}
                                    placeholder="id do modelo neste provedor"
                                    title={c.model}
                                    onChange={(e) => setCandidate(col.key, t.key, i, { model: e.target.value })}
                                  />
                                )}
                                <div className="flex flex-shrink-0 items-center gap-0.5">
                                  <button type="button" className="btn-ghost px-1 py-0 text-[10px]" title="Testar conexão com este modelo"
                                    disabled={!c.model || probe === "…"} onClick={() => testCell(col.key, t.key, i, c)}>
                                    {probe === "…" ? "⟳" : "▶"}
                                  </button>
                                  <button type="button" className="btn-ghost px-1 py-0 text-[10px] disabled:opacity-30" disabled={i === 0}
                                    title="Subir (prioridade maior)" onClick={() => moveCandidate(col.key, t.key, i, -1)}>↑</button>
                                  <button type="button" className="btn-ghost px-1 py-0 text-[10px] disabled:opacity-30" disabled={i === candidates.length - 1}
                                    title="Descer (usado como reserva)" onClick={() => moveCandidate(col.key, t.key, i, 1)}>↓</button>
                                  <button type="button" className="btn-ghost px-1 py-0 text-[10px] text-rose-400 hover:text-rose-200"
                                    title="Remover" onClick={() => removeCandidate(col.key, t.key, i)}>✕</button>
                                </div>
                              </div>

                              {c.provider !== "openrouter" && provider?.models_url && (
                                <a className="ml-4 block text-[10px] text-slate-500 hover:text-brand hover:underline" href={provider.models_url} target="_blank" rel="noreferrer">
                                  modelos disponíveis em {provider.label} ↗
                                </a>
                              )}

                              {info && (
                                <div className="ml-4 flex flex-wrap items-center gap-1.5 text-[10px]">
                                  <ClusterScore cluster={col.key as ClusterName} m={info} mode={t.key === "tier2" ? "value" : "score"} />
                                  <span className={`font-mono ${info.price === 0 ? "font-semibold text-emerald-400" : "text-slate-300"}`}>
                                    {priceLabel(info.price)}
                                  </span>
                                  {info.alias && (
                                    <span className="rounded border border-violet-700/60 bg-violet-950/50 px-1 text-violet-300" title="Apelido: troca de modelo sozinho quando o fabricante lança uma versão nova">
                                      apelido
                                    </span>
                                  )}
                                  {overCeiling && (
                                    <span className="rounded border border-amber-800 bg-amber-950/80 px-1 font-semibold text-amber-300"
                                      title={`Acima do teto do Tier 1 (US$ ${ceiling.toFixed(2)})`}>
                                      acima do teto
                                    </span>
                                  )}
                                </div>
                              )}

                              {probe && probe !== "…" && (
                                <div className={`ml-4 text-[10px] ${probe.ok ? "text-emerald-300" : "text-red-300"}`}>
                                  {probe.ok ? "✔" : "✘"} {probe.detail}{probe.latency_ms ? ` · ${probe.latency_ms} ms` : ""}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* ------------------------------------------------------------ roles */}
      <RolesPanel roles={roles} setRoles={setRoles} tasks={s.role_tasks ?? []} roleTasks={roleTasks} setRoleTasks={setRoleTasks} />

      <div className="text-right">
        <button className="btn-primary" disabled={!dirty} onClick={save}>Salvar modelos</button>
      </div>

      {pickerTarget && (
        <ModelPickerModal
          onClose={() => setPickerTarget(null)}
          onSelect={(modelId) => {
            if (pickerTarget.index !== undefined) {
              setCandidate(pickerTarget.cluster, pickerTarget.tier, pickerTarget.index, { model: modelId, provider: "openrouter" });
            } else {
              addCandidate(pickerTarget.cluster, pickerTarget.tier, modelId, "openrouter");
            }
            setPickerTarget(null);
          }}
          clusterTarget={pickerTarget.cluster}
          tierTarget={pickerTarget.tier}
          tier1Ceiling={ceiling}
          tier2Floor={s.models?.tier2_floor ?? 0.75}
          catalog={catalog}
          clusters={activeClusters}
        />
      )}
    </div>
  );
}

const TIER_OPTIONS = ["tier1", "tier2", "tier3"] as const;

/** One card per Loompa, alphabetical, carrying its default tier and the named calls that do not
 * use it. Only tasks whose profile really differs are declared, so a card stays short. */
function RolesPanel({
  roles,
  setRoles,
  tasks,
  roleTasks,
  setRoleTasks,
}: {
  roles: Record<string, string>;
  setRoles: (r: Record<string, string>) => void;
  tasks: RoleTaskInfo[];
  roleTasks: Record<string, string>;
  setRoleTasks: (r: Record<string, string>) => void;
}) {
  const listed = Object.entries(roles)
    .filter(([role]) => !BACKEND_SERVICES.has(role))
    .map(([role, tier]) => ({ role, tier, label: ROLE_DISPLAY[role] ?? role }))
    .sort((a, b) => a.label.localeCompare(b.label, "pt-BR"));

  return (
    <div className="rounded-md border border-line bg-slate-900/30 p-2.5">
      <div className="mb-2 text-xs font-semibold text-slate-200">
        Loompa → Tier padrão{" "}
        <span className="font-normal text-slate-400">
          — o tier que cada um usa nas tarefas do dia a dia (uma história complexa escala para o Tier 1 sozinha)
        </span>
      </div>
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {listed.map(({ role, tier, label }) => {
          const mine = tasks.filter((t) => t.role === role);
          return (
            <div key={role} className="space-y-1.5 rounded border border-line/40 bg-slate-900/60 p-1.5 text-xs">
              <label className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-slate-200">{label}</span>
                <select className={select} value={tier} onChange={(e) => setRoles({ ...roles, [role]: e.target.value })}>
                  {TIER_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              {mine.map((t) => (
                <label key={t.key} className="flex items-center justify-between gap-2 border-t border-line/30 pt-1.5 pl-2">
                  <span className="min-w-0">
                    <span className="block truncate text-[11px] text-slate-300">↳ {t.label}</span>
                    <span className="block truncate text-[10px] text-slate-500" title={t.hint}>{t.hint}</span>
                  </span>
                  <select
                    className={select}
                    value={roleTasks[t.key] ?? t.tier}
                    onChange={(e) => setRoleTasks({ ...roleTasks, [t.key]: e.target.value })}
                  >
                    {TIER_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------- budget

function BudgetPanel({ s, onSave }: { s: Settings; onSave: (p: SettingsPatch) => Promise<void> }) {
  const [b, setB] = useState(s.budget);
  const [par, setPar] = useState(s.schedule.max_parallel);
  const [ceiling, setCeiling] = useState(s.models?.tier1_ceiling ?? 1.25);

  useEffect(() => {
    setB(s.budget);
    setPar(s.schedule.max_parallel);
    setCeiling(s.models?.tier1_ceiling ?? 1.25);
  }, [s]);

  const periodWord = b.period === "weekly" ? "semana" : "mês";
  const quarter = Math.round((b.cap_usd / 4) * 100) / 100;

  return (
    <div className="max-w-3xl space-y-4">
      <div className="space-y-3 rounded-lg border border-line bg-slate-900/40 p-3">
        <div className="font-semibold text-slate-100">Teto de gastos</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs text-slate-400">
            Período
            <select className={`${select} mt-1 w-full`} value={b.period} onChange={(e) => setB({ ...b, period: e.target.value as BudgetPeriod })}>
              <option value="weekly">semanal (segunda a domingo)</option>
              <option value="monthly">mensal</option>
            </select>
          </label>
          <label className="block text-xs text-slate-400">
            Teto por {periodWord} (US$)
            <input type="number" min={0} step={1} className={`${input} mt-1`} value={b.cap_usd} onChange={(e) => setB({ ...b, cap_usd: Number(e.target.value) })} />
          </label>
        </div>
        <label className="block text-xs text-slate-400">
          Avisar ao atingir (fração do teto)
          <input type="number" min={0} max={1} step={0.05} className={`${input} mt-1`} value={b.warn_at_fraction} onChange={(e) => setB({ ...b, warn_at_fraction: Number(e.target.value) })} />
          <span className="mt-0.5 block text-[11px] text-slate-500">
            {Math.round(b.warn_at_fraction * 100)}% do teto (US$ {(b.cap_usd * b.warn_at_fraction).toFixed(2)}) manda um aviso para a Caixa de Entrada.
          </span>
        </label>
      </div>

      <div className="space-y-2 rounded-lg border border-line bg-slate-900/40 p-3">
        <div className="font-semibold text-slate-100">Se o teto estourar</div>
        {([
          { key: "pause", title: `Pausar a esteira até ${b.period === "weekly" ? "a próxima semana" : "o próximo mês"}`, note: "Nada em andamento é perdido: as histórias guardam o estado e voltam de onde pararam." },
          { key: "tier3", title: "Passar todos os Loompas para o Tier 3 (gratuito)", note: "A fábrica continua entregando, com modelos mais simples, sem custo adicional." },
        ] as const).map((o) => (
          <label key={o.key} className={`flex cursor-pointer gap-2 rounded-md border p-2 text-xs transition-colors ${b.on_exceed === o.key ? "border-brand/60 bg-brand/10" : "border-line/60 hover:border-line"}`}>
            <input type="radio" name="on_exceed" className="mt-0.5" checked={b.on_exceed === o.key} onChange={() => setB({ ...b, on_exceed: o.key })} />
            <span>
              <span className="font-medium text-slate-200">{o.title}</span>
              <span className="mt-0.5 block text-slate-400">{o.note}</span>
            </span>
          </label>
        ))}
      </div>

      <div className="space-y-2 rounded-lg border border-line bg-slate-900/40 p-3">
        <div className="font-semibold text-slate-100">Limite por modelo</div>
        <label className="block text-xs text-slate-400">
          Teto para recomendação de modelos (US$ por 1M de tokens)
          <input type="number" min={0} step={0.25} className={`${input} mt-1`} value={ceiling} onChange={(e) => setCeiling(Number(e.target.value))} />
        </label>
        <p className="text-[11px] text-slate-500">
          Usado pela <strong className="text-slate-400">sugestão inteligente</strong>: modelos cujo custo combinado (3 de entrada : 1 de saída) passa deste valor
          ficam fora do Tier 1. O padrão é um quarto do teto do período — hoje US$ {quarter.toFixed(2)}, cerca de 4M de tokens por {periodWord}.
          {Math.abs(ceiling - quarter) > 0.01 && (
            <button type="button" className="ml-1 text-brand hover:underline" onClick={() => setCeiling(quarter)}>usar US$ {quarter.toFixed(2)}</button>
          )}
        </p>
      </div>

      <label className="block text-xs text-slate-400">
        Histórias em paralelo
        <input type="number" min={1} max={32} className={`${input} mt-1`} value={par} onChange={(e) => setPar(Number(e.target.value))} />
      </label>

      <p className="text-[11px] text-slate-500">
        O custo exato é acompanhado no painel de cada provedor; aqui é uma estimativa por tokens.
      </p>
      <div className="text-right">
        <button className="btn-primary" onClick={() => onSave({ budget: b, max_parallel: par, tier1_ceiling: ceiling })}>
          Salvar orçamento
        </button>
      </div>
    </div>
  );
}
