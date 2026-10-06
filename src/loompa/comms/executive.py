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
    sprint_id: str | None = None  # a note about a sprint opens its report in the Sprints tab
    # screenshots of the page that go with a delivery (`desktop.png`, `mobile.png`), served by
    # the dashboard from the story's shots folder under the hub (ADR-0030)
    images: list[str] = Field(default_factory=list)
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
                *(f"{o.label}\n{o.description}" for o in self.options),
                *(f"{d.title}\n{d.context}" for d in self.decisions),
                *(f"{o.label}\n{o.description}" for d in self.decisions for o in d.options),
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
# A project file named by path or name, without a line ("src/contas/cli.py", "package.json"): contas
# Sprint 2 put "git checkout -- src/contas/cli.py" in a founder option and the audit let it through.
_FILE_NAME = re.compile(
    r"(?<![\w@.])[\w\-]+(?:/[\w.\-]+)*\.(?:py|pyi|ts|tsx|js|jsx|mjs|go|rs|java|rb|toml|json|ya?ml|cfg|ini|lock|sql|sh)\b"
)
# A name from the code: a constant (FAIXA_RETORNO_Y) or a snake_case identifier. Prose in any
# language has no underscore inside a word. A line reference ("linhas 1959-1963") goes with it:
# both reached the founder in a blocked message on 06/10 (SP-010).
_CODE_NAME = re.compile(r"(?<![\w`])`?[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+`?(?![\w`])")
_LINE_REF = re.compile(r",?\s*\b(?:linhas?|lines?)\s+\d+(?:\s*[-–]\s*\d+)?", re.I)
# The noun the writer already put before a name the sanitizer replaced: "o arquivo src/x.js"
# became "o arquivo um arquivo do projeto".
_DOUBLED = re.compile(
    r"\b(?:(o|um|no|do|ao|num|dum)\s+)?(?:arquivo|file)\s+um (arquivo do (?:projeto|sistema))",
    re.I,
)
_ARTICLE = {"no": "num ", "num": "num ", "do": "de um ", "dum": "de um ", "ao": "a um "}


def _undouble(match: re.Match[str]) -> str:
    lead = _ARTICLE.get((match.group(1) or "").lower(), "um ")
    return lead + match.group(2)


_GIT_COMMAND = re.compile(
    r"\bgit (?:checkout|diff|reset|revert|restore|commit|push|pull|merge|rebase|stash|add|log|status)\b"
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TRAILING_NOISE = re.compile(r"\n{3,}")

# The factory's own words, which the founder never needs (masculine nouns, so the article the
# sentence already has still agrees): one list for the audit, the scrubber
# and the factory's jargon detector (plan 8.6.7; a S-041 option said "versão guardada no branch"
# after the detector already knew the word and the audit did not).
TECH_WORDS: dict[str, str] = {
    r"\bworktrees?\b": "espaço de trabalho",
    r"\bbranch(?:es)?\b": "ramo de trabalho",
    r"\bmerges?\b": "envio à versão principal",
    r"\bcommits?\b": "registro",
    r"\bchecklists?\b": "roteiro de verificação",
    r"\brebase\b": "realinhamento",
    r"\bpytest\b": "testes",
    r"\blint\b": "verificador de estilo",
    r"\bstack ?trace\b": "detalhe técnico",
    r"\bdiffs?\b": "comparativo",
    r"\bpull requests?\b": "pedido de revisão",
    r"\bPRs?\b": "pedido de revisão",
}
TECH_WORD = re.compile("|".join(TECH_WORDS), re.I)

# Jargon -> plain language (pt-BR and en variants kept short on purpose).
_JARGON: dict[str, str] = {
    # before the single words: "merge conflict" reads better whole
    r"\bmerge conflict\b": "conflito entre versões do código",
    r"\bnull ?pointer\b": "dado ausente",
    r"\bNoneType\b": "dado ausente",
    r"\bsegfault\b": "falha do programa",
    r"\bregression\b": "algo que funcionava deixou de funcionar",
    r"\bregressão\b": "algo que funcionava deixou de funcionar",
    r"\bflaky test\b": "verificação instável",
    r"\bCI pipeline\b": "verificação automática",
    r"\bendpoint\b": "ponto de acesso da API",
    r"\brace condition\b": "conflito de execução simultânea",
    r"\bdeadlock\b": "travamento",
    r"\bOOM\b": "falta de memória",
    r"\b5\d\d\b(?= error)": "erro do servidor",
    # a plan's task ids ("T1") are the factory's bookkeeping, not the founder's (contas Sprint 2:
    # "Bloqueio na criação de arquivo de testes para a tarefa T1")
    r"\btarefa T(\d{1,3})\b": r"tarefa \1",
    r"\bT(\d{1,3})\b": r"tarefa \1",
    **TECH_WORDS,
}

_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "stack_trace",
        re.compile(r"Traceback \(most recent call last\)|^\s*File \".*\", line \d+", re.M),
    ),
    ("raw_exception", re.compile(r"\b[A-Z][A-Za-z]+(Error|Exception)\b:")),
    ("file_line_ref", _FILE_LINE),
    ("file_name", _FILE_NAME),
    ("git_command", _GIT_COMMAND),
    ("absolute_path", re.compile(r"(?<![\w/])/(Users|home|var|tmp|opt)/[\w./\-]+")),
    ("hex_identifier", _HEX_ID),
    ("task_id", re.compile(r"\bT\d{1,3}\b")),
    ("code_name", _CODE_NAME),
    ("line_ref", _LINE_REF),
    ("jargon", TECH_WORD),
    ("terminal_noise", re.compile(r"\$ (pytest|npm|pip|uv|ruff|git) ")),
    ("shell_prompt", re.compile(r"^\s*[\$#>] ", re.M)),
    ("ansi_escape", _ANSI),
]

MAX_FOUNDER_CHARS = 1200


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` at a word boundary, with "…" when cut. A cut in the middle of a word
    reached the founder as "tests/fim_de_jogo_lcd.t", a path the audit no longer recognised, and
    option labels ended in "para a" (12.1)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:-—") + "…"


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
        line = _FILE_NAME.sub("um arquivo do projeto", line)
        line = _DOUBLED.sub(_undouble, line)
        line = _LINE_REF.sub("", line)
        line = _CODE_NAME.sub("um valor do código", line)
        line = _GIT_COMMAND.sub("um comando técnico", line)
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


CARD_OPTIONS = ("backlog", "sprint", "drop")


def card_decision(card: dict) -> Decision:
    """The founder's choice about one suggested card (a finding raised while delivering).

    The next sprint is the Sprint Meeting's call (ADR-0018), so a finding never offers it: it
    goes to the backlog (the default), is dropped or, when the story that raised it belongs to
    the running sprint (`card["sprint"]`), joins that sprint. Joining is recommended only for a
    collateral bug of this work; anything broader is better weighed with the rest of the backlog.
    """
    sprint = str(card.get("sprint") or "")
    urgent = bool(sprint) and int(card.get("priority") or 500) <= 250  # bugs come in at 250
    options = [Option(key="backlog", label="Enviar ao backlog", recommended=not urgent)]
    if sprint:
        options.append(
            Option(key="sprint", label=f"Incluir neste sprint ({sprint})", recommended=urgent)
        )
    options.append(Option(key="drop", label="Ignorar"))
    return Decision(
        id=str(card["id"]),
        title=sanitize_for_founder(f"Achado: {card.get('title', '')}", max_chars=160),
        context=sanitize_for_founder(str(card.get("detail") or ""), max_chars=300),
        options=options,
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
        f"Pedido de revisão: {pr_url}"
        if pr_url
        else "A entrega está guardada à parte, sem mexer no produto, pronta para inspeção."
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


def compose_research_message(
    *,
    factory: str,
    story_id: str,
    story_title: str,
    summary: str,
    recommendation: str,
    limitations: list[str],
    sources: int,
    cost_usd: float = 0.0,
    cards: list[dict] | None = None,
    concerns: str = "",
) -> FounderMessage:
    """A finished research: what was found, what we recommend, what we could not check, plus
    every follow-up the research suggests as its own decision."""
    parts = [sanitize_for_founder(summary, max_chars=550)]
    if recommendation.strip():
        parts.append("Recomendação: " + sanitize_for_founder(recommendation, max_chars=330))
    if limitations:
        parts.append("Limitações: " + sanitize_for_founder(" ".join(limitations), max_chars=260))
    if concerns.strip():
        parts.append("Ressalvas da revisão: " + sanitize_for_founder(concerns, max_chars=160))
    cited = f"{sources} fonte(s) verificada(s). " if sources else ""
    return FounderMessage(
        factory=factory,
        story_id=story_id,
        kind=MessageKind.DELIVERY,
        sender="Analyst Loompa",
        title=f"Pesquisa pronta: “{story_title}”",
        context="\n\n".join(p for p in parts if p),
        impact=(
            "Nada foi alterado no produto. O relatório completo, com as fontes, está na história "
            f"no painel. {cited}Custo desta pesquisa: US$ {cost_usd:.2f}."
        ),
        options=[
            Option(key="approve", label="Aprovar a pesquisa", recommended=True),
            Option(key="changes", label="Pedir mais aprofundamento"),
        ],
        decisions=[card_decision(c) for c in cards or []],
    )
