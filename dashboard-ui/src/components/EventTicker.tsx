import type { LoompaEvent } from "../types";

const LABEL: Record<string, string> = {
  "story.stage": "→", "inbox.new": "📬", "llm.call": "🧠", "story.escalated": "⬆️", "story.retry": "🔁", "tool.call": "🔧",
  "worktree.commit": "✅", "story.merged": "🚀", "kaizen.learning": "💡", "agent.state": "👤", "engine.started": "▶", "engine.stopped": "⏸", "scheduler.paused": "⛔",
};

export default function EventTicker({ events }: { events: LoompaEvent[] }) {
  const last = events.slice(-6).reverse();
  return (
    <footer className="flex h-7 items-center gap-4 overflow-hidden border-t border-line bg-panel px-3 text-[11px] text-slate-400">
      {last.length === 0 && <span>aguardando eventos da fábrica…</span>}
      {last.map((e, i) => (
        <span key={`${e.id ?? i}-${i}`} className="whitespace-nowrap">
          {LABEL[e.type] ?? "•"} {e.story_id ?? ""} {e.agent ? `${e.agent}:` : ""} {summarize(e)}
        </span>
      ))}
    </footer>
  );
}

function summarize(e: LoompaEvent): string {
  const p = e.payload ?? {};
  switch (e.type) {
    case "story.stage": return String(p.stage ?? "");
    case "inbox.new": return String(p.title ?? "").slice(0, 60);
    case "llm.call": return `${p.model} $${Number(p.cost_usd ?? 0).toFixed(4)}`;
    case "tool.call": return String(p.tool ?? "");
    case "worktree.commit": return `commit ${p.sha}`;
    case "agent.state": return `${p.state}${p.detail ? ` · ${String(p.detail).slice(0, 40)}` : ""}`;
    case "kaizen.learning": return String(p.title ?? "").slice(0, 50);
    default: return e.type;
  }
}
