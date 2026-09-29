import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer, Row } from "./AgentDrawer";

type Tab = "spec" | "plan" | "tasks" | "research" | "diff" | "log";
type Diff = { source: "branch" | "merged" | "none"; ref: string; stat: string; diff: string };

export default function StoryDrawer({ slug, id, onClose }: { slug: string; id: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("tasks");
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
  const tabs: Tab[] = st.kind === "research" ? ["research", "log"] : ["tasks", "spec", "plan", "diff", "log"];
  return (
    <Drawer title={`${s.id} · ${s.title}`} onClose={onClose}>
      <div className="space-y-2 text-sm">
        <Row k="Etapa" v={<span className="chip bg-slate-800 text-slate-200">{s.stage}</span>} />
        <Row k="Custo" v={`US$ ${Number(data.usage?.cost_usd ?? 0).toFixed(4)} · ${data.usage?.calls ?? 0} consultas`} />
        <Row k="Tentativas" v={`tier 2: ${s.attempts.tier2} · tier 1: ${s.attempts.tier1} · atual: ${s.current_tier}`} />
        {s.branch && <Row k="Branch" v={<code className="text-xs">{s.branch}</code>} />}
        {s.pr_url && <Row k="Pull request" v={<a className="text-brand hover:underline" href={s.pr_url} target="_blank" rel="noreferrer">{s.pr_url}</a>} />}
        {st.founder_notes?.length > 0 && <Row k="Suas orientações" v={<ul className="list-disc pl-4">{st.founder_notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>} />}
        {st.delivery_summary && <Row k="Entrega" v={st.delivery_summary} />}
      </div>
      <div className="mt-4 flex gap-1 border-b border-line text-xs">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 ${tab === t ? "border-b-2 border-brand text-brand" : "text-slate-400"}`}>{t === "log" ? "histórico" : t === "diff" ? "mudanças" : t + ".md"}</button>
        ))}
      </div>
      <div className="scroll-thin mt-3 max-h-[50vh] overflow-y-auto">
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
