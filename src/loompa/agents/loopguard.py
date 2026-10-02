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
* a write tool that keeps failing gets a hint to switch to another way of editing;
* with `repeat_limit`, the task stops once that many lookups were answered from memory since the
  tree last changed (Fase 8.5): the model is going in circles, and `contas` S-031 T5 went on for
  53 calls and 35 minutes until the tool-call limit. `summary()` is its diagnosis.

Notes ride on the tool result instead of a new user turn: every provider accepts that shape.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from loompa.aci import ACI
from loompa.aci.tools import ToolResult
from loompa.agents.toolbox import LOOKUP_TOOLS, PRUNED, RUN_TOOLS, WRITE_TOOLS
from loompa.llm import Message

REPEAT_PREFIX = "[repeated]"
REREAD_LIMIT = 3  # from the third re-read of a pruned, unchanged result it counts as a repeat
REREAD_NOTE = (
    "You have now read this same unchanged content {n} times in this task: it keeps leaving "
    "your context because you read more than fits. Stop exploring. Write the change with what "
    "you know now (edit the smallest part you are sure of), or call `blocked` saying what is "
    "missing."
)

EXPLORE_NUDGES_TO_STOP = 3  # ignored explore nudges in a row that end the task as a loop
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


def short_call(name: str, args: dict[str, Any]) -> str:
    """`read_file path=src/app.py`: how a repeated call is named in a diagnosis."""
    shown = " ".join(f"{k}={str(v)[:60]}" for k, v in args.items() if k not in ("content", "new"))
    return f"{name} {shown}".strip()


class LoopGuard:
    def __init__(self, aci: ACI | None, *, explore_nudge: int = 0, repeat_limit: int = 0):
        self.aci = aci
        self.explore_nudge = explore_nudge
        self.repeat_limit = repeat_limit  # 0 = never stop the task
        self.calls = 0
        self.repeats = 0
        self.nudges = 0
        self.reads_in_a_row = 0
        self.streak = 0  # lookups answered from memory since the tree last changed
        self.repeated: Counter[str] = Counter()
        self._fails: dict[str, int] = {}
        self._seen: dict[str, _Seen] = {}
        self._version_seen = self._version
        # re-reads of a lookup whose result was pruned, with nothing changed (per call key)
        self._rereads: Counter[str] = Counter()

    @property
    def _version(self) -> int:
        return self.aci.version if self.aci is not None else 0

    def before(self, name: str, args: dict[str, Any]) -> ToolResult | None:
        """A result to use instead of running the tool, when running it would change nothing."""
        if name not in LOOKUP_TOOLS and name not in RUN_TOOLS:
            return None
        prev = self._seen.get(call_key(name, args))
        if prev is None or prev.version != self._version:
            return None
        ago = self.calls + 1 - prev.at
        if name in RUN_TOOLS:
            self._count_repeat(name, args)
            return ToolResult(
                True,
                f"{REPEAT_PREFIX} Nothing in the code changed since you ran this {ago} call(s) "
                f"ago, so the result is the same:\n{prev.output[:2000]}\n"
                "Change the code before running it again, or call `done`/`blocked`.",
            )
        if prev.message is not None and not prev.message.content.startswith(PRUNED):
            self._count_repeat(name, args)
            return ToolResult(
                True,
                f"{REPEAT_PREFIX} You made this exact lookup {ago} call(s) ago and nothing changed "
                "since: the full result is still above in your history. Use it.",
            )
        # The earlier result was pruned from the history: reading again is fair, once or twice.
        # Past that it is the loop of contas Sprint 2 (S-045 T3: 40 rounds, no write, the same
        # 200-line blocks read 5-6 times each because they never fit the pruning budget): it
        # counts as a repeat, and `after` tells the model to write.
        key = call_key(name, args)
        self._rereads[key] += 1
        if self._rereads[key] >= REREAD_LIMIT:
            self._count_repeat(name, args)
        return None

    def _count_repeat(self, name: str, args: dict[str, Any]) -> None:
        self.repeats += 1
        self.streak += 1
        self.repeated[short_call(name, args)] += 1

    @property
    def stuck(self) -> bool:
        """The task should stop: `repeat_limit` lookups answered from memory with no change, or
        three explore nudges ignored in a row (contas Sprint 2, S-047 T3: 52 reads, 4 nudges and
        no write in 40 rounds; the nudges had no consequence and the round limit ended it 12
        minutes later)."""
        repeated = bool(self.repeat_limit) and self.streak >= self.repeat_limit
        return repeated or self.exploring

    @property
    def exploring(self) -> bool:
        return bool(self.explore_nudge) and self.reads_in_a_row >= EXPLORE_NUDGES_TO_STOP * (
            self.explore_nudge
        )

    def summary(self, limit: int = 4) -> str:
        """The calls repeated most, for the diagnosis the next attempt reads."""
        repeated = ", ".join(f"{call} (x{n})" for call, n in self.repeated.most_common(limit))
        if self.exploring:
            lead = f"{self.reads_in_a_row} lookups in a row without writing anything"
            return f"{lead}; {repeated}" if repeated else lead
        return repeated

    def after(
        self, name: str, args: dict[str, Any], result: ToolResult, message: Message | None
    ) -> str:
        """Record a call that ran (or was answered by `before`). Returns guidance to append to
        its result, or ""."""
        self.calls += 1
        if self._version != self._version_seen:  # the call changed the tree: a fresh start
            self._version_seen = self._version
            self.streak = 0
            self._rereads.clear()  # the code changed: reading it again is new information
        repeated = result.output.startswith(REPEAT_PREFIX)
        if (name in LOOKUP_TOOLS or name in RUN_TOOLS) and not repeated and result.ok:
            self._seen[call_key(name, args)] = _Seen(
                self._version, result.output, message, self.calls
            )
        notes: list[str] = []
        if name in LOOKUP_TOOLS and self._rereads.get(call_key(name, args), 0) >= REREAD_LIMIT:
            notes.append(REREAD_NOTE.format(n=self._rereads[call_key(name, args)] + 1))
        if name in LOOKUP_TOOLS:
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
