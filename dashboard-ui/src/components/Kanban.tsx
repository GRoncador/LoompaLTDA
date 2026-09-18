import { useState } from "react";
import type { Column, StoryCard } from "../types";

const TONE: Record<string, string> = {
  BACKLOG: "border-slate-600", SPEC: "border-violet-500", DEV: "border-emerald-500", TEST: "border-sky-500", AWAITING_FOUNDER: "border-amber-500", DONE: "border-slate-500",
};

export default function Kanban({ columns, onOpen, onPromote, onCreate }: { columns: Column[]; onOpen: (id: string) => void; onPromote: (id: string) => void; onCreate: (title: string) => Promise<void> }) {
  const [title, setTitle] = useState("");
  return (
    <>
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <h2 className="text-sm font-semibold">📋 Kanban de Fluxo de Valor</h2>
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (title.trim()) { await onCreate(title.trim()); setTitle(""); } }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Nova história rápida…" className="w-64 rounded-md border border-line bg-ink px-2 py-1 text-xs" />
          <button className="btn-ghost text-xs" type="submit">+ Backlog</button>
        </form>
      </div>
      <div className="scroll-thin grid flex-1 grid-cols-6 gap-2 overflow-x-auto p-3">
        {columns.map((c) => (
          <div key={c.key} className={`flex min-w-[150px] flex-col rounded-md border-t-4 bg-ink/50 ${TONE[c.key] ?? ""}`}>
            <div className="flex items-center justify-between px-2 py-1 text-xs font-semibold text-slate-300">
              <span>{c.label}</span><span className="text-slate-500">{c.stories.length}</span>
            </div>
            <div className="scroll-thin flex-1 space-y-1.5 overflow-y-auto px-2 pb-2">
              {c.stories.map((s) => <Card key={s.id} s={s} onOpen={onOpen} onPromote={onPromote} />)}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Card({ s, onOpen, onPromote }: { s: StoryCard; onOpen: (id: string) => void; onPromote: (id: string) => void }) {
  const kaizen = s.origin === "kaizen" && s.stage === "BACKLOG";
  return (
    <div className="rounded-md border border-line bg-panel p-2 text-xs hover:border-slate-500">
      <button className="w-full text-left" onClick={() => onOpen(s.id)}>
        <div className="flex items-center justify-between text-[10px] text-slate-500">
          <span>{s.id}</span>
          <span>{s.cost_usd > 0 ? `$${s.cost_usd.toFixed(2)}` : ""}</span>
        </div>
        <div className="mt-0.5 font-medium leading-snug text-slate-100">{s.title}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          {s.epic && <span className="chip bg-slate-800 text-slate-400">{s.epic}</span>}
          {s.kind && s.kind !== "feature" && <span className="chip bg-fuchsia-900/50 text-fuchsia-200">{s.kind}</span>}
          {s.complexity && s.complexity !== "STANDARD" && <span className="chip bg-slate-800 text-slate-400">{s.complexity.toLowerCase()}</span>}
          {s.phase && s.stage !== "AWAITING_FOUNDER" && s.stage !== "DONE" && <span className="chip bg-slate-800 text-slate-400" title={s.route.join(" → ")}>{s.phase.replace("_", " ")}</span>}
          {s.current_tier === "tier1" && <span className="chip bg-blue-900/60 text-blue-200">tier 1</span>}
          {s.qa_verdict === "CONCERNS" && <span className="chip bg-orange-900/50 text-orange-200">ressalvas</span>}
          {s.blocked_reason && <span className="chip bg-amber-900/60 text-amber-200">{s.blocked_reason === "delivery" ? "revisar" : s.blocked_reason === "question" ? "dúvida" : s.blocked_reason === "waiver" ? "risco" : "bloqueada"}</span>}
          {s.tasks_total > 0 && <span className="chip bg-slate-800 text-slate-400">{s.tasks_done}/{s.tasks_total}</span>}
        </div>
      </button>
      {kaizen && <button className="mt-1 w-full rounded bg-lime-900/50 py-0.5 text-[10px] text-lime-200 hover:bg-lime-800/60" onClick={() => onPromote(s.id)}>💡 aprovar para execução</button>}
    </div>
  );
}
