"""The story's trace as the panel reads it (plan 11.4).

`loompa trace` has shown each round of the tool loop in the terminal since Fase 8.1; this turns
the same file into JSON for the story window, so the founder can watch what the Loompa asked the
model, which tools it called with which arguments and what came back — the way an agent CLI shows
its own run. The trace file stays the source: nothing new is recorded for this.

Two levels, because a story's trace is big: `outline` is the tree with the numbers of each span,
and `detail` opens one span with its messages resolved from the hashes they are stored under.
Text is clipped here, not in the browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loompa.trace import Trace, TraceNode, read_trace, trace_dir

MAX_SPANS = 4000  # a long story's trace: enough for the whole run, bounded for the browser
MAX_TEXT = 40_000  # one message shown in the window

# what each kind of span carries that is worth a line in the panel
KEEP = {
    "node": ("tier", "next", "attempt"),
    "task": ("text", "origin", "tier", "outcome"),
    "round": ("label", "calls"),
    "llm": (
        "agent", "provider", "model", "responded", "served_by", "finish_reason",
        "input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "cost_usd",
        "cuts", "free_fallback",
    ),
    "tool": ("agent", "args", "ok", "chars", "repeat", "note", "error"),
}


def _clip(value: Any, limit: int = 400) -> Any:
    if isinstance(value, str):
        return value[:limit] + ("…" if len(value) > limit else "")
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:20]]
    return value


def _attempts(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Every try of one call: the candidates that failed or were cut before the answer."""
    out = []
    for a in attrs.get("attempts") or []:
        if not isinstance(a, dict):
            continue
        out.append(
            {
                "model": a.get("model"),
                "outcome": a.get("outcome"),
                "error": _clip(a.get("error"), 200),
                "output_tokens": a.get("output_tokens"),
                "cost_usd": a.get("cost_usd"),
            }
        )
    return out


def _span(node: TraceNode, depth: int) -> dict[str, Any]:
    s, a = node.span, node.attrs
    out: dict[str, Any] = {
        "id": s.get("id"),
        "depth": depth,
        "kind": node.kind,
        "name": s.get("name"),
        "start": s.get("start"),
        "ms": s.get("ms"),
        "status": s.get("status"),
        "has_detail": bool(a.get("messages") or a.get("response") or a.get("result")),
    }
    if s.get("error"):
        out["error"] = _clip(s["error"], 300)
    out["attrs"] = {k: _clip(a[k]) for k in KEEP.get(node.kind, ()) if a.get(k) is not None}
    if node.kind == "llm" and a.get("attempts"):
        out["attempts"] = _attempts(a)
    return out


def outline(root: Path, story_id: str) -> dict[str, Any]:
    """The whole trace flattened depth-first, newest run first: each span with its numbers."""
    trace = read_trace(trace_dir(root) / f"{story_id}.jsonl")
    rows: list[dict[str, Any]] = []
    for top in reversed(trace.tree()):  # the latest phase first, like the history tab
        stack: list[tuple[TraceNode, int]] = [(top, 0)]
        while stack:
            node, depth = stack.pop()
            rows.append(_span(node, depth))
            stack.extend((child, depth + 1) for child in reversed(node.children))
    calls = [s for s in trace.spans if s.get("kind") == "llm"]
    tools = [s for s in trace.spans if s.get("kind") == "tool"]
    return {
        "spans": rows[:MAX_SPANS],
        "truncated": len(rows) > MAX_SPANS,
        "totals": {
            "calls": len(calls),
            "tools": len(tools),
            "cost_usd": round(sum(float((s.get("attrs") or {}).get("cost_usd") or 0) for s in calls), 4),
            "cuts": sum(int((s.get("attrs") or {}).get("cuts") or 0) for s in calls),
            "model_ms": sum(int(s.get("ms") or 0) for s in calls),
            "tool_ms": sum(int(s.get("ms") or 0) for s in tools),
        },
    }


def _message(trace: Trace, h: str) -> dict[str, Any] | None:
    msg = trace.messages.get(h)
    if msg is None:
        return None
    return {
        "role": msg.get("role", "?"),
        "name": msg.get("name"),
        "content": _clip(msg.get("content") or "", MAX_TEXT),
        "tool_calls": [
            {"name": c.get("name"), "arguments": _clip(c.get("arguments"), 2000)}
            for c in (msg.get("tool_calls") or [])
        ],
    }


def detail(root: Path, story_id: str, span_id: str) -> dict[str, Any] | None:
    """One span with its prompt, its answer and the tool result, resolved from the hashes."""
    trace = read_trace(trace_dir(root) / f"{story_id}.jsonl")
    span = trace.find(span_id)
    if span is None:
        return None
    attrs = dict(span.get("attrs") or {})
    hashes = attrs.pop("messages", None) or []
    response = attrs.pop("response", None)
    result = attrs.pop("result", None)
    return {
        "id": span.get("id"),
        "kind": span.get("kind"),
        "name": span.get("name"),
        "start": span.get("start"),
        "ms": span.get("ms"),
        "status": span.get("status"),
        "error": span.get("error"),
        "attrs": _clip(attrs, 2000),
        "messages": [m for m in (_message(trace, h) for h in hashes) if m],
        "response": _message(trace, response) if response else None,
        "result": _message(trace, result) if result else None,
    }
