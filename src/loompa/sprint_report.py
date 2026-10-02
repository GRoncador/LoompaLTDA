"""Sprint report (Plano 8.2): what a sprint did, measured in code. No model.

Everything here is read from what the factory already records: the events and the usage table
inside the sprint's window (from its start to its close, or now while it runs). Per story: the
stages it went through and how long each took, wall-clock and model time, calls, tokens, real
cost, attempts and escalations, blocks, the founder's answers, deliveries and requests for
changes. Then what was not planned (stories that joined mid-sprint, tasks added after the plan,
extra review rounds), what was left for later (cards and findings filed during the sprint) and
the totals, compared with the sprint before.

Sprints that ran before the trace (ADR-0015) have no task origins, task times, cuts or reported
cost: those numbers are `None` and the report says "não medido" instead of showing a zero.
The executive summary that goes on top is the Master's (`MasterAgent.write_sprint_report`).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loompa.engine.state import KANBAN_COLUMNS, kanban_column
from loompa.sprints import Sprint, SprintBoard, SprintStatus
from loompa.store import Store, now_iso

# A Worker task the plan did not write: who put it in the checklist (ADR-0015 §1).
ORIGIN_LABEL = {
    "plan": "plano",
    "replan": "replanejamento",
    "preflight": "pré-checagem de risco",
    "founder": "ajuste pedido por você",
    "inspector": "correção pedida pelo Inspector",
}
STAGE_LABEL = {
    "BACKLOG": "Backlog",
    "SPEC": "Especificação",
    "PLAN": "Planejamento",
    "DEV": "Desenvolvimento",
    "TEST": "Testes",
    "REVIEW": "Revisão",
    "AWAITING_FOUNDER": "Aguardando você",
    "DONE": "Concluída",
    "CANCELLED": "Cancelada",
}
RESULT_LABEL = {
    "delivered": "entregue",
    "split": "dividida em épico",
    "cancelled": "cancelada",
    "withdrawn": "devolvida ao backlog",
    "waiting": "aguardando você",
    "working": "em andamento",
}
ORIGIN_STORY = {
    "epic": "parte de um épico dividido",
    "kaizen": "achado do Kaizen",
    "founder": "pedido seu",
    "brainstorm": "brainstorm",
    "meeting": "reunião",
}
TIMELINE_POINTS = 48
NOT_MEASURED = "não medido"


def _ts(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _seconds(a: str, b: str) -> float:
    return max(0.0, (_ts(b) - _ts(a)).total_seconds())


def window(sprint: Sprint) -> tuple[str, str]:
    """The sprint's time window: from its start (its creation while it is still planned) to its
    close, or now while it runs."""
    return sprint.started_at or sprint.created_at, sprint.closed_at or now_iso()


def members(sprint: Sprint, events: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """`(all, planned)`: every story that was in the sprint at some point, and the ones it
    started with. A story taken out in a meeting is no longer in `story_ids`; the events keep
    it."""
    planned: list[str] = []
    seen: list[str] = []
    for e in events:
        p = e["payload"]
        if p.get("sprint_id") != sprint.id:
            continue
        if e["type"] == "sprint.started":
            planned = [str(s) for s in p.get("stories") or []]
            seen += planned
        elif e["type"] == "sprint.adjusted":
            seen += [str(s) for s in (p.get("joined") or []) + (p.get("withdrawn") or [])]
    if not planned:  # a sprint started before `sprint.started` carried its stories
        planned = list(sprint.story_ids)
    out = list(dict.fromkeys([*planned, *seen, *sprint.story_ids]))
    return out, planned


# ----------------------------------------------------------------------- per story


def _stage_times(evs: list[dict[str, Any]], end: str) -> tuple[dict[str, float], list[tuple]]:
    """Seconds in each stage, from the story's stage changes in the window, and the changes
    themselves as `(at, stage)`. The last stage counts until the window's end unless terminal."""
    changes = [(e["created_at"], str(e["payload"].get("stage") or "")) for e in evs]
    changes = [(at, st) for at, st in changes if st]
    times: dict[str, float] = {}
    for i, (at, stage) in enumerate(changes):
        if stage in ("DONE", "CANCELLED"):
            continue
        until = changes[i + 1][0] if i + 1 < len(changes) else end
        times[stage] = times.get(stage, 0.0) + _seconds(at, until)
    return times, changes


def _story(
    store: Store,
    sid: str,
    sprint: Sprint,
    evs: list[dict[str, Any]],
    usage: list[dict[str, Any]],
    *,
    planned: bool,
    end: str,
    traced: bool,
    split_children: set[str],
    split_parents: set[str] = frozenset(),
) -> dict[str, Any]:
    row = store.get_story(sid) or {}
    st = row.get("state") or {}
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in evs:
        by_type.setdefault(e["type"], []).append(e)
    stage_times, changes = _stage_times(by_type.get("story.stage", []), end)
    stage = row.get("stage", "CANCELLED")
    if sid not in sprint.story_ids:
        result = "withdrawn"
    elif sid in split_parents:
        # an epic is done when its parts are: the parts are the deliveries, the epic is not
        # (tamagotchi SP-001 reported "4 de 5 entregues" counting S-002, which has no code)
        result = "split"
    elif stage == "DONE":
        result = "delivered"
    elif stage == "CANCELLED":
        result = "cancelled"
    elif stage == "AWAITING_FOUNDER":
        result = "waiting"
    else:
        result = "working"
    finished_at = next(
        (at for at, s in reversed(changes) if s in ("DONE", "CANCELLED")),
        None,
    )
    first = evs[0]["created_at"] if evs else None
    last = finished_at or (
        end if result in ("working", "waiting") else (evs[-1]["created_at"] if evs else None)
    )
    retries = [e for e in by_type.get("story.retry", []) if e["payload"].get("tier")]
    recoveries = [e for e in by_type.get("story.retry", []) if e["payload"].get("cause")]
    answers = by_type.get("inbox.answered", [])
    verdicts = [
        str(e["payload"].get("verdict") or "") for e in by_type.get("inspector.verdict", [])
    ]
    changes_asked = max(
        sum(1 for e in answers if e["payload"].get("option") == "changes"),
        len(by_type.get("story.reopened", [])),
    )
    finished = by_type.get("worker.task_finished", [])
    old_tasks = by_type.get("worker.task", [])
    if finished:
        tasks: dict[str, Any] | None = {
            "done": sum(1 for e in finished if e["payload"].get("outcome") == "finished"),
            "runs": len(finished),
            "unfinished": sum(1 for e in finished if e["payload"].get("outcome") == "unfinished"),
            "seconds": round(sum(float(e["payload"].get("duration_s") or 0) for e in finished)),
            "by_origin": dict(
                Counter(
                    str(e["payload"].get("origin") or "plan")
                    for e in finished
                    if e["payload"].get("outcome") == "finished"
                )
            ),
        }
    elif old_tasks:  # before the trace: how many runs and how they ended, nothing about origin
        tasks = {
            "done": sum(1 for e in old_tasks if e["payload"].get("ended_by") == "done"),
            "runs": len(old_tasks),
            "unfinished": sum(1 for e in old_tasks if e["payload"].get("ended_by") != "done"),
            "seconds": None,
            "by_origin": None,
        }
    else:
        tasks = None
    cost = sum(float(u["cost_usd"] or 0) for u in usage)
    if sid in split_children:
        joined_how = ORIGIN_STORY["epic"]
    else:
        joined_how = ORIGIN_STORY.get(str(row.get("origin") or ""), "incluída numa reunião")
    return {
        "id": sid,
        "title": row.get("title", sid),
        "origin": row.get("origin", ""),
        "kind": st.get("kind", "feature"),
        "complexity": st.get("complexity", ""),
        "planned": planned,
        "joined_how": None if planned else joined_how,
        "stage": stage,
        "result": result,
        "started_at": first,
        "finished_at": finished_at,
        "wall_s": round(_seconds(first, last)) if first and last else 0,
        "model_s": round(sum(int(u["duration_ms"] or 0) for u in usage) / 1000),
        "stage_s": {k: round(v) for k, v in stage_times.items()},
        "founder_wait_s": round(stage_times.get("AWAITING_FOUNDER", 0.0)),
        "calls": len(usage),
        "input_tokens": sum(int(u["input_tokens"] or 0) for u in usage),
        "output_tokens": sum(int(u["output_tokens"] or 0) for u in usage),
        "cost_usd": round(cost, 6),
        "retries": len(retries),
        "escalations": len(by_type.get("story.escalated", [])),
        "replans": len(by_type.get("story.replanned", [])),
        "restarts": len(by_type.get("story.restarted", [])),
        "recoveries": len(recoveries),
        "stalls": len(by_type.get("story.stalled", [])),
        "blocks": dict(
            Counter(str(e["payload"].get("reason") or "") for e in by_type.get("story.blocked", []))
        ),
        "founder_answers": len(answers),
        "deliveries": len(by_type.get("story.delivered", [])),
        "changes_asked": changes_asked,
        "review_rounds": len(verdicts),
        "verdicts": verdicts,
        "spec_rejections": len(by_type.get("spec.rejected", [])),
        "tasks": tasks,
        "cuts": len(by_type.get("llm.cut", [])) if traced else None,
        "unfinished_tasks": len(by_type.get("story.task_unfinished", [])) if traced else None,
    }


# --------------------------------------------------------------------------- report


def measure(store: Store, slug: str, sprint: Sprint, *, previous: bool = True) -> dict[str, Any]:
    """The measured part of a sprint's report, as plain data (JSON-ready)."""
    start, end = window(sprint)
    events = store.events_between(slug, start, end)
    usage = store.usage_between(slug, start, end)
    traced = any(u["span_id"] for u in usage)  # the trace exists from ADR-0015 on
    ids, planned = members(sprint, events)
    in_sprint = set(ids)
    by_story: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e["story_id"]:
            by_story.setdefault(e["story_id"], []).append(e)
    usage_by_story: dict[str, list[dict[str, Any]]] = {}
    for u in usage:
        if u["story_id"]:
            usage_by_story.setdefault(u["story_id"], []).append(u)
    split_children = {
        str(c)
        for e in events
        if e["type"] == "story.split"
        for c in e["payload"].get("children") or []
    }
    stories = [
        _story(
            store,
            sid,
            sprint,
            by_story.get(sid, []),
            usage_by_story.get(sid, []),
            planned=sid in planned,
            end=end,
            traced=traced,
            split_children=split_children,
            split_parents={e["story_id"] for e in events if e["type"] == "story.split"},
        )
        for sid in ids
    ]

    # what was not planned
    added_tasks: Counter[str] = Counter()
    for s in stories:
        for origin, n in ((s["tasks"] or {}).get("by_origin") or {}).items():
            if origin != "plan":
                added_tasks[origin] += n
    unplanned = {
        "joined": [
            {"id": s["id"], "title": s["title"], "how": s["joined_how"]}
            for s in stories
            if not s["planned"]
        ],
        "tasks_added": dict(added_tasks) if traced else None,
        "extra_review_rounds": sum(max(0, s["review_rounds"] - 1) for s in stories),
        "spec_rejections": sum(s["spec_rejections"] for s in stories),
        "replans": sum(s["replans"] for s in stories),
        "restarts": sum(s["restarts"] for s in stories),
    }

    # what was left for later: cards filed during the sprint that are not part of it
    created = [
        e
        for e in events
        if e["type"] == "story.created" and e["story_id"] and e["story_id"] not in in_sprint
    ]
    left = []
    for e in created:
        row = store.get_story(e["story_id"]) or {}
        left.append(
            {
                "id": e["story_id"],
                "title": row.get("title") or e["payload"].get("title", ""),
                "origin": e["payload"].get("origin") or row.get("origin", ""),
                "stage": row.get("stage", ""),
            }
        )
    findings = Counter(
        str(e["payload"].get("kind") or "") for e in events if e["type"] == "kaizen.learning"
    )
    later = {
        "cards": left,
        "findings": dict(findings),
        "withdrawn": [
            {"id": s["id"], "title": s["title"]} for s in stories if s["result"] == "withdrawn"
        ],
        "duplicates": sum(1 for e in events if e["type"] == "backlog.duplicate"),
    }

    totals = _totals(stories, usage, start, end, sprint)
    report: dict[str, Any] = {
        "sprint": {
            "id": sprint.id,
            "goal": sprint.goal,
            "status": sprint.status.value,
            "created_at": sprint.created_at,
            "started_at": sprint.started_at,
            "closed_at": sprint.closed_at,
        },
        "generated_at": now_iso(),
        "measured": {
            "traced": traced,
            "reported_cost_calls": sum(1 for u in usage if u["cost_source"] == "reported"),
            "calls": len(usage),
        },
        "totals": totals,
        "stories": stories,
        "unplanned": unplanned,
        "later": later,
        "stage_time": _stage_time(stories),
        "timeline": _timeline(by_story, ids, start, end),
        "previous": None,
    }
    if previous:
        prev = previous_sprint(store, slug, sprint)
        if prev is not None:
            report["previous"] = {
                "id": prev.id,
                "goal": prev.goal,
                "totals": measure(store, slug, prev, previous=False)["totals"],
            }
    return report


def _totals(
    stories: list[dict[str, Any]],
    usage: list[dict[str, Any]],
    start: str,
    end: str,
    sprint: Sprint,
) -> dict[str, Any]:
    results = Counter(s["result"] for s in stories)
    delivered = results.get("delivered", 0)
    story_cost = sum(s["cost_usd"] for s in stories)
    traced = any(u["span_id"] for u in usage)
    tasks = [s["tasks"] for s in stories if s["tasks"]]
    return {
        "stories": len(stories),
        "planned": sum(1 for s in stories if s["planned"]),
        "joined": sum(1 for s in stories if not s["planned"]),
        "delivered": delivered,
        "split": results.get("split", 0),
        "cancelled": results.get("cancelled", 0),
        "withdrawn": results.get("withdrawn", 0),
        "waiting": results.get("waiting", 0),
        "working": results.get("working", 0),
        "duration_s": round(_seconds(start, end)) if sprint.started_at else 0,
        "wall_s": sum(s["wall_s"] for s in stories),
        "model_s": sum(s["model_s"] for s in stories),
        "founder_wait_s": sum(s["founder_wait_s"] for s in stories),
        "calls": sum(s["calls"] for s in stories),
        "input_tokens": sum(s["input_tokens"] for s in stories),
        "output_tokens": sum(s["output_tokens"] for s in stories),
        "cost_usd": round(story_cost, 6),
        # meetings, triage, Kaizen and every other call of the factory in the same window
        "factory_cost_usd": round(sum(float(u["cost_usd"] or 0) for u in usage), 6),
        "cost_per_delivered_usd": round(story_cost / delivered, 6) if delivered else None,
        "retries": sum(s["retries"] for s in stories),
        "escalations": sum(s["escalations"] for s in stories),
        "replans": sum(s["replans"] for s in stories),
        "restarts": sum(s["restarts"] for s in stories),
        "recoveries": sum(s["recoveries"] for s in stories),
        "stalls": sum(s["stalls"] for s in stories),
        "blocks": sum(sum(s["blocks"].values()) for s in stories),
        "founder_answers": sum(s["founder_answers"] for s in stories),
        "deliveries": sum(s["deliveries"] for s in stories),
        "changes_asked": sum(s["changes_asked"] for s in stories),
        "review_rounds": sum(s["review_rounds"] for s in stories),
        # work done again: attempts after a failure, re-plans, restarts and changes the founder
        # asked for. Extra review rounds are not added: each of those attempts is reviewed again
        "rework": sum(
            s["retries"] + s["replans"] + s["restarts"] + s["changes_asked"] for s in stories
        ),
        "tasks_done": sum(t["done"] for t in tasks) if tasks else None,
        "cuts": sum(s["cuts"] or 0 for s in stories) if traced else None,
    }


def _stage_time(stories: list[dict[str, Any]]) -> dict[str, int]:
    out: Counter[str] = Counter()
    for s in stories:
        for stage, secs in s["stage_s"].items():
            out[stage] += secs
    return dict(out)


def _timeline(
    by_story: dict[str, list[dict[str, Any]]], ids: list[str], start: str, end: str
) -> list[dict[str, Any]]:
    """Burn-up: how many of the sprint's stories were in each kanban column over time."""
    if not ids:
        return []
    t0, t1 = _ts(start), _ts(end)
    span = max((t1 - t0).total_seconds(), 1.0)
    changes = {
        sid: [
            (_ts(e["created_at"]), kanban_column(str(e["payload"].get("stage") or "")))
            for e in by_story.get(sid, [])
            if e["type"] == "story.stage" and e["payload"].get("stage")
        ]
        for sid in ids
    }
    points = []
    for i in range(TIMELINE_POINTS + 1):
        at = t0.timestamp() + span * i / TIMELINE_POINTS
        counts = {key: 0 for key, _ in KANBAN_COLUMNS}
        for sid in ids:
            column = "BACKLOG"
            for when, col in changes[sid]:
                if when.timestamp() <= at:
                    column = col
                else:
                    break
            counts[column] = counts.get(column, 0) + 1
        points.append(
            {"at": datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds"), **counts}
        )
    return points


def previous_sprint(store: Store, slug: str, sprint: Sprint) -> Sprint | None:
    """The last finished sprint that started before this one: what it is compared with. Before
    ADR-0018 two sprints could run at the same time, so it may have closed after this began."""
    board = SprintBoard(store, slug)
    begun = sprint.started_at or sprint.created_at
    before = [
        s
        for s in board.sprints()
        if s.id != sprint.id
        and s.status in (SprintStatus.CLOSED, SprintStatus.CANCELLED)
        and s.started_at
        and s.started_at < begun
    ]
    return before[-1] if before else None


def sprints_of(store: Store, slug: str, story_id: str) -> list[str]:
    """Every sprint a story went through (10.8): a restart or a story left over can put it in
    more than one. Read from the sprints themselves, never from a column on the story."""
    return [s.id for s in SprintBoard(store, slug).sprints() if story_id in s.story_ids]


def current_sprints(store: Store, slug: str) -> dict[str, str]:
    """`story -> sprint` for the card marker: the open or running sprint a story is in, else
    the last one it went through."""
    out: dict[str, str] = {}
    for sprint in SprintBoard(store, slug).sprints():  # oldest first: later ones win
        for sid in sprint.story_ids:
            out[sid] = sprint.id
    return out


# -------------------------------------------------------------------------- render


def duration(seconds: float | None) -> str:
    if seconds is None:
        return NOT_MEASURED
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86_400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    return f"{s // 86_400}d {(s % 86_400) // 3600}h"


def usd(v: float | None) -> str:
    if v is None:
        return NOT_MEASURED
    return f"US$ {v:.2f}" if v >= 1 else f"US$ {v:.4f}"


def _num(v: float | int | None) -> str:
    return NOT_MEASURED if v is None else f"{v:,}".replace(",", ".")


def _delta(now: float | None, before: float | None, fmt) -> str:
    if now is None or before is None:
        return ""
    if before == 0:
        return f" (antes: {fmt(before)})"
    if now == before:
        return f" (antes: {fmt(before)}, igual)"
    pct = (now - before) / before * 100
    return f" (antes: {fmt(before)}, {'+' if pct >= 0 else ''}{pct:.0f}%)"


def render_markdown(report: dict[str, Any], summary: str = "") -> str:
    """The report as the founder reads it: `.loompa/reports/SP-00X.md`, `loompa sprint report`
    and the Sprints tab show the same content. pt-BR; story ids, no paths or code."""
    sp, t = report["sprint"], report["totals"]
    prev = report.get("previous")
    pt = prev["totals"] if prev else {}
    lines = [f"# Relatório do {sp['id']}", ""]
    if sp["goal"]:
        lines += [f"**Meta:** {sp['goal']}", ""]
    status = {
        "open": "em planejamento",
        "running": "rodando",
        "closed": "encerrado",
        "cancelled": "cancelado",
    }
    period = (
        f"{_day(sp['started_at'])} a {_day(sp['closed_at'])}"
        if sp["closed_at"]
        else f"desde {_day(sp['started_at'] or sp['created_at'])}"
    )
    lines += [f"**Status:** {status.get(sp['status'], sp['status'])} · {period}", ""]
    if summary:
        lines += ["## Resumo executivo", "", summary.strip(), ""]

    lines += ["## Números do sprint", ""]
    compare = f" Comparação com o {prev['id']}." if prev else ""
    lines.append(
        f"{t['delivered']} de {t['stories']} histórias entregues"
        + (f", {t['cancelled']} canceladas" if t["cancelled"] else "")
        + (f", {t['withdrawn']} devolvidas ao backlog" if t["withdrawn"] else "")
        + (f", {t['waiting']} aguardando você" if t["waiting"] else "")
        + (f", {t['working']} em andamento" if t["working"] else "")
        + "."
        + compare
    )
    lines.append("")
    rows = [
        (
            "Duração do sprint",
            duration(t["duration_s"]),
            _delta(t["duration_s"], pt.get("duration_s"), duration),
        ),
        ("Custo das histórias", usd(t["cost_usd"]), _delta(t["cost_usd"], pt.get("cost_usd"), usd)),
        (
            "Custo por história entregue",
            usd(t["cost_per_delivered_usd"]) if t["delivered"] else "—",
            _delta(t["cost_per_delivered_usd"], pt.get("cost_per_delivered_usd"), usd),
        ),
        ("Custo total da fábrica no período", usd(t["factory_cost_usd"]), ""),
        ("Consultas aos modelos", _num(t["calls"]), _delta(t["calls"], pt.get("calls"), _num)),
        ("Tempo de modelo", duration(t["model_s"]), ""),
        ("Tempo das histórias esperando você (somado)", duration(t["founder_wait_s"]), ""),
        (
            "Retrabalho (novas tentativas, replanejamentos, recomeços, ajustes pedidos)",
            _num(t["rework"]),
            _delta(t["rework"], pt.get("rework"), _num),
        ),
        ("Subidas para o modelo mais forte", _num(t["escalations"]), ""),
        ("Bloqueios", _num(t["blocks"]), ""),
        ("Respostas suas", _num(t["founder_answers"]), ""),
        ("Pedidos de ajuste na entrega", _num(t["changes_asked"]), ""),
        ("Tarefas concluídas", _num(t["tasks_done"]), ""),
        ("Respostas cortadas pelo limite", _num(t["cuts"]), ""),
    ]
    lines += ["| Medida | Valor |", "| --- | --- |"]
    lines += [f"| {k} | {v}{d} |" for k, v, d in rows]
    lines.append("")

    lines += ["## Por história", ""]
    lines += [
        "| História | Resultado | Tempo | Modelo | Consultas | Custo | Tentativas | Bloqueios | Tarefas |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in report["stories"]:
        tasks = s["tasks"]
        task_text = (
            (NOT_MEASURED if not report["measured"]["traced"] else "—")
            if tasks is None
            else f"{tasks['done']}"
            + (f" ({tasks['unfinished']} não terminadas)" if tasks["unfinished"] else "")
        )
        tries = s["retries"] + s["restarts"]
        lines.append(
            f"| {s['id']} · {_cell(s['title'])} | {RESULT_LABEL[s['result']]} | "
            f"{duration(s['wall_s'])} | {duration(s['model_s'])} | {s['calls']} | "
            f"{usd(s['cost_usd'])} | {tries}"
            + (f" (+{s['escalations']} subida)" if s["escalations"] else "")
            + f" | {sum(s['blocks'].values())} | {task_text} |"
        )
    lines.append("")

    un = report["unplanned"]
    lines += ["## O que não estava previsto", ""]
    said = False
    for j in un["joined"]:
        lines.append(f"- {j['id']} · {j['title']} entrou no meio do sprint ({j['how']}).")
        said = True
    if un["tasks_added"] is None:
        lines.append(f"- Tarefas acrescentadas depois do plano: {NOT_MEASURED} neste sprint.")
    else:
        for origin, n in sorted(un["tasks_added"].items()):
            lines.append(
                f"- {n} tarefa(s) acrescentada(s) depois do plano: {ORIGIN_LABEL.get(origin, origin)}."
            )
            said = True
    for key, label in (
        ("extra_review_rounds", "rodada(s) extra(s) de revisão do Inspector"),
        ("spec_rejections", "especificação(ões) devolvida(s) pelo Product Owner"),
        ("replans", "replanejamento(s)"),
        ("restarts", "história(s) recomeçada(s) do zero"),
    ):
        if un[key]:
            lines.append(f"- {un[key]} {label}.")
            said = True
    if not said and un["tasks_added"] is not None:
        lines.append("- Nada: o sprint andou como foi planejado.")
    lines.append("")

    later = report["later"]
    lines += ["## O que ficou para depois", ""]
    if later["withdrawn"]:
        lines.append(
            "- Devolvidas ao backlog: "
            + ", ".join(f"{w['id']} · {w['title']}" for w in later["withdrawn"])
            + "."
        )
    if later["cards"]:
        by_origin = Counter(c["origin"] for c in later["cards"])
        lines.append(
            f"- {len(later['cards'])} card(s) novo(s) no backlog durante o sprint: "
            + ", ".join(f"{n} {ORIGIN_STORY.get(o, o)}" for o, n in by_origin.most_common())
            + "."
        )
    if later["findings"]:
        from loompa.agents.kaizen import KIND_LABEL

        lines.append(
            "- Achados do Kaizen: "
            + ", ".join(
                f"{n} {KIND_LABEL.get(k, k).lower()}"
                for k, n in Counter(later["findings"]).most_common()
            )
            + "."
        )
    if later["duplicates"]:
        lines.append(
            f"- {later['duplicates']} achado(s) repetido(s) apontado(s) para cards que já existiam."
        )
    if not (later["withdrawn"] or later["cards"] or later["findings"]):
        lines.append("- Nada novo entrou no backlog.")
    lines.append("")
    if not report["measured"]["traced"]:
        lines += [
            "_Este sprint rodou antes do rastro da fábrica: a origem e o tempo das tarefas, as "
            "respostas cortadas e o custo informado pelo provedor aparecem como “não medido”._",
            "",
        ]
    factory = report.get("factory") or []
    if factory:  # the factory's own findings of this sprint (8.3): the Fábrica tab has the rest
        lines += ["## Achados da fábrica neste sprint", ""]
        lines += [
            f"- [{f['severity']}] {f['title']}" + (" (novo)" if f.get("new") else "")
            for f in factory
        ]
        lines += ["", "Detalhes e evidências na aba Fábrica do painel.", ""]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "/")[:70]


def _day(iso: str | None) -> str:
    if not iso:
        return "—"
    return _ts(iso).astimezone().strftime("%d/%m %H:%M")  # the founder's clock, as the panel


# ------------------------------------------------------------------------- summary


def summary_facts(report: dict[str, Any]) -> str:
    """The report as the Master reads it to write the executive summary (English headings)."""
    t, sp = report["totals"], report["sprint"]
    lines = [
        f"## Sprint {sp['id']} ({sp['status']})",
        f"Goal: {sp['goal'] or '(none)'}",
        f"Stories: {t['stories']} ({t['planned']} planned, {t['joined']} joined mid-sprint); "
        f"delivered {t['delivered']}"
        + (
            f", split into an epic whose parts are counted instead {t['split']}"
            if t.get("split")
            else ""
        )
        + f", cancelled {t['cancelled']}, back to backlog "
        f"{t['withdrawn']}, waiting for the founder {t['waiting']}, still in progress {t['working']}.",
        f"Duration: {duration(t['duration_s'])}. Cost of the stories: {usd(t['cost_usd'])}"
        + (f" ({usd(t['cost_per_delivered_usd'])} per delivered story)" if t["delivered"] else "")
        + f". Founder waiting time: {duration(t['founder_wait_s'])}.",
        f"Rework: {t['rework']} (retries {t['retries']}, re-plans {t['replans']}, restarts "
        f"{t['restarts']}, changes the founder asked {t['changes_asked']}). Escalations to a "
        f"stronger model: {t['escalations']}. Blocks: {t['blocks']}.",
    ]
    prev = report.get("previous")
    if prev:
        p = prev["totals"]
        lines.append(
            f"Previous sprint {prev['id']}: delivered {p['delivered']} of {p['stories']}, "
            f"duration {duration(p['duration_s'])}, cost {usd(p['cost_usd'])}"
            + (
                f" ({usd(p['cost_per_delivered_usd'])} per delivered story)"
                if p["delivered"]
                else ""
            )
            + f", rework {p['rework']}."
        )
    lines.append("## Stories")
    for s in report["stories"]:
        lines.append(
            f'- {s["id"]} "{s["title"]}": {s["result"]}, {duration(s["wall_s"])}, '
            f"{usd(s['cost_usd'])}, retries {s['retries']}, blocks {sum(s['blocks'].values())}"
            + ("" if s["planned"] else f", joined mid-sprint ({s['joined_how']})")
        )
    later = report["later"]
    lines.append(
        f"## Left for later\nNew backlog cards: {len(later['cards'])}; Kaizen findings: "
        f"{sum(later['findings'].values())}; back to backlog: {len(later['withdrawn'])}."
    )
    return "\n".join(lines)


def fallback_summary(report: dict[str, Any]) -> str:
    """The executive summary written in code, when no model is available (dry-run) or the one
    it wrote did not pass the audit."""
    t = report["totals"]
    said = [
        f"O {report['sprint']['id']} entregou {t['delivered']} de {t['stories']} histórias"
        + (f" em {duration(t['duration_s'])}" if t["duration_s"] else "")
        + (f", a {usd(t['cost_per_delivered_usd'])} por entrega" if t["delivered"] else "")
        + "."
    ]
    if report["sprint"]["goal"]:
        said.append(f"Meta: {report['sprint']['goal']}.")
    if t["joined"]:
        said.append(f"{t['joined']} entraram no meio do sprint.")
    if t["rework"]:
        said.append(
            f"Houve {t['rework']} retrabalho(s), {t['escalations']} com o modelo mais forte."
        )
    if t["founder_wait_s"]:
        said.append(f"Somadas, as histórias esperaram por você {duration(t['founder_wait_s'])}.")
    prev = report.get("previous")
    if prev and t["delivered"] and prev["totals"].get("cost_per_delivered_usd"):
        before = prev["totals"]["cost_per_delivered_usd"]
        now = t["cost_per_delivered_usd"]
        said.append(
            f"Cada entrega custou {'menos' if now < before else 'mais'} que no {prev['id']} "
            f"({usd(now)} contra {usd(before)})."
        )
    return " ".join(said)


# ---------------------------------------------------------------------------- files


def report_paths(reports_dir: Path, sprint_id: str) -> tuple[Path, Path]:
    return reports_dir / f"{sprint_id}.md", reports_dir / f"{sprint_id}.json"


def save(reports_dir: Path, report: dict[str, Any], summary: str) -> Path:
    """Write the report as markdown and JSON. The folder keeps itself out of the product's
    repository: the report is about the factory's work (cost, time), not part of the product."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    ignore = reports_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text(
            "# Loompa sprint reports: the factory's own records, never committed\n*\n"
        )
    md, js = report_paths(reports_dir, report["sprint"]["id"])
    md.write_text(render_markdown(report, summary), encoding="utf-8")
    js.write_text(
        json.dumps({**report, "summary": summary}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return md


def load(reports_dir: Path, sprint_id: str) -> dict[str, Any] | None:
    _, js = report_paths(reports_dir, sprint_id)
    if not js.is_file():
        return None
    try:
        return json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
