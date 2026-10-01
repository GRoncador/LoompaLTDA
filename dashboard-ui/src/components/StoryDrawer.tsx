import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer, Row } from "./AgentDrawer";

type Tab = "spec" | "plan" | "tasks" | "research" | "diff" | "log";
type Diff = { source: "branch" | "merged" | "none"; ref: string; stat: string; diff: string };

export default function StoryDrawer({ slug, id, onClose }: { slug: string; id: string; onClose: () => void }) {
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
  const tabs: Tab[] = st.kind === "research" ? ["research", "log"] : ["spec", "plan", "tasks", "diff", "log"];
  // how the Product Owner read the request that became this card (ADR-0017)
  const triage = st.extra?.po_triage ?? null;
  const notes: string[] = st.founder_notes ?? [];
  const original: string = triage?.original ?? "";
  return (
    <Drawer title={`${s.id} · ${s.title}`} onClose={onClose} fill>
      {/* header, details and tabs stay put; only the active tab's content scrolls */}
      <div className="scroll-thin max-h-[40vh] shrink-0 space-y-2 overflow-y-auto text-sm">
        <Row k="Etapa" v={<span className="chip bg-slate-800 text-slate-200">{s.stage}</span>} />
        <Row k="Custo" v={`US$ ${Number(data.usage?.cost_usd ?? 0).toFixed(4)} · ${data.usage?.calls ?? 0} consultas`} />
        <Row k="Tentativas" v={`tier 2: ${s.attempts.tier2} · tier 1: ${s.attempts.tier1} · atual: ${s.current_tier}`} />
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
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-brand text-brand" : "text-slate-400"}`}>{t === "log" ? "histórico" : t === "diff" ? "mudanças" : t + ".md"}</button>
        ))}
      </div>
      <div className="scroll-always mt-3 min-h-0 flex-1 pr-1">
        {tab === "diff" ? (
          <DiffView diff={diff} />
        ) : tab === "log" ? (
          <ul className="space-y-1 text-xs text-slate-300">
            {data.commits?.map((c: string, i: number) => <li key={`c${i}`}>✅ {c}</li>)}
            {data.checkpoints?.map((c: any) => <li key={c.id} className="text-slate-500">{c.created_at.slice(11, 19)} · {c.node} → {c.stage}</li>)}
          </ul>
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-xs text-slate-300">{data.docs?.[tab] ?? "(ainda não gerado)"}</pre>
        )}
      </div>
    </Drawer>
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
