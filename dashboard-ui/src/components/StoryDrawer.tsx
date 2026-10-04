import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer, Row } from "./AgentDrawer";
import LoompaFigure, { ROLE_NAME } from "./LoompaFigure";
import Markdown from "./Markdown";
import AgentTrace from "./AgentTrace";

type Tab = "spec" | "plan" | "tasks" | "research" | "diff" | "log" | "agent";
type Diff = { source: "branch" | "merged" | "none"; ref: string; stat: string; diff: string };

export default function StoryDrawer({ slug, id, onClose, onOpenSprint }: { slug: string; id: string; onClose: () => void; onOpenSprint: (id: string) => void }) {
  const [data, setData] = useState<any>(null);
  // the order Spec Kit writes them in: what, then how, then the steps (plan 11.2)
  const [tab, setTab] = useState<Tab>("spec");
  const [diff, setDiff] = useState<Diff | null>(null);
  useEffect(() => {
    api.story(slug, id)
      .then((d) => { setData(d); if (d?.state?.kind === "research") setTab("research"); })
      .catch(() => setData(null));
  }, [slug, id]);
  useEffect(() => {
    if (tab === "diff" && !diff) api.storyDiff(slug, id).then(setDiff).catch(() => setDiff({ source: "none", ref: "", stat: "", diff: "" }));
  }, [tab, diff, slug, id]);
  if (!data) return <Drawer title={id} onClose={onClose}><p className="text-sm text-slate-400">carregando…</p></Drawer>;
  const s = data.story, st = data.state;
  // a research story ends in a report, not in code: no spec/plan/tasks to browse
  const tabs: Tab[] = st.kind === "research" ? ["research", "log", "agent"] : ["spec", "plan", "tasks", "diff", "log", "agent"];
  // how the Product Owner read the request that became this card (ADR-0017)
  const triage = st.extra?.po_triage ?? null;
  const act = data.activity ?? null;
  const cap = Number(data.story_cap_usd ?? 0);
  const overCap = cap > 0 && Number(data.usage?.cost_usd ?? 0) >= cap;
  const blockers: string[] = s.waiting ? [...new Set([...(s.waiting.gone ?? []), ...(s.waiting.pending ?? []), ...(s.waiting.chain ?? [])])] : (s.depends_on ?? []);
  const notes: string[] = st.founder_notes ?? [];
  const original: string = triage?.original ?? "";
  return (
    <Drawer title={`${s.id} · ${s.title}`} onClose={onClose} fill>
      {/* header, details and tabs stay put; only the active tab's content scrolls */}
      <div className="scroll-thin max-h-[40vh] shrink-0 space-y-2 overflow-y-auto text-sm">
        <Row k="Etapa" v={
          <span className="flex flex-wrap items-center gap-1">
            <span className="chip bg-slate-800 text-slate-200">{s.stage}</span>
            {act?.stage_since && <span className="text-slate-400">há {ago(act.stage_since)}</span>}
            {act?.detail && <span className="text-slate-400">· {act.detail}</span>}
          </span>
        } />
        <Row k="Classificação" v={`${KIND_LABEL[st.kind] ?? st.kind} · ${COMPLEXITY_LABEL[st.complexity] ?? st.complexity ?? "padrão"}${s.epic ? ` · ${s.epic}` : ""}`} />
        {s.tasks_total > 0 && <Row k="Tarefas do plano" v={
          <span className="flex items-center gap-2">
            <span className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-800">
              <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, (100 * s.tasks_done) / s.tasks_total)}%` }} />
            </span>
            <span className="tabular-nums text-slate-300">{s.tasks_done}/{s.tasks_total} concluídas</span>
          </span>
        } />}
        {data.sprints?.length > 0 && <Row k="Sprints" v={<span className="flex flex-wrap gap-1">{data.sprints.map((sp: string) => <button key={sp} className="chip bg-sky-900/50 text-sky-200 hover:bg-sky-800/60" title="abrir na aba Sprints" onClick={() => onOpenSprint(sp)}>{sp}</button>)}</span>} />}
        <Row k="Custo" v={
          <span className={overCap ? "text-amber-400" : ""}>
            US$ {Number(data.usage?.cost_usd ?? 0).toFixed(4)} · {data.usage?.calls ?? 0} consultas
            {cap > 0 ? ` · teto por história US$ ${cap.toFixed(2)}${overCap ? " (atingido)" : ""}` : ""}
          </span>
        } />
        <Row k="Tentativas" v={`${s.attempts.tier2 + s.attempts.tier1 + 1}ª · tier 2: ${s.attempts.tier2} · tier 1: ${s.attempts.tier1} · agora no ${s.current_tier === "tier1" ? "tier 1 (modelos mais fortes)" : s.current_tier === "tier3" ? "tier 3 (gratuitos)" : "tier 2"}`} />
        {act?.model && <Row k="Modelo atual" v={<code className="text-xs">{act.model}</code>} />}
        {blockers.length > 0 && <Row k="Bloqueada por" v={<span className="text-red-300">🔒 {blockers.join(", ")}{s.waiting?.label ? ` · ${s.waiting.label}` : ""}</span>} />}
        {s.blocked_reason && <Row k="Esperando você" v={<span className="text-amber-200">{BLOCKED_LABEL[s.blocked_reason] ?? s.blocked_reason}</span>} />}
        {st.qa_verdict && <Row k="Veredito do Inspector" v={
          <span className={st.qa_verdict === "FAIL" ? "text-red-300" : st.qa_verdict === "CONCERNS" ? "text-orange-200" : "text-slate-200"}>
            {QA_LABEL[st.qa_verdict] ?? st.qa_verdict}
            {(st.qa_findings ?? []).length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-slate-400">
                {(st.qa_findings ?? []).map((f: any, i: number) => <li key={i}>{f.id ? `${f.id} · ` : ""}{f.severity ? `${f.severity}: ` : ""}{f.text}</li>)}
              </ul>
            )}
          </span>
        } />}
        {s.branch && <Row k="Branch" v={<code className="text-xs">{s.branch}</code>} />}
        {s.pr_url && <Row k="Pull request" v={<a className="text-brand hover:underline" href={s.pr_url} target="_blank" rel="noreferrer">{s.pr_url}</a>} />}
        {s.priority_pinned && s.stage === "BACKLOG" && <Row k="Prioridade" v="📌 fixada por você: o Product Owner não move este card" />}
        {notes.length > 0 && <Row k="Suas orientações" v={<ul className="list-disc pl-4">{notes.map((n: string, i: number) => <li key={i} className="whitespace-pre-wrap">{n}</li>)}</ul>} />}
        {triage?.rewritten && original && !notes.includes(original) && <Row k="Texto original" v={<span className="whitespace-pre-wrap text-slate-400">{original}</span>} />}
        {triage?.forced && <Row k="Objeção do PO" v={<span className="text-amber-200">Gravado por decisão sua. {triage.objection}</span>} />}
        {triage && !triage.forced && triage.reason && <Row k="Nota do PO" v={<span className="text-slate-400">{triage.reason}</span>} />}
        {st.delivery_summary && <Row k="Entrega" v={st.delivery_summary} />}
      </div>
      <div className="mt-4 flex shrink-0 gap-1 border-b border-line text-xs">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)} title={TAB_HINT[t]} className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-brand text-brand" : "text-slate-400"}`}>{t === "log" ? "histórico" : t === "diff" ? "mudanças" : t === "agent" ? "bastidores" : t + ".md"}</button>
        ))}
      </div>
      <div className="scroll-always mt-3 min-h-0 flex-1 pr-1">
        {tab === "diff" ? (
          <DiffView diff={diff} />
        ) : tab === "log" ? (
          <History history={data.history ?? []} commits={data.commits ?? []} />
        ) : tab === "agent" ? (
          <AgentTrace slug={slug} id={id} live={["SPEC", "PLAN", "DEV", "TEST", "REVIEW"].includes(s.stage)} />
        ) : (
          <Markdown text={data.docs?.[tab] ?? ""} />
        )}
      </div>
    </Drawer>
  );
}

const TAB_HINT: Record<string, string> = {
  spec: "O que a história precisa entregar", plan: "Como o Architect decidiu fazer",
  tasks: "A lista de tarefas e o que já foi feito", research: "O relatório da pesquisa",
  diff: "O código que esta história mudou", log: "O que aconteceu em cada etapa",
  agent: "Bastidores: cada consulta ao modelo e cada ferramenta usada, com o que foi enviado e recebido",
};

const KIND_LABEL: Record<string, string> = { feature: "funcionalidade", bugfix: "correção", research: "pesquisa" };
const COMPLEXITY_LABEL: Record<string, string> = { SIMPLE: "simples", STANDARD: "padrão", COMPLEX: "complexa" };
const BLOCKED_LABEL: Record<string, string> = { delivery: "revisar a entrega", question: "responder uma dúvida", waiver: "decidir sobre um risco", dependency: "uma dependência saiu", cost_cap: "liberar mais custo", persistent_failure: "decidir depois de três tentativas", conflict: "resolver um conflito", stack: "decidir sobre a stack" };
const QA_LABEL: Record<string, string> = { PASS: "aprovada", CONCERNS: "aprovada com ressalvas", FAIL: "reprovada", WAIVED: "achado sério: decisão sua" };

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

type Step = { id: number; at: string; node: string; stage: string; role: string; summary: string };

/** What happened at each step, said by who did it (11.3), newest first; then the saved versions,
 * also newest first: the founder opens this tab to see what just happened (11.4). */
function History({ history, commits }: { history: Step[]; commits: string[] }) {
  const steps = [...history].reverse();
  const saved = [...commits].reverse();
  const day = (iso: string) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  const time = (iso: string) => new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="space-y-3 text-xs">
      {steps.length === 0 ? <p className="text-slate-400">Nada aconteceu ainda.</p> : (
        <ol className="space-y-1.5">
          {steps.map((h, i) => (
            <li key={h.id} className="flex gap-2">
              <span className="w-16 shrink-0 text-right tabular-nums text-slate-500" title={new Date(h.at).toLocaleString("pt-BR")}>
                {i === 0 || day(steps[i - 1].at) !== day(h.at) ? `${day(h.at)} ` : ""}{time(h.at)}
              </span>
              <LoompaFigure role={h.role} />
              <span className="min-w-0">
                {h.role !== "founder" && <span className="text-slate-400">{ROLE_NAME[h.role] ?? h.role}: </span>}
                <span className="text-slate-200">{h.summary}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {saved.length > 0 && (
        <div>
          <div className="mb-1 text-slate-500">Versões salvas (mais recente primeiro)</div>
          <ul className="space-y-0.5 text-slate-300">{saved.map((c, i) => <li key={i}>✅ {c}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

/** The story's changes, coloured like a terminal diff: what it adds, removes and where. */
function DiffView({ diff }: { diff: Diff | null }) {
  if (!diff) return <p className="text-xs text-slate-400">carregando…</p>;
  if (diff.source === "none" || !diff.diff) return <p className="text-xs text-slate-400">Nenhuma mudança de código ainda.</p>;
  const tone = (l: string) =>
    l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff --git") ? "text-slate-200 font-semibold"
      : l.startsWith("+") ? "bg-emerald-950/60 text-emerald-200"
      : l.startsWith("-") ? "bg-red-950/60 text-red-200"
      : l.startsWith("@@") ? "text-sky-300"
      : "text-slate-400";
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-slate-500">{diff.source === "merged" ? `integrada na versão principal (${diff.ref})` : `em andamento no branch ${diff.ref}`}</p>
      {diff.stat && <pre className="whitespace-pre-wrap font-mono text-[11px] text-slate-400">{diff.stat}</pre>}
      <pre className="overflow-x-auto font-mono text-[11px] leading-snug">
        {diff.diff.split("\n").map((l, i) => <div key={i} className={`whitespace-pre px-1 ${tone(l)}`}>{l || " "}</div>)}
      </pre>
    </div>
  );
}
