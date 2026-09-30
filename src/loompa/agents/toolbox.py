"""Tools for every role: permission profiles and the toolbox that enforces them (ADR-0009).

A profile says which ACI tools a role may call and where its writes may land. The `Toolbox`
checks the profile when a tool is *called*, not only when tools are listed to the model, so a
role that hallucinates a call to a tool it was never offered is refused in code.

| role                                           | reads | writes                              | runs tests |
| ---------------------------------------------- | ----- | ----------------------------------- | ---------- |
| worker                                         | repo  | the story's `allowed_paths`         | yes        |
| inspector                                      | repo  | nothing                             | yes        |
| architect, product, product_owner, analyst,    | repo  | `.loompa/specs/`, plus `docs/` when | no         |
| master                                         |       | running inside a story worktree     |            |
| anyone else                                    | repo  | nothing                             | no         |

Roles that browse the web (Analyst) also get the MCP tools of the servers configured for them.
Credentials and factory state are unreadable for every role (`aci.tools.is_protected`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loompa.aci import ACI
from loompa.aci.tools import TOOL_SPECS, ToolResult
from loompa.llm import Message

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext
    from loompa.mcp import McpSession

PRUNED = "[pruned]"
READ_TOOLS = frozenset({"read_file", "list_dir", "search", "find_symbol"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "delete_file", "fix_lint"})
RUN_TOOLS = frozenset({"run_tests", "run_lint"})
SIGNAL_TOOLS = frozenset({"done", "blocked", "note_learning"})

SPEC_DIRS = (".loompa/specs/",)  # story artifacts: spec, plan, tasks, research
WORKTREE_DOCS = ("docs/",)  # only writable inside a story worktree, never in the main checkout


@dataclass(frozen=True)
class ToolProfile:
    role: str
    tools: frozenset[str]
    # Where write tools may write. None = the story's own `allowed_paths` (the Worker's plan).
    write_paths: tuple[str, ...] | None = ()
    worktree_write_paths: tuple[str, ...] = ()  # extra scope when the root is a story worktree


_DOC_ROLE_TOOLS = READ_TOOLS | {"write_file", "edit_file"}

PROFILES: dict[str, ToolProfile] = {
    "worker": ToolProfile("worker", READ_TOOLS | WRITE_TOOLS | RUN_TOOLS | SIGNAL_TOOLS, None),
    "inspector": ToolProfile("inspector", READ_TOOLS | RUN_TOOLS | {"note_learning"}, ()),
    **{
        role: ToolProfile(role, _DOC_ROLE_TOOLS, SPEC_DIRS, WORKTREE_DOCS)
        for role in ("architect", "product", "product_owner", "analyst", "master")
    },
}
READ_ONLY = ToolProfile("readonly", READ_TOOLS, ())


def profile_for(role: str) -> ToolProfile:
    return PROFILES.get(role, READ_ONLY)


REASON_PROP = {
    "type": "string",
    "description": "One or two sentences: the root cause you found and what this change does.",
}
DIAGNOSIS_MISSING = (
    "error: before the first change of this task, state in `reason` (1-2 sentences) the root "
    "cause you found and what you will change; then repeat the call."
)


class Toolbox:
    """The tools one agent may use in one run: ACI tools filtered by its profile, plus MCP tools.

    With `diagnosis=True` (a Worker fixing a failure or a bug, Fase 7 item 7.11), the write tools
    take a `reason` and the first write of the run is refused without one: the root cause is
    stated before the code changes, in the same call, costing no extra round when given."""

    def __init__(
        self,
        aci: ACI,
        profile: ToolProfile,
        *,
        offered: Iterable[str] | None = None,
        mcp: McpSession | None = None,
        diagnosis: bool = False,
    ):
        self.aci = aci
        self.profile = profile
        wanted = frozenset(offered) if offered is not None else profile.tools
        self.allowed = profile.tools & wanted  # the profile is the ceiling, `offered` narrows it
        self.mcp = mcp
        self.seen_urls: set[str] = set()  # URLs that came back from web tools (source check)
        self.diagnosis_required = diagnosis
        self.diagnosis = ""  # the `reason` of the first write, once given

    # ------------------------------------------------------------------ building
    @classmethod
    def for_role(
        cls,
        ctx: EngineContext,
        role: str,
        root: Path | None = None,
        *,
        offered: Iterable[str] | None = None,
        allowed_paths: list[str] | None = None,
        in_worktree: bool = False,
        mcp: McpSession | None = None,
    ) -> Toolbox:
        profile = profile_for(role)
        if profile.write_paths is None:
            scope = allowed_paths  # the Worker: whatever the Architect's plan allows
        else:
            scope = list(
                profile.write_paths + (profile.worktree_write_paths if in_worktree else ())
            )
        return cls(
            ctx.aci_for(root or ctx.root, allowed_paths=scope), profile, offered=offered, mcp=mcp
        )

    # -------------------------------------------------------------------- using
    def spec(self) -> list[dict[str, Any]]:
        specs = [t for t in TOOL_SPECS if t["name"] in self.allowed]
        if self.diagnosis_required:
            specs = [_with_reason(t) if t["name"] in WRITE_TOOLS else t for t in specs]
        if self.mcp is not None:
            specs += self.mcp.specs()
        return specs

    def offers(self, name: str) -> bool:
        return name in self.allowed or (self.mcp is not None and self.mcp.owns(name))

    @property
    def learnings(self) -> list[dict[str, str]]:
        return self.aci.learnings

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if self.mcp is not None and self.mcp.owns(name):
            result = await self.mcp.call(name, args)
            if result.ok:
                self.seen_urls |= extract_urls(result.output)
            return result
        if name not in self.allowed:
            return ToolResult(
                False,
                f"error: the tool {name} is not available to the {self.profile.role} role",
            )
        if name in WRITE_TOOLS:
            args = dict(args)
            reason = str(args.pop("reason", "") or "").strip()
            if self.diagnosis_required and not self.diagnosis:
                if len(reason) < 12:
                    return ToolResult(False, DIAGNOSIS_MISSING)
                self.diagnosis = reason
        return await self.aci.call(name, args)

    @property
    def used_web(self) -> bool:
        return self.mcp is not None and self.mcp.calls_ok > 0


# ---------------------------------------------------------------------------- helpers


def _with_reason(spec: dict[str, Any]) -> dict[str, Any]:
    params = spec["parameters"]
    return {
        **spec,
        "parameters": {
            **params,
            "properties": {**params.get("properties", {}), "reason": REASON_PROP},
            "required": [*params.get("required", []), "reason"],
        },
    }


def extract_urls(text: str) -> set[str]:
    return {normalize_url(u) for u in re.findall(r"https?://[^\s<>\"')\]]+", text or "")}


def normalize_url(url: str) -> str:
    """Comparison key for a cited source: no fragment, no trailing slash or punctuation."""
    url = url.strip().rstrip(".,;:")
    url = url.split("#", 1)[0]
    return url.rstrip("/").lower()


def prune_tool_history(
    messages: list[Message], *, keep_last: int = 6, max_chars: int = 300, keep_files_chars: int = 0
) -> int:
    """Collapse old tool results into one-line stubs so long tasks stop re-paying for every file
    read. The model keeps a trace of what it did; only the last `keep_last` results stay verbatim.

    With `keep_files_chars`, older `read_file` results also stay verbatim, newest first and up to
    that many characters, while they are still the latest read of their file and nothing wrote
    to it since. Pruning those is what sent the Worker back to read the same file again (`contas`,
    Fase 7 item 7.11): a re-read costs a whole round, which re-sends the full history."""
    tool_idx = [i for i, m in enumerate(messages) if m.role == "tool"]
    older = tool_idx[:-keep_last] if keep_last else tool_idx
    keep: set[int] = set()
    if keep_files_chars and older:
        calls = _calls_by_id(messages)
        latest: set[str] = set()  # files whose latest read (or write) is newer than index i
        budget = keep_files_chars
        for i in reversed(tool_idx):
            name, args = calls.get(messages[i].tool_call_id or "", ("", {}))
            path = str(args.get("path") or "")
            if name in WRITE_TOOLS and path:
                latest.add(path)  # an earlier read of it is stale now
            elif name == "read_file" and path:
                fresh = path not in latest
                latest.add(path)
                if fresh and i in older and len(messages[i].content) <= budget:
                    keep.add(i)
                    budget -= len(messages[i].content)
    pruned = 0
    for i in older:
        m = messages[i]
        if i in keep or len(m.content) <= max_chars or m.content.startswith(PRUNED):
            continue
        first = m.content.strip().splitlines()[0][:120]
        m.content = f"{PRUNED} earlier {m.name or 'tool'} result ({len(m.content)} chars), removed to save context; call again if you need it: {first} …"
        pruned += 1
    return pruned


def _calls_by_id(messages: list[Message]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        c.id: (c.name, c.arguments if isinstance(c.arguments, dict) else {})
        for m in messages
        if m.role == "assistant"
        for c in m.tool_calls or []
    }
