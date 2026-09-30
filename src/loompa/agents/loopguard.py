"""Loop discipline for tool-using agents (Fase 7, item 7.11).

Measured in `contas` (Sprint 1): the Worker spent most of its rounds reading. Tasks ran dozens of
`read_file`/`search`/`list_dir` calls in a row without a single edit, and one fix pass ran the same
test suite thirty times with nothing changed in between (each run ~30 s, each round re-sending
the whole history). The guard answers those in code, never by asking the model to behave:

* a read or a run repeated while nothing changed in the tree (`ACI.version`) is answered from
  memory: a read whose result is still verbatim in the history points back at it, a run repeats
  its previous result without running the command again;
* a long streak of reads with no change gets a note appended to the tool result, asking for the
  diagnosis and the edit (or `blocked`) instead of more exploring;
* a write tool that keeps failing gets a hint to switch to another way of editing.

Notes ride on the tool result instead of a new user turn: every provider accepts that shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loompa.aci import ACI
from loompa.aci.tools import ToolResult
from loompa.agents.toolbox import PRUNED, READ_TOOLS, RUN_TOOLS, WRITE_TOOLS
from loompa.llm import Message

REPEAT_PREFIX = "[repeated]"

EXPLORE_NUDGE = (
    "[guidance] You made {n} lookups in a row without changing anything. What you read is in "
    "your history: stop exploring. State in one sentence the cause or what you will change, and "
    "make the change now; if you lack information only a person can give, call `blocked`."
)

WRITE_FAIL_HINT = {
    "apply_patch": (
        "[guidance] apply_patch failed {n} times in a row. Read the current lines with read_file "
        "and use edit_file with an exact snippet, or write_file to rewrite a small file."
    ),
    "edit_file": (
        "[guidance] edit_file failed {n} times in a row. Read the lines again with read_file and "
        "copy `old` exactly as it is, with one more line of context."
    ),
}
GENERIC_FAIL_HINT = (
    "[guidance] {tool} failed {n} times in a row: read the error again and change approach."
)


@dataclass
class _Seen:
    version: int
    output: str
    message: Message | None
    at: int  # the call number it was made at


def call_key(name: str, args: dict[str, Any]) -> str:
    return name + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


class LoopGuard:
    def __init__(self, aci: ACI | None, *, explore_nudge: int = 0):
        self.aci = aci
        self.explore_nudge = explore_nudge
        self.calls = 0
        self.repeats = 0
        self.nudges = 0
        self.reads_in_a_row = 0
        self._fails: dict[str, int] = {}
        self._seen: dict[str, _Seen] = {}

    @property
    def _version(self) -> int:
        return self.aci.version if self.aci is not None else 0

    def before(self, name: str, args: dict[str, Any]) -> ToolResult | None:
        """A result to use instead of running the tool, when running it would change nothing."""
        if name not in READ_TOOLS and name not in RUN_TOOLS:
            return None
        prev = self._seen.get(call_key(name, args))
        if prev is None or prev.version != self._version:
            return None
        ago = self.calls + 1 - prev.at
        if name in RUN_TOOLS:
            self.repeats += 1
            return ToolResult(
                True,
                f"{REPEAT_PREFIX} Nothing in the code changed since you ran this {ago} call(s) "
                f"ago, so the result is the same:\n{prev.output[:2000]}\n"
                "Change the code before running it again, or call `done`/`blocked`.",
            )
        if prev.message is not None and not prev.message.content.startswith(PRUNED):
            self.repeats += 1
            return ToolResult(
                True,
                f"{REPEAT_PREFIX} You made this exact lookup {ago} call(s) ago and nothing changed "
                "since: the full result is still above in your history. Use it.",
            )
        return None  # the earlier result was pruned from the history: reading again is fair

    def after(
        self, name: str, args: dict[str, Any], result: ToolResult, message: Message | None
    ) -> str:
        """Record a call that ran (or was answered by `before`). Returns guidance to append to
        its result, or ""."""
        self.calls += 1
        repeated = result.output.startswith(REPEAT_PREFIX)
        if (name in READ_TOOLS or name in RUN_TOOLS) and not repeated and result.ok:
            self._seen[call_key(name, args)] = _Seen(
                self._version, result.output, message, self.calls
            )
        notes: list[str] = []
        if name in READ_TOOLS:
            self.reads_in_a_row += 1
            if self.explore_nudge and self.reads_in_a_row % self.explore_nudge == 0:
                self.nudges += 1
                notes.append(EXPLORE_NUDGE.format(n=self.reads_in_a_row))
        elif name in WRITE_TOOLS or name in RUN_TOOLS:
            self.reads_in_a_row = 0
        if name in WRITE_TOOLS:
            streak = 0 if result.ok else self._fails.get(name, 0) + 1
            self._fails[name] = streak
            if streak and streak % 3 == 0:
                hint = WRITE_FAIL_HINT.get(name, GENERIC_FAIL_HINT)
                notes.append(hint.format(n=streak, tool=name))
        return "\n\n".join(notes)
