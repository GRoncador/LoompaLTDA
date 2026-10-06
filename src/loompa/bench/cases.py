"""Real cases for the prompt bench (plan 14.1), taken from the factories' traces.

A case is one model call a factory really made: the messages it sent, the answer it got, what it
cost, and what became of the story afterwards (approved at the first review or not, how many
rounds, the inspector's verdict, cancelled). Replaying the same input with another prompt or
another model is then a measurement instead of a guess.

Three shapes of call, because not every prompt is a single question:
- `one_shot` — system + user, no tools (classify, summaries, reviews): replayed as is;
- `first`    — the opening call of a tool loop (system + user + tools): replayed with the same
  tools offered, to see what the model does first (read, test, edit);
- `final`    — the call that closed a tool loop with the JSON answer (spec, plan): replayed with
  the exploration it had done, flattened to text, to compare the answer it writes from it.

Cases live in the hub (`~/.loompa/bench/cases/<factory>/<prompt>.jsonl`), never in a repository:
they carry the factories' code, as the traces do.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loompa.bench import registry
from loompa.config.secrets import hub_home
from loompa.trace import read_trace, trace_dir

PER_PROMPT = 40


@dataclass
class Case:
    id: str
    factory: str
    story: str
    prompt: str
    similarity: float
    mode: str  # one_shot | first | final
    language: str
    messages: list[dict[str, Any]]
    tools: list[str] = field(default_factory=list)
    response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    tier: str = ""
    task: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    ms: int = 0
    at: str = ""
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    complexity: str | None = None
    role: str = ""
    outcome: dict[str, Any] = field(default_factory=dict)

    @property
    def system(self) -> str:
        return str(self.messages[0].get("content", "")) if self.messages else ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def bench_dir(home: Path | None = None) -> Path:
    return (home or hub_home()) / "bench"


def cases_dir(home: Path | None = None) -> Path:
    return bench_dir(home) / "cases"


# ----------------------------------------------------------------------------- harvesting


def story_outcomes(root: Path) -> dict[str, dict[str, Any]]:
    """What became of each story: the labels a case is judged against."""
    db_path = root / ".loompa" / "state.db"
    if not db_path.is_file():
        return {}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: dict[str, dict[str, Any]] = {}
    try:
        for sid, stage, raw in db.execute("SELECT id, stage, state_json FROM stories"):
            try:
                st = json.loads(raw or "{}")
            except ValueError:
                st = {}
            out[sid] = {
                "stage": stage,
                "kind": st.get("kind"),
                "complexity": st.get("complexity"),
                "spec_review_rounds": st.get("spec_review_rounds"),
                "plan_review_rounds": st.get("plan_review_rounds"),
                "qa_verdict": st.get("qa_verdict"),
                "attempts_tier1": st.get("attempts_tier1"),
                "founder_notes": len(st.get("founder_notes") or []),
                "merged": bool(st.get("merged_sha")),
                "tasks_total": st.get("tasks_total"),
            }
    finally:
        db.close()
    return out


def _mode(span: dict[str, Any], n_messages: int, answer: dict[str, Any]) -> str | None:
    tools = (span.get("attrs") or {}).get("tools")
    if not tools:
        return "one_shot" if n_messages == 2 else None
    if n_messages == 2:
        return "first"
    if not answer.get("tool_calls") and (answer.get("content") or "").lstrip().startswith(
        ("{", "`")
    ):
        return "final"
    return None


def harvest_factory(root: Path, slug: str, language: str = "pt-BR") -> Iterator[Case]:
    outcomes = story_outcomes(root)
    folder = trace_dir(root)
    if not folder.is_dir():
        return
    known: dict[str, tuple[str, float]] = {}  # system hash -> identified prompt
    for path in sorted(folder.glob("*.jsonl")):
        trace = read_trace(path)
        for span in trace.spans:
            if span.get("kind") != "llm" or span.get("status") != "ok":
                continue
            attrs = span.get("attrs") or {}
            hashes = attrs.get("messages") or []
            answer = trace.messages.get(str(attrs.get("response") or ""))
            if not hashes or answer is None:
                continue
            docs = [trace.messages.get(h) for h in hashes]
            if any(d is None for d in docs) or docs[0].get("role") != "system":
                continue
            mode = _mode(span, len(docs), answer)
            if mode is None:
                continue
            if hashes[0] not in known:
                known[hashes[0]] = registry.identify(str(docs[0].get("content", "")), language)
            prompt, similarity = known[hashes[0]]
            if not prompt:
                continue
            story = str(span.get("story") or "")
            yield Case(
                id=f"{slug}:{story or path.stem}:{span['id']}",
                factory=slug,
                story=story,
                prompt=prompt,
                similarity=round(similarity, 3),
                mode=mode,
                language=language,
                messages=[{k: v for k, v in d.items() if k not in ("t", "h")} for d in docs],
                tools=list(attrs.get("tools") or []),
                response=str(answer.get("content") or ""),
                tool_calls=list(answer.get("tool_calls") or []),
                model=str(attrs.get("model") or ""),
                provider=str(attrs.get("provider") or ""),
                tier=str(attrs.get("tier") or ""),
                task=str(attrs.get("task") or ""),
                cost_usd=float(attrs.get("cost_usd") or 0),
                input_tokens=int(attrs.get("input_tokens") or 0),
                output_tokens=int(attrs.get("output_tokens") or 0),
                ms=int(span.get("ms") or 0),
                at=str(span.get("start") or ""),
                max_tokens=attrs.get("max_tokens"),
                reasoning_effort=attrs.get("reasoning_effort"),
                complexity=attrs.get("complexity"),
                role=str(attrs.get("role") or ""),
                outcome=outcomes.get(story, {}),
            )


def sample(cases: list[Case], per_prompt: int = PER_PROMPT) -> list[Case]:
    """At most `per_prompt` cases per (prompt, mode), spread over stories, newest first: twenty
    calls of one story's tool loop would measure that story, not the prompt. Retries that sent
    the same input are one case."""
    groups: dict[tuple[str, str], dict[str, list[Case]]] = defaultdict(lambda: defaultdict(list))
    seen: set[tuple[str, str]] = set()
    for case in sorted(cases, key=lambda c: c.at, reverse=True):
        user = json.dumps(case.messages[1:], sort_keys=True, ensure_ascii=False)[:4000]
        if (case.prompt, user) in seen:
            continue
        seen.add((case.prompt, user))
        groups[(case.prompt, case.mode)][case.story].append(case)
    out: list[Case] = []
    for by_story in groups.values():
        queues = list(by_story.values())
        picked: list[Case] = []
        while queues and len(picked) < per_prompt:
            for q in list(queues):
                if not q:
                    queues.remove(q)
                    continue
                picked.append(q.pop(0))
                if len(picked) >= per_prompt:
                    break
        out.extend(picked)
    return out


def save(cases: list[Case], slug: str, home: Path | None = None) -> dict[str, int]:
    """Write one file per prompt for this factory, replacing the previous harvest."""
    folder = cases_dir(home) / slug
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.jsonl"):
        old.unlink()
    by_prompt: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        by_prompt[case.prompt].append(case)
    counts: dict[str, int] = {}
    for prompt, items in by_prompt.items():
        with (folder / f"{prompt}.jsonl").open("w", encoding="utf-8") as fh:
            for case in items:
                fh.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
        counts[prompt] = len(items)
    return counts


def load(
    prompt: str | None = None,
    *,
    mode: str | None = None,
    factories: list[str] | None = None,
    home: Path | None = None,
) -> list[Case]:
    folder = cases_dir(home)
    if not folder.is_dir():
        return []
    out: list[Case] = []
    for path in sorted(folder.glob("*/*.jsonl")):
        if factories and path.parent.name not in factories:
            continue
        if prompt and path.stem != prompt:
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    case = Case.from_dict(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if mode is None or case.mode == mode:
                    out.append(case)
    return out
