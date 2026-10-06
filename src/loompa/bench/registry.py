"""Which prompts the bench knows (plan 14.2): every `*SYSTEM` constant of the agents.

A prompt is found, not declared: every module-level string in `loompa.agents` that opens with the
`<!-- role:… -->` marker is a prompt, keyed `<module>.<CONSTANT>` (`deployer.SUMMARY_SYSTEM`). A
new prompt is in the bench the day it is written, and a renamed one is found by its text.

Three things the bench needs from a prompt, all derived here:
- `render`: the text as the code sends it today (`{language}` filled);
- `contract`: the top-level keys and coarse types of its "Respond with JSON only" line — the shape
  the code reads, checked on every answer (a candidate prompt is held to the current contract,
  because the code that consumes the answer did not change with it);
- `identify`: which prompt a system message recorded in a trace came from. Traces keep the text as
  it was sent, weeks of rewrites ago, so the match is by similarity, not equality.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cache

import loompa.agents as agents_pkg

ROLE_MARK = re.compile(r"<!-- role:([a-z_{}]+) -->")
JSON_LINE = "Respond with JSON only"
MIN_SIMILARITY = 0.55  # below this a recorded system message belongs to no known prompt
# Where agents start what they append to a prompt: the explore hint of the roles that read the
# repository first, and the story block of the Worker's cache-friendly prefix.
TAIL_MARKERS = ("\nYou may explore the repository with the read-only tools", "\n## Story ")


@dataclass(frozen=True)
class Prompt:
    key: str  # deployer.SUMMARY_SYSTEM
    module: str  # loompa.agents.deployer
    const: str
    template: str

    @property
    def role(self) -> str:
        m = ROLE_MARK.search(self.template)
        return m.group(1) if m else ""

    def render(self, language: str = "pt-BR", template: str | None = None) -> str:
        """The prompt as the agent sends it. A template that takes other fields than `language`
        (the consult prompt takes `role`) keeps them unfilled rather than failing."""
        text = self.template if template is None else template
        try:
            return text.format(language=language)
        except (KeyError, IndexError):
            return text.replace("{language}", language).replace("{{", "{").replace("}}", "}")

    @property
    def contract(self) -> dict[str, str]:
        return parse_contract(self.render())


@cache
def prompts() -> dict[str, Prompt]:
    found: dict[str, Prompt] = {}
    for info in pkgutil.iter_modules(agents_pkg.__path__):
        name = f"{agents_pkg.__name__}.{info.name}"
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - an optional backend that does not import here
            continue
        for const, value in vars(mod).items():
            if (
                const.isupper()
                and const.endswith("SYSTEM")
                and isinstance(value, str)
                and value.lstrip().startswith("<!-- role:")
            ):
                key = f"{info.name}.{const}"
                found[key] = Prompt(key, name, const, value)
    return dict(sorted(found.items()))


def get(key: str) -> Prompt:
    table = prompts()
    if key in table:
        return table[key]
    matches = [k for k in table if k.lower().endswith(key.lower()) or key.lower() in k.lower()]
    if len(matches) == 1:
        return table[matches[0]]
    raise KeyError(key if not matches else f"{key}: ambíguo ({', '.join(matches)})")


# ----------------------------------------------------------------------------- identification


def _head(text: str, n: int = 900) -> str:
    return re.sub(r"\s+", " ", text[:n]).strip()


def identify(system: str, language: str = "pt-BR") -> tuple[str, float]:
    """The prompt a recorded system message came from, and how sure (0..1). The role marker
    narrows the field; the head of the text decides between the prompts of one role."""
    m = ROLE_MARK.search(system[:200])
    role = m.group(1) if m else None
    head = _head(system)
    best, score = "", 0.0
    for key, p in prompts().items():
        if role and p.role not in (role, "{role}"):
            continue
        r = SequenceMatcher(None, _head(p.render(language)), head, autojunk=False)
        if r.quick_ratio() < score:
            continue
        s = r.ratio()
        if s > score:
            best, score = key, s
    return (best, score) if score >= MIN_SIMILARITY else ("", score)


def tail_after(prompt_text: str, recorded: str) -> str:
    """What the agent appended after the prompt in a recorded system message (the Worker puts the
    story, spec and plan there; tool-using roles the explore hint). The prompt's own text in the
    recording is an older version, so its end is found by alignment, not by length."""
    for marker in TAIL_MARKERS:
        at = recorded.find(marker)
        if at > 0:
            return recorded[at:]
    sm = SequenceMatcher(None, prompt_text, recorded, autojunk=False)
    end = 0
    for a, b, size in sm.get_matching_blocks():
        if size >= 12 and a + size >= len(prompt_text) - 300:
            end = b + size + (len(prompt_text) - (a + size))
    if not end:
        return ""
    return recorded[min(end, len(recorded)) :]


# ----------------------------------------------------------------------------- the JSON contract


def parse_contract(prompt_text: str) -> dict[str, str]:
    """Top-level keys of the JSON shape after "Respond with JSON only", with a coarse type each:
    str | bool | int | list | object | enum. Empty when the prompt has no such line."""
    at = prompt_text.find(JSON_LINE)
    if at < 0:
        return {}
    start = prompt_text.find("{", at)
    if start < 0:
        return {}
    depth, end = 0, -1
    for i in range(start, len(prompt_text)):
        ch = prompt_text[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return {}
    body = prompt_text[start + 1 : end]
    keys: dict[str, str] = {}
    depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == '"' and depth == 0:
            close = body.find('"', i + 1)
            if close < 0:
                break
            name = body[i + 1 : close]
            rest = body[close + 1 :].lstrip()
            if rest.startswith(":"):
                value = rest[1:].lstrip()
                keys[name] = _kind(value)
                i = close + 1
                continue
            i = close
        i += 1
    return keys


def _kind(value: str) -> str:
    if value.startswith("["):
        return "list"
    if value.startswith("{"):
        return "object"
    if value.startswith('"'):
        options = re.match(r'"[^"]*"(?:\s*\|\s*"[^"]*")*', value)
        names = re.findall(r'"([^"]*)"', options.group(0)) if options else []
        return "enum:" + "|".join(names) if len(names) > 1 else "str"
    word = re.match(r"[a-z]+", value)
    w = word.group(0) if word else ""
    return {"str": "str", "bool": "bool", "int": "int", "float": "int"}.get(w, "any")
