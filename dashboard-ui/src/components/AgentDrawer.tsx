import { useEffect, useState } from "react";
import { api } from "../api";

export default function AgentDrawer({ slug, name, onClose, onOpenStory }: { slug: string; name: string; onClose: () => void; onOpenStory: (id: string) => void }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => { api.agent(slug, name).then(setData).catch(() => setData(null)); const t = setInterval(() => api.agent(slug, name).then(setData).catch(() => {}), 5000); return () => clearInterval(t); }, [slug, name]);
  return (
    <Drawer title={name} onClose={onClose}>
      {!data ? <p className="text-sm text-slate-400">carregando…</p> : (
        <div className="space-y-3 text-sm">
          <Row k="Papel" v={data.role} />
          <Row k="Estado" v={<span className={`chip ${data.state === "BLOCKED" ? "bg-red-900/60 text-red-200" : data.state === "WORKING" ? "bg-emerald-900/60 text-emerald-200" : data.state === "TESTING" ? "bg-sky-900/60 text-sky-200" : "bg-slate-800 text-slate-300"}`}>{data.state}</span>} />
          <Row k="Modelo ativo" v={data.model || "—"} />
          <Row k="Atividade" v={data.detail || "—"} />
          {data.story && <Row k="História" v={<button className="text-brand hover:underline" onClick={() => onOpenStory(data.story.id)}>{data.story.id} · {data.story.title}</button>} />}
          {data.worktree && <Row k="Worktree" v={<code className="text-xs text-slate-400">{data.worktree}</code>} />}
          <div className="rounded-md border border-line bg-ink/60 p-3">
            <div className="mb-1 text-xs font-semibold text-slate-400">Telemetria (Finance Loompa)</div>
            <Usage label="Hoje" u={data.today} />
            <Usage label="Mês" u={data.month} />
          </div>
        </div>
      )}
    </Drawer>
  );
}

function Usage({ label, u }: { label: string; u: any }) {
  if (!u) return <div className="text-xs text-slate-500">{label}: sem consultas</div>;
  return <div className="text-xs">{label}: <b>US$ {Number(u.cost_usd).toFixed(4)}</b> · {u.calls} consultas · {u.input_tokens} in / {u.output_tokens} out tokens</div>;
}

export function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex gap-3"><span className="w-28 shrink-0 text-slate-500">{k}</span><span className="min-w-0 flex-1 break-words">{v}</span></div>;
}

export function Drawer({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/40" onClick={onClose}>
      <aside className="scroll-thin h-full w-full max-w-lg overflow-y-auto border-l border-line bg-panel p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold">{title}</h3>
          <button className="text-slate-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        {children}
      </aside>
    </div>
  );
}
