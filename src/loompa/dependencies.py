"""Dependencies between stories and the gate before `plan` (ADR-0021, plan 10.7 and 10.9).

A story's `depends_on` (a column of `stories`, written only by the Product Owner) names the cards
it needs delivered first. Stories linked that way form a *chain*. Nothing here writes: these are
the readings the Product Owner, the Scheduler, the engine and the panel share.

The gate decides, before a chain story plans, whether it may: every dependency delivered and every
spec of the chain approved (open); something still missing (wait: the story parks and frees its
slot, nobody is asked); or a dependency cancelled or sent back to the backlog (gone: the founder
decides). Waiting is time, not a decision, so it never reaches the inbox.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loompa.engine.state import TERMINAL, Stage

WAIT_KEY = "waiting"  # state.extra: parked at the gate before `plan`, with what it waits for
LIVE = (Stage.SPEC, Stage.PLAN, Stage.DEV, Stage.TEST, Stage.REVIEW, Stage.AWAITING_FOUNDER)


def deps_of(row: Mapping[str, Any] | None) -> list[str]:
    raw = (row or {}).get("depends_on") or []
    return [str(x) for x in raw] if isinstance(raw, list) else []


def find_cycle(edges: Mapping[str, Iterable[str]], start: str | None = None) -> list[str] | None:
    """A cycle in `edges` (node → the nodes it depends on), as the path that closes it
    (`[A, B, A]`), or None. With `start`, only cycles reachable from it."""
    graph = {k: list(v) for k, v in edges.items()}
    color: dict[str, int] = {}  # 1 = on the current path, 2 = done
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = 1
        path.append(node)
        for nxt in graph.get(node, []):
            if color.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            if color.get(nxt) is None and (found := visit(nxt)):
                return found
        path.pop()
        color[node] = 2
        return None

    for node in [start] if start is not None else list(graph):
        if color.get(node) is None and (found := visit(node)):
            return found
    return None


def would_cycle(
    edges: Mapping[str, Iterable[str]], node: str, deps: Iterable[str]
) -> list[str] | None:
    """The cycle `node` → `deps` would close, or None."""
    trial = {k: list(v) for k, v in edges.items()}
    trial[node] = list(deps)
    return find_cycle(trial, node)


def edges_of(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    return {r["id"]: deps_of(r) for r in rows}


# ------------------------------------------------------------------------------ the gate


def active(row: Mapping[str, Any]) -> bool:
    """Admitted and unfinished: the stories a chain is made of."""
    return row["stage"] in LIVE


def spec_passed(row: Mapping[str, Any]) -> bool:
    """The story's spec is approved: it is past `spec_review` (or `spec`, when its route has no
    review). A story with no `plan` in its route (research) never blocks a chain's specs."""
    if row["stage"] in TERMINAL:
        return True
    st = row.get("state") or {}
    route = list(st.get("route") or [])
    if not route:
        return False  # not classified yet
    if "plan" not in route:
        return True
    phase = st.get("phase") or ""
    if row["stage"] == Stage.AWAITING_FOUNDER:
        phase = st.get("resume_phase") or phase
    last = "spec_review" if "spec_review" in route else "spec"
    if phase in route:
        return route.index(phase) > route.index(last)
    return row["stage"] in (Stage.PLAN, Stage.DEV, Stage.TEST, Stage.REVIEW)


def chain_of(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> list[str]:
    """Every active story linked to `story_id` by dependencies, either way, itself included."""
    links: dict[str, set[str]] = {}
    for sid, row in rows.items():
        if not active(row):
            continue
        for dep in deps_of(row):
            if dep in rows and active(rows[dep]):
                links.setdefault(sid, set()).add(dep)
                links.setdefault(dep, set()).add(sid)
    seen, todo = {story_id}, [story_id]
    while todo:
        for nxt in links.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return sorted(seen)


def dependents(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> list[str]:
    """Unfinished stories that depend on `story_id`."""
    return sorted(
        sid
        for sid, row in rows.items()
        if row["stage"] not in TERMINAL and story_id in deps_of(row)
    )


class Gate(StrEnum):
    OPEN = "open"
    WAIT = "wait"
    GONE = "gone"


@dataclass
class GateResult:
    gate: Gate = Gate.OPEN
    pending: list[str] = field(default_factory=list)  # dependencies not delivered yet
    founder: list[str] = field(default_factory=list)  # ...of those, the ones waiting on the founder
    gone: list[str] = field(default_factory=list)  # cancelled, or back in the backlog
    chain: list[str] = field(default_factory=list)  # chain stories whose spec is not approved

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "pending": self.pending,
            "founder": self.founder,
            "gone": self.gone,
            "chain": self.chain,
            "label": self.label(),
        }

    def label(self) -> str:
        """The card's chip, in the founder's language."""
        if self.gone:
            return f"{', '.join(self.gone)} saiu do sprint: decisão sua"
        if self.founder:
            return f"aguardando {', '.join(self.pending)}, que espera você"
        if self.pending:
            return f"spec aprovada · aguardando {', '.join(self.pending)}"
        if self.chain:
            return f"spec aprovada · aguardando as specs de {', '.join(self.chain)}"
        return ""


def plan_gate(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> GateResult:
    """May `story_id` plan now? See the module docstring."""
    me = rows.get(story_id)
    if me is None:
        return GateResult()
    out = GateResult()
    for dep in deps_of(me):
        row = rows.get(dep)
        if row is None or row["stage"] in (Stage.CANCELLED, Stage.BACKLOG):
            out.gone.append(dep)
        elif row["stage"] != Stage.DONE:
            out.pending.append(dep)
            if row["stage"] == Stage.AWAITING_FOUNDER:
                out.founder.append(dep)
    out.chain = [
        sid for sid in chain_of(rows, story_id) if sid != story_id and not spec_passed(rows[sid])
    ]
    if out.gone:
        out.gate = Gate.GONE
    elif out.pending or out.chain:
        out.gate = Gate.WAIT
    return out


def rows_by_id(store: Any, slug: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in store.list_stories(slug)}


def in_chain(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> bool:
    return len(chain_of(rows, story_id)) > 1


def missing_from(
    rows: Mapping[str, Mapping[str, Any]],
    picked: Iterable[str],
    extra: Mapping[str, list[str]] | None = None,
) -> list[tuple[str, str]]:
    """`(story, dependency)` pairs that break the assembly rule: a dependency of a picked story
    that is neither picked nor delivered. `extra` adds relations not saved yet (a draft)."""
    chosen = set(picked)
    out: list[tuple[str, str]] = []
    for sid in sorted(chosen):
        deps = (extra or {}).get(sid) or deps_of(rows.get(sid))
        for dep in deps:
            row = rows.get(dep)
            if dep not in chosen and (row is None or row["stage"] != Stage.DONE):
                out.append((sid, dep))
    return out


def note_dependents(store: Any, slug: str, msg: Any) -> None:
    """A story others depend on asks the founder something: say so in its message, since one
    answer unblocks them all (ADR-0021)."""
    waiting = dependents(rows_by_id(store, slug), msg.story_id)
    if not waiting:
        return
    one = len(waiting) == 1
    line = (
        f"{'A' if one else 'As'} {', '.join(waiting)} {'depende' if one else 'dependem'} desta: "
        f"sua resposta destrava {'as duas' if one else 'todas'}."
    )
    if line not in msg.impact:
        msg.impact = f"{msg.impact} {line}".strip()


def delivered_brief(store: Any, story_id: str, *, limit: int = 4000) -> str:
    """What the stories `story_id` depends on delivered and the founder approved: their criteria,
    the founder's guidance on them and the Worker's summary. contas Sprint 2, S-049: its spec was
    written before S-047 and S-051 were delivered (all specs of a chain come first), and it undid
    what the founder had approved in them (a decimal point, reading old data files)."""
    parts: list[str] = []
    for dep in deps_of(store.get_story(story_id)):
        row = store.get_story(dep) or {}
        if row.get("stage") != Stage.DONE:
            continue
        st = row.get("state") or {}
        lines = [f"### {dep}: {row.get('title', '')}"]
        if st.get("acceptance"):
            lines.append("Acceptance criteria it met:")
            lines += [f"- {c}" for c in st["acceptance"][:15]]
        if st.get("founder_notes"):
            lines.append("The founder's guidance on it, which its delivery follows:")
            lines += [f"- {n[:600]}" for n in st["founder_notes"][-5:]]
        if st.get("worker_summary"):
            lines.append(f"What was built:\n{str(st['worker_summary'])[:1200]}")
        parts.append("\n".join(lines))
    if not parts:
        return ""
    text = "\n\n".join(parts)
    return (
        "## What the stories this one builds on delivered (approved by the founder)\n"
        + (text if len(text) <= limit else text[:limit] + "\n…")
        + "\n\n"
    )
