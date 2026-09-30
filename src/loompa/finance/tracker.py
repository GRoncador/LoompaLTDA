"""Finance Loompa (CFO): exact USD accounting per call, story and day; budget alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loompa.comms import FounderMessage, MessageKind, Option
from loompa.config.schema import BudgetConfig, LoompaConfig
from loompa.store import Store


def today_start_iso() -> str:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def month_start_iso() -> str:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def week_start_iso() -> str:
    """Monday 00:00 UTC of the current week: the founder reads a week as Monday to Sunday."""
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(days=now.weekday())).isoformat()


def period_start_iso(period: str) -> str:
    return month_start_iso() if period == "monthly" else week_start_iso()


PERIOD_LABEL = {"weekly": "semana", "monthly": "mês"}


def period_label(period: str) -> str:
    return PERIOD_LABEL.get(period, PERIOD_LABEL["weekly"])


@dataclass
class UsageRecord:
    agent: str
    role: str
    provider: str
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    duration_ms: int = 0
    story_id: str | None = None
    cost_usd: float = 0.0
    reported_cost: float | None = None  # what the provider billed, when it says (OpenRouter)
    served_by: str = ""  # the provider behind a router that ran the call
    finish_reason: str = ""
    span_id: str = ""  # the call's span in the story's trace
    cost_source: str = ""  # reported | table


@dataclass
class BudgetStatus:
    """Where the factory stands inside the current budget period."""

    period: str  # weekly | monthly
    period_cost_usd: float
    today_cost_usd: float
    cap_usd: float
    fraction: float
    warn: bool
    over: bool  # the cap is spent, whatever the factory does about it
    exhausted: bool  # ...and the answer is to stop dispatching
    downgrade: bool  # ...and the answer is to put every role on free models

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.period_cost_usd)

    @property
    def period_label(self) -> str:
        return period_label(self.period)


# Below this, what tools read is noise next to the prompts themselves.
TOOL_TOKENS_WORTH_A_NOTE = 20_000


class CostTracker:
    def __init__(self, store: Store, config: LoompaConfig, factory: str):
        self.store = store
        self.config = config
        self.factory = factory

    @property
    def _warned_key(self) -> str:
        """One alert per budget period: a new week (or month) starts a new key."""
        now = datetime.now(UTC)
        if self.config.budget.period == "monthly":
            return f"finance:warned:{now:%Y-%m}"
        monday = now - timedelta(days=now.weekday())
        return f"finance:warned:{monday:%Y-W%V}"

    # ----------------------------------------------------------------- pricing
    def cost_of(
        self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
    ) -> float:
        price = self.config.price_for(model)
        billable_input = max(0, input_tokens - cached_tokens)
        return round(
            (
                billable_input * price.input
                + cached_tokens * price.cached_input
                + output_tokens * price.output
            )
            / 1_000_000,
            6,
        )

    def record(self, rec: UsageRecord) -> float:
        """Meter one call. The provider's own bill wins over the price table: OpenRouter's
        catalogue shows one of the providers that serve a model, and the one that served the
        call may charge 20x less or more. The table stays for providers that do not say."""
        if rec.reported_cost is not None:
            rec.cost_usd, rec.cost_source = round(rec.reported_cost, 6), "reported"
        else:
            rec.cost_usd = self.cost_of(
                rec.model, rec.input_tokens, rec.output_tokens, rec.cached_tokens
            )
            rec.cost_source = "table"
        self.store.record_usage(
            factory=self.factory,
            story_id=rec.story_id,
            agent=rec.agent,
            role=rec.role,
            provider=rec.provider,
            model=rec.model,
            tier=rec.tier,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cached_tokens=rec.cached_tokens,
            cost_usd=rec.cost_usd,
            duration_ms=rec.duration_ms,
            served_by=rec.served_by,
            finish_reason=rec.finish_reason,
            cost_source=rec.cost_source,
            span_id=rec.span_id,
        )
        if rec.story_id:
            story = self.store.get_story(rec.story_id)
            if story:
                self.store.update_story(
                    rec.story_id, cost_usd=round(story["cost_usd"] + rec.cost_usd, 6)
                )
        return rec.cost_usd

    # ------------------------------------------------------------------ budget
    def status(self, budget: BudgetConfig | None = None) -> BudgetStatus:
        b = budget or self.config.budget
        spent = float(
            self.store.usage_totals(self.factory, since_iso=period_start_iso(b.period))["cost_usd"]
        )
        today = float(
            self.store.usage_totals(self.factory, since_iso=today_start_iso())["cost_usd"]
        )
        fraction = (spent / b.cap_usd) if b.cap_usd else 0.0
        over = fraction >= 1.0
        return BudgetStatus(
            period=b.period,
            period_cost_usd=round(spent, 4),
            today_cost_usd=round(today, 4),
            cap_usd=b.cap_usd,
            fraction=round(fraction, 4),
            warn=fraction >= b.warn_at_fraction,
            over=over,
            exhausted=over and b.on_exceed == "pause",
            downgrade=over and b.on_exceed == "tier3",
        )

    def maybe_alert(self) -> FounderMessage | None:
        """Emit one executive alert per period when the warn threshold is crossed."""
        st = self.status()
        if not st.warn or self.store.get(self._warned_key):
            return None
        self.store.set(self._warned_key, "1")
        pct = int(st.fraction * 100)
        label = st.period_label
        this = "esta" if label == "semana" else "este"
        next_one = "a próxima semana" if label == "semana" else "o próximo mês"
        pauses = self.config.budget.on_exceed == "pause"
        bump = (5.0, 10.0) if st.period == "weekly" else (10.0, 30.0)
        msg = FounderMessage(
            factory=self.factory,
            kind=MessageKind.FINANCE,
            sender="Finance Loompa",
            title=(
                f"Já usamos {pct}% do orçamento de IA d{this} {label} "
                f"(US$ {st.period_cost_usd:.2f} de US$ {st.cap_usd:.2f})"
            ),
            context=(
                f"O consumo de IA está acima do ritmo previsto para {this} {label}. "
                + (
                    "A esteira será pausada automaticamente ao atingir o teto."
                    if pauses
                    else "Ao atingir o teto, todos os Loompas passam a usar só modelos gratuitos."
                )
            ).strip(),
            impact=(
                (
                    f"Sem ação, novas entregas podem ficar aguardando até {next_one}. "
                    if pauses
                    else "Sem ação, as entregas continuam, porém com modelos mais simples. "
                )
                + "Já apliquei prompts mais curtos nas tarefas rotineiras."
            ),
            options=[
                Option(
                    key="keep",
                    label="Manter o teto"
                    + (" (pausar ao atingir)" if pauses else " (modelos gratuitos ao atingir)"),
                    recommended=True,
                ),
                Option(key="raise_10", label=f"Aumentar o teto em US$ {bump[0]:.0f}"),
                Option(key="raise_30", label=f"Aumentar o teto em US$ {bump[1]:.0f}"),
            ],
        )
        self.store.put_message(msg)
        return msg

    # ----------------------------------------------------------------- reports
    def daily_report(self) -> dict[str, Any]:
        since = today_start_iso()
        totals = self.store.usage_totals(self.factory, since_iso=since)
        return {
            "date": datetime.now(UTC).date().isoformat(),
            "totals": totals,
            "by_role": self.store.usage_by("role", self.factory, since),
            "by_model": self.store.usage_by("model", self.factory, since),
            "by_story": self.store.usage_by("story_id", self.factory, since),
            "budget": self.status().__dict__,
        }

    def executive_daily_summary(self) -> str:
        r = self.daily_report()
        t = r["totals"]
        st = r["budget"]
        label = period_label(st["period"])
        lines = [
            f"Gasto de hoje: US$ {t['cost_usd']:.2f} em {t['calls']} consultas de IA.",
            f"Acumulado {'da' if label == 'semana' else 'do'} {label}: US$ {st['period_cost_usd']:.2f} "
            f"de US$ {st['cap_usd']:.2f} ({int(st['fraction'] * 100)}%).",
        ]
        if r["by_story"]:
            top = [
                f"{row['key'] or 'geral'} (US$ {row['cost_usd']:.2f})" for row in r["by_story"][:3]
            ]
            lines.append("Entregas que mais consumiram: " + ", ".join(top) + ".")
        return "\n".join(lines)

    def suggestions(self) -> list[str]:
        """Cheap heuristics the Finance Loompa surfaces in the daily Kaizen summary."""
        out = []
        by_role = self.store.usage_by(
            "role", self.factory, period_start_iso(self.config.budget.period)
        )
        total = sum(r["cost_usd"] for r in by_role) or 0.0
        for row in by_role:
            if total and row["cost_usd"] / total > 0.5 and row["key"] in ("worker", "inspector"):
                out.append(
                    f"O papel '{row['key']}' concentra {int(row['cost_usd'] / total * 100)}% do custo: revisar tamanho dos prompts e leituras de arquivo."
                )
            if (
                row["input_tokens"]
                and row["output_tokens"]
                and row["input_tokens"] / max(1, row["output_tokens"]) > 25
            ):
                out.append(
                    f"'{row['key']}' lê muito mais do que escreve (razão {int(row['input_tokens'] / max(1, row['output_tokens']))}:1): paginar leituras e usar busca exata antes de abrir arquivos."
                )
        tools = self.store.tool_output_by(self.factory, period_start_iso(self.config.budget.period))
        fed = sum(int(r["tokens"] or 0) for r in tools)
        if tools and fed >= TOOL_TOKENS_WORTH_A_NOTE:
            top = tools[0]
            share = int(int(top["tokens"]) / fed * 100)
            out.append(
                f"'{top['agent']}' trouxe ~{int(top['tokens']) // 1000} mil tokens para o contexto com "
                f"'{top['tool']}' ({top['calls']} chamadas, {share}% do que as ferramentas leram): "
                "ler trechos em vez de arquivos inteiros e buscar antes de abrir."
            )
        by_tier = self.store.usage_by(
            "tier", self.factory, period_start_iso(self.config.budget.period)
        )
        t1 = next((r for r in by_tier if r["key"] == "tier1"), None)
        if t1 and total and t1["cost_usd"] / total > 0.4:
            out.append(
                "Escalações para Tier 1 já respondem por mais de 40% do custo: vale reforçar a Constituição com as lições recentes."
            )
        return out
