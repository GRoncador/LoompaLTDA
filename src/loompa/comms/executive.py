"""Executive (non-technical) communication format for the Founder inbox.

Golden rule from the brief: every message to the Founder must be plain business language,
structured as **simple context**, **business impact** and **recommended options**. Stack
traces, raw errors and terminal noise are filtered out before anything reaches the inbox.

Two layers enforce this:

1. `sanitize_for_founder` - deterministic scrubbing (traces, paths, hex ids, jargon).
2. `audit_executive_text` - a gate that lists remaining violations; the Master Loompa must
   rewrite (with an LLM when available) until the audit is clean.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MessageKind(StrEnum):
    DECISION = "decision"  # founder must choose an option
    BLOCKED = "blocked"  # task blocked, needs guidance
    DELIVERY = "delivery"  # something is ready for inspection
    INFO = "info"  # FYI, no action required
    FINANCE = "finance"  # budget alerts and daily cost summary
    KAIZEN = "kaizen"  # discoveries / improvements catalogued


class MessageStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    ARCHIVED = "archived"


class Option(BaseModel):
    key: str
    label: str
    description: str = ""
    recommended: bool = False


class Decision(BaseModel):
    """One independent question inside a message (e.g. what to do with a suggested card).
    The founder answers each on its own; the message's own `options` stay the main decision."""

    id: str  # the backlog card it is about
    title: str
    context: str = ""
    options: list[Option] = Field(default_factory=list)
    chosen: str | None = None  # option key, once decided


class FounderAnswer(BaseModel):
    option_key: str | None = None
    text: str | None = None
    decisions: dict[str, str] = Field(default_factory=dict)  # decision id -> option key
    answered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FounderMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    factory: str = ""
    story_id: str | None = None
    kind: MessageKind = MessageKind.INFO
    status: MessageStatus = MessageStatus.PENDING
    sender: str = "Master Loompa"
    title: str  # one-sentence summary
    context: str  # simple context
    impact: str = ""  # business impact
    options: list[Option] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)  # independent side decisions
    allow_free_text: bool = True
    technical_ref: str | None = None  # pointer to the filtered technical log, never shown inline
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answer: FounderAnswer | None = None

    @property
    def requires_action(self) -> bool:
        return self.kind in (MessageKind.DECISION, MessageKind.BLOCKED) and (
            self.status == MessageStatus.PENDING
        )

    def executive_audit(self) -> list[ExecutiveViolation]:
        text = "\n".join(
            [
                self.title,
                self.context,
                self.impact,
                *(o.description for o in self.options),
                *(f"{d.title}\n{d.context}" for d in self.decisions),
                *(o.description for d in self.decisions for o in d.options),
            ]
        )
        return audit_executive_text(text)


class ExecutiveViolation(BaseModel):
    rule: str
    excerpt: str


# ----------------------------------------------------------------------------- filters

_TRACEBACK_LINE = re.compile(
    r"^\s*(Traceback \(most recent call last\)|File \".*\", line \d+|at .+\(.+:\d+:\d+\)|\s+\^+\s*$)"
)
_ERROR_CLASS = re.compile(r"\b[A-Z][A-Za-z]+(Error|Exception|Warning)\b(:.*)?")
_HEX_ID = re.compile(r"\b0x[0-9a-fA-F]{6,}\b|\b[0-9a-f]{32,}\b")
_PATH = re.compile(r"(?<![\w/])(/[\w.\-]+){3,}(:\d+)?")
_FILE_LINE = re.compile(r"\b[\w\-/]+\.(py|ts|tsx|js|jsx|go|rs|java|rb):\d+\b")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TRAILING_NOISE = re.compile(r"\n{3,}")

# Jargon -> plain language (pt-BR and en variants kept short on purpose).
_JARGON: dict[str, str] = {
    r"\bstack ?trace\b": "detalhe técnico",
    r"\bnull ?pointer\b": "dado ausente",
    r"\bNoneType\b": "dado ausente",
    r"\bsegfault\b": "falha do programa",
    r"\bmerge conflict\b": "conflito entre versões do código",
    r"\bregression\b": "algo que funcionava deixou de funcionar",
    r"\bregressão\b": "algo que funcionava deixou de funcionar",
    r"\bflaky test\b": "verificação instável",
    r"\bCI pipeline\b": "verificação automática",
    r"\bendpoint\b": "ponto de acesso da API",
    r"\brace condition\b": "conflito de execução simultânea",
    r"\bdeadlock\b": "travamento",
    r"\bOOM\b": "falta de memória",
    r"\b5\d\d\b(?= error)": "erro do servidor",
}

_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "stack_trace",
        re.compile(r"Traceback \(most recent call last\)|^\s*File \".*\", line \d+", re.M),
    ),
    ("raw_exception", re.compile(r"\b[A-Z][A-Za-z]+(Error|Exception)\b:")),
    ("file_line_ref", _FILE_LINE),
    ("absolute_path", re.compile(r"(?<![\w/])/(Users|home|var|tmp|opt)/[\w./\-]+")),
    ("hex_identifier", _HEX_ID),
    ("terminal_noise", re.compile(r"\$ (pytest|npm|pip|uv|ruff|git) ")),
    ("shell_prompt", re.compile(r"^\s*[\$#>] ", re.M)),
    ("ansi_escape", _ANSI),
]

MAX_FOUNDER_CHARS = 1200


def sanitize_for_founder(text: str, *, max_chars: int = MAX_FOUNDER_CHARS) -> str:
    """Deterministically strip technical noise from `text` for Founder consumption."""
    text = _ANSI.sub("", text or "")
    kept: list[str] = []
    for line in text.splitlines():
        if _TRACEBACK_LINE.match(line):
            continue
        line = _ERROR_CLASS.sub("um erro técnico", line)
        line = _FILE_LINE.sub("um arquivo do sistema", line)
        line = _PATH.sub("um arquivo do sistema", line)
        line = _HEX_ID.sub("(id)", line)
        for pattern, plain in _JARGON.items():
            line = re.sub(pattern, plain, line, flags=re.I)
        kept.append(line.rstrip())
    out = _TRAILING_NOISE.sub("\n\n", "\n".join(kept)).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return out


def audit_executive_text(text: str) -> list[ExecutiveViolation]:
    """Return every violation of the executive-language rule found in `text`."""
    violations: list[ExecutiveViolation] = []
    for rule, pattern in _FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text or ""):
            violations.append(ExecutiveViolation(rule=rule, excerpt=match.group(0)[:80]))
    if len(text or "") > MAX_FOUNDER_CHARS * 2:
        violations.append(ExecutiveViolation(rule="too_long", excerpt=f"{len(text)} chars"))
    return violations


# --------------------------------------------------------------------------- composers


def compose_blocked_message(
    *,
    factory: str,
    story_id: str,
    story_title: str,
    reason: str,
    impact: str = "",
    options: list[Option] | None = None,
    technical_ref: str | None = None,
) -> FounderMessage:
    """Standard BLOCKED_AWAITING_INPUT message with sane default options."""
    opts = options or [
        Option(key="retry", label="Tentar novamente com outra abordagem", recommended=True),
        Option(key="skip", label="Deixar para depois (volta ao backlog)"),
        Option(key="drop", label="Cancelar esta entrega"),
    ]
    return FounderMessage(
        factory=factory,
        story_id=story_id,
        kind=MessageKind.BLOCKED,
        title=f"Precisamos da sua orientação em “{story_title}”",
        context=sanitize_for_founder(reason),
        impact=sanitize_for_founder(
            impact or "Enquanto isso, as demais entregas seguem normalmente."
        ),
        options=opts,
        technical_ref=technical_ref,
    )


def compose_decision_message(
    *,
    factory: str,
    story_id: str | None,
    question: str,
    context: str,
    options: list[Option],
    impact: str = "",
) -> FounderMessage:
    return FounderMessage(
        factory=factory,
        story_id=story_id,
        kind=MessageKind.DECISION,
        title=sanitize_for_founder(question, max_chars=160),
        context=sanitize_for_founder(context),
        impact=sanitize_for_founder(impact),
        options=options,
    )


CARD_OPTIONS = ("sprint", "backlog", "drop")


def card_decision(card: dict) -> Decision:
    """The founder's choice about one suggested card (a finding raised while delivering)."""
    urgent = int(card.get("priority") or 500) <= 250  # bugs come in at 250
    return Decision(
        id=str(card["id"]),
        title=sanitize_for_founder(f"Achado: {card.get('title', '')}", max_chars=160),
        context=sanitize_for_founder(str(card.get("detail") or ""), max_chars=300),
        options=[
            Option(key="sprint", label="Incluir no próximo sprint", recommended=urgent),
            Option(key="backlog", label="Manter no backlog", recommended=not urgent),
            Option(key="drop", label="Descartar"),
        ],
    )


def compose_delivery_message(
    *,
    factory: str,
    story_id: str,
    story_title: str,
    summary: str,
    pr_url: str | None = None,
    cost_usd: float = 0.0,
    cards: list[dict] | None = None,
) -> FounderMessage:
    """The delivery plus, as independent decisions, every card the work suggested."""
    where = (
        f"Pull request: {pr_url}"
        if pr_url
        else "A entrega está no branch da história, pronta para inspeção."
    )
    return FounderMessage(
        factory=factory,
        story_id=story_id,
        kind=MessageKind.DELIVERY,
        title=f"Entrega pronta para revisão: “{story_title}”",
        context=sanitize_for_founder(summary),
        impact=f"{where} Custo desta entrega: US$ {cost_usd:.2f}.",
        options=[
            Option(key="approve", label="Aprovar e liberar", recommended=True),
            Option(key="changes", label="Pedir ajustes"),
        ],
        decisions=[card_decision(c) for c in cards or []],
    )
