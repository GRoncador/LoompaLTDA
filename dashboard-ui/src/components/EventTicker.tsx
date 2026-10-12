import LoompaFigure from "./LoompaFigure";
import type { Agent, LoompaEvent } from "../types";
import { Icon, type IconName } from "./Icon";

const I = (name: IconName) => <Icon name={name} className="h-3 w-3" />;
const LABEL: Record<string, React.ReactNode> = {
  "story.stage": "→", "inbox.new": I("inbox"), "llm.call": I("brain"), "story.escalated": I("arrowUp"), "story.retry": I("refresh"), "tool.call": I("wrench"),
  "worktree.commit": I("check"), "story.merged": I("rocket"), "kaizen.learning": I("lightbulb"), "agent.state": I("user"), "engine.started": I("play"), "engine.stopped": I("stop"), "engine.pausing": I("pause"), "engine.paused": I("pause"), "engine.resumed": I("play"), "story.paused": I("pause"), "scheduler.paused": I("ban"),
};

export default function EventTicker({ events, agents = [], factory = "" }: { events: LoompaEvent[]; agents?: Agent[]; factory?: string }) {
  const last = events.slice(-6).reverse();
  // the Loompa who spoke, drawn like everywhere else, instead of one generic figure for all
  const roleOf = new Map(agents.map((a) => [a.name, a.role]));
  return (
    <footer className="flex h-7 items-center gap-4 overflow-hidden border-t border-line bg-panel px-3 text-[11px] text-slate-400">
      {last.length === 0 && <span>aguardando eventos da fábrica…</span>}
      {last.map((e, i) => (
        <span key={`${e.id ?? i}-${i}`} className="flex items-center gap-1 whitespace-nowrap">
          {e.agent && roleOf.has(e.agent) ? <LoompaFigure role={roleOf.get(e.agent)!} /> : <span>{LABEL[e.type] ?? "•"}</span>}
          {e.agent ? e.agent.replace(/ Loompa$/, "") : ""}
          {e.story_id && <b className="font-semibold text-slate-300">{e.story_id}</b>}
          <span>: {summarize(e, factory)}</span>
        </span>
      ))}
    </footer>
  );
}

function summarize(e: LoompaEvent, factory = ""): string {
  const p = e.payload ?? {};
  switch (e.type) {
    // the greeting of a fresh connection says which factory you are looking at
    case "hello": return `hello ${factory || e.factory || ""}`.trim();
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
