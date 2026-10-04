"""Dependencies between stories and the gate before `plan` (ADR-0021, plan 10.7 and 10.9).

A story's `depends_on` (a column of `stories`, written only by the Product Owner) names the cards
it needs delivered first. Stories linked that way form a *chain*. Nothing here writes: these are
the readings the Product Owner, the Scheduler, the engine and the panel share.

The gate decides, before a chain story plans, whether it may: every dependency delivered and every
spec of the chain approved (open); something still missing (wait: the story parks and frees its
slot, nobody is asked); or a dependency cancelled or sent back to the backlog (gone: the founder
decides). Waiting is time, not a decision, so it never reaches the inbox.

A dependency that is *built and approved by the Inspector*, waiting only for the founder to accept
the delivery, counts as delivered for this gate (11.5): the founder's day was becoming the chain's
bottleneck, and nothing is lost by building on code the Inspector passed. The dependent starts its
worktree from that branch, so it has the code. What the founder's approval still holds is the
merge: a story whose dependency is not in the base yet parks before `review` (`merge_gate`),
because merging it would drag the unapproved delivery into the base with it. Only one dependency
may be in that state, since a worktree starts from one branch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loompa.engine.state import TERMINAL, Stage

WAIT_KEY = "waiting"  # state.extra: parked at the gate before `plan`, with what it waits for
BUILT_ON_KEY = "built_on"  # state.extra: the story whose branch this worktree started from (11.5)
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


def awaiting_merge(row: Mapping[str, Any]) -> bool:
    """Built, green for the Inspector and waiting only for the founder to accept the delivery.
    Its code exists on its branch; what it lacks is the merge into the base."""
    st = row.get("state") or {}
    return (
        row["stage"] == Stage.AWAITING_FOUNDER
        and str(st.get("blocked_reason") or "") == "delivery"
        and str(st.get("qa_verdict") or "") in ("PASS", "CONCERNS")
    )


def parked_before_review(row: Mapping[str, Any]) -> bool:
    """Finished and green, parked before `review` because a delivery it was built on is not in
    the base yet. It never asked the founder anything: it is the one before it that did."""
    note = ((row.get("state") or {}).get("extra") or {}).get(WAIT_KEY) or {}
    return bool(note.get("unmerged")) and row["stage"] not in TERMINAL


def ready_to_merge(row: Mapping[str, Any]) -> bool:
    """The story's code is written and the Inspector passed it; only the merge into the base is
    missing — either because the founder has not accepted the delivery, or because the delivery
    before it in the chain has not been accepted. Either way the next story may build on it, and
    that is what keeps a chain of three moving while the founder decides the first (11.5)."""
    return awaiting_merge(row) or parked_before_review(row)


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
    unmerged: list[str] = field(default_factory=list)  # approved deliveries not in the base yet

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate.value,
            "pending": self.pending,
            "founder": self.founder,
            "gone": self.gone,
            "chain": self.chain,
            "unmerged": self.unmerged,
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
        if self.unmerged:
            return f"construindo sobre {', '.join(self.unmerged)}, que espera o seu aceite"
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
        elif ready_to_merge(row):
            out.unmerged.append(dep)  # built and approved by the Inspector: good enough to plan
        elif row["stage"] != Stage.DONE:
            out.pending.append(dep)
            if row["stage"] == Stage.AWAITING_FOUNDER:
                out.founder.append(dep)
    if len(out.unmerged) > 1:
        # a worktree starts from one branch: with two deliveries pending, the story would be
        # missing one of them. It waits for the founder, as before.
        out.pending.extend(out.unmerged)
        out.founder.extend(out.unmerged)
        out.unmerged = []
    out.chain = [
        sid for sid in chain_of(rows, story_id) if sid != story_id and not spec_passed(rows[sid])
    ]
    if out.gone:
        out.gate = Gate.GONE
    elif out.pending or out.chain:
        out.gate = Gate.WAIT
    return out


def built_on(row: Mapping[str, Any] | None) -> str:
    """The story whose branch this one's worktree started from, when there is one (11.5)."""
    extra = ((row or {}).get("state") or {}).get("extra") or {}
    return str(extra.get(BUILT_ON_KEY) or "")


def merge_gate(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> list[str]:
    """Dependencies this story must not merge over. Two cases, and the second is the one a
    rejected delivery creates:

    * the dependency is built and green but not in the base — a delivery the founder has not
      accepted, or one parked behind another in the chain. Merging now would carry it along;
    * the story's own branch *started from* that dependency's branch and the dependency is not in
      the base yet. That stays true when the founder asks for changes: the delivery goes back to
      `dev` and stops being "awaiting", but this story still carries its commits, so it waits
      until the dependency is really merged (its code arrives in the rebase before the delivery).
    """
    me = rows.get(story_id)
    if me is None:
        return []
    on = built_on(me)
    out = []
    for dep in deps_of(me):
        row = rows.get(dep)
        if row is None:
            continue
        if ready_to_merge(row) or (dep == on and row["stage"] != Stage.DONE):
            out.append(dep)
    return out


def gone_under(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> str:
    """The story this one was built on, when it was cancelled or sent back to the backlog: its
    code will never reach the base, so this one cannot be delivered as it stands (11.5)."""
    on = built_on(rows.get(story_id))
    row = rows.get(on) if on else None
    return on if row is not None and row["stage"] in (Stage.CANCELLED, Stage.BACKLOG) else ""


def still_waiting(rows: Mapping[str, Mapping[str, Any]], row: Mapping[str, Any]) -> bool:
    """A parked story that must stay parked. Where it parked says what it waits for: `unmerged`
    in the note means it is a finished story held before `review` until the delivery it was
    built on is accepted; anything else is the gate before `plan`."""
    note = ((row.get("state") or {}).get("extra") or {}).get(WAIT_KEY)
    if not note:
        return False
    if note.get("unmerged"):
        return bool(merge_gate(rows, row["id"]))
    return plan_gate(rows, row["id"]).gate == Gate.WAIT


def base_branch_for(rows: Mapping[str, Mapping[str, Any]], story_id: str) -> str:
    """The branch a new worktree starts from: the dependency whose delivery is approved but not
    merged yet, so the story is written on top of its code. "" means the base branch."""
    pending = merge_gate(rows, story_id)
    if len(pending) != 1:
        return ""
    return str((rows.get(pending[0]) or {}).get("branch") or "")


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
