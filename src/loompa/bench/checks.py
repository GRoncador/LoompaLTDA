"""Checks in code for one answer of the prompt bench (plan 14.2): the part that needs no judge.

- the answer is a JSON object and it keeps the prompt's contract (keys, coarse types, enum values)
  — the contract of the *current* prompt, because the code reading the answer is the current one;
- the text the founder reads passes `audit_executive_text`, is Portuguese, is not JSON inside a
  string (the SP-009 sprint report came back that way) and keeps the sentence limit its prompt
  sets;
- the decision fields agree with what the factory decided when the call was real, and with the
  story's known outcome where one exists (the complexity a story ended with).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loompa.bench.registry import Prompt
from loompa.comms.executive import audit_executive_text
from loompa.llm.providers import extract_json

# Fields of each prompt's answer that the founder reads, and the sentence limit the prompt sets.
FOUNDER_TEXT: dict[str, tuple[tuple[str, ...], int | None]] = {
    "deployer.SUMMARY_SYSTEM": (("summary",), 4),
    "master.SPRINT_REPORT_SYSTEM": (("summary",), 5),
    "master.EXEC_SYSTEM": (("title", "context", "impact", "options"), None),
    "product_owner.PROPOSE_SYSTEM": (("reply",), 5),
    "product_owner.SPLIT_SYSTEM": (("reply",), 4),
    "product_owner.REVIEW_CARDS_SYSTEM": (("reply",), None),
    "product_owner.TRIAGE_REQUEST_SYSTEM": (("reason",), None),
}

# Fields that carry the decision, compared with the recorded answer.
DECISIONS: dict[str, tuple[str, ...]] = {
    "master.CLASSIFY_SYSTEM": ("kind", "complexity"),
    "product_owner.REVIEW_SYSTEM": ("approved",),
    "product_owner.PLAN_REVIEW_SYSTEM": ("approved",),
    "product_owner.RESEARCH_REVIEW_SYSTEM": ("approved",),
    "product_owner.TRIAGE_REQUEST_SYSTEM": ("admit", "reason_code"),
    "worker.DOD_SYSTEM": ("complete",),
    "deployer.DIAGNOSE_SYSTEM": ("action",),
    "architect.LESSON_SYSTEM": ("generalizable",),
    "inspector.JUDGE_SYSTEM": ("criteria.pass",),
    "product_owner.TRIAGE_FINDINGS_SYSTEM": ("findings.duplicate_of",),
    "architect.SIMPLE_SYSTEM": ("not_simple",),
}

# Common English function words: Portuguese text carries almost none of them.
_EN = re.compile(
    r"\b(the|and|with|this|that|was|were|have|has|story|which|from|will|should|would)\b", re.I
)
_PT = re.compile(r"\b(o|a|os|as|de|do|da|que|com|para|não|uma|um|está|foi|em|no|na)\b", re.I)


@dataclass
class Checked:
    json_ok: bool = False
    contract_ok: bool = False
    contract_errors: list[str] = field(default_factory=list)
    founder_ok: bool | None = None  # None: the prompt has no founder text
    founder_errors: list[str] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)
    first_tools: list[str] | None = None  # `first` mode only: what it did first

    @property
    def ok(self) -> bool:
        return self.json_ok and self.contract_ok and self.founder_ok is not False


def parse(text: str) -> dict[str, Any] | None:
    try:
        data = extract_json(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


_TYPES = {
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "int": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def contract_errors(data: dict[str, Any], contract: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key, kind in contract.items():
        if key not in data:
            errors.append(f"missing:{key}")
            continue
        value = data[key]
        if value is None and kind in ("str", "list"):
            # the agents read `data.get(key) or ""`: a null text or list is an empty one, not a
            # broken contract (the Pre-flight's question and options, when nothing is irreversible)
            continue
        fits = _TYPES.get(kind)
        if fits is not None and not fits(value):
            errors.append(f"type:{key}")
        elif kind.startswith("enum:") and str(value) not in kind[5:].split("|"):
            errors.append(f"enum:{key}={str(value)[:20]}")
    return errors


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.strip()) > 3])


def is_portuguese(text: str) -> bool:
    words = len(text.split())
    if words < 6:
        return True
    return len(_PT.findall(text)) >= 2 * len(_EN.findall(text))


def founder_errors(data: dict[str, Any], fields: tuple[str, ...], limit: int | None) -> list[str]:
    errors: list[str] = []
    for name in fields:
        value = data.get(name)
        texts = [str(v) for v in value] if isinstance(value, list) else [str(value or "")]
        for text in texts:
            if not text.strip():
                errors.append(f"empty:{name}")
                continue
            if text.lstrip().startswith(("{", "[")):
                errors.append(f"json_inside:{name}")
            for v in audit_executive_text(text):
                errors.append(f"audit:{name}:{v.rule}")
            if not is_portuguese(text):
                errors.append(f"not_pt:{name}")
            if re.search(r"\b(tarefa|task)\s*T?\d+\s*:", text, re.I):
                errors.append(f"task_list:{name}")
            if limit and sentences(text) > limit:
                errors.append(f"sentences:{name}={sentences(text)}>{limit}")
    return errors


def decision(prompt_key: str, data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    out: dict[str, Any] = {}
    for name in DECISIONS.get(prompt_key, ()):
        if "." in name:
            outer, inner = name.split(".", 1)
            items = data.get(outer) or []
            out[name] = [i.get(inner) if isinstance(i, dict) else None for i in items]
        else:
            out[name] = data.get(name)
    return out


def check(prompt: Prompt, mode: str, text: str, tool_calls: list[dict[str, Any]]) -> Checked:
    result = Checked()
    if mode == "first":
        result.first_tools = [str(c.get("name")) for c in tool_calls]
        result.json_ok = result.contract_ok = True
        return result
    data = parse(text)
    result.json_ok = data is not None
    if data is None:
        result.contract_errors = ["no_json"]
        if prompt.key in FOUNDER_TEXT:
            result.founder_ok = False
        return result
    result.contract_errors = contract_errors(data, prompt.contract)
    result.contract_ok = not result.contract_errors
    if prompt.key in FOUNDER_TEXT:
        fields, limit = FOUNDER_TEXT[prompt.key]
        result.founder_errors = founder_errors(data, fields, limit)
        result.founder_ok = not result.founder_errors
    result.decision = decision(prompt.key, data)
    return result
