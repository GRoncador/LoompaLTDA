"""Finance Loompa (CFO): exact USD accounting per call, story and day; budget alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loompa.comms import FounderMessage, MessageKind, Option
from loompa.config.schema import BudgetConfig, LoompaConfig
from loompa.store import Store


def today_start_iso() -> str:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def month_start_iso() -> str:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


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


@dataclass
class BudgetStatus:
    month_cost_usd: float
    today_cost_usd: float
    cap_usd: float
    fraction: float
    warn: bool
    exhausted: bool

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.month_cost_usd)


class CostTracker:
    def __init__(self, store: Store, config: LoompaConfig, factory: str):
        self.store = store
        self.config = config
        self.factory = factory
        self._warned_key = f"finance:warned:{datetime.now(UTC):%Y-%m}"

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
        rec.cost_usd = self.cost_of(
            rec.model, rec.input_tokens, rec.output_tokens, rec.cached_tokens
        )
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
        month = float(
            self.store.usage_totals(self.factory, since_iso=month_start_iso())["cost_usd"]
        )
        today = float(
            self.store.usage_totals(self.factory, since_iso=today_start_iso())["cost_usd"]
        )
        fraction = (month / b.monthly_cap_usd) if b.monthly_cap_usd else 0.0
        return BudgetStatus(
            month_cost_usd=round(month, 4),
            today_cost_usd=round(today, 4),
            cap_usd=b.monthly_cap_usd,
            fraction=round(fraction, 4),
            warn=fraction >= b.warn_at_fraction,
            exhausted=b.hard_stop and fraction >= 1.0,
        )

    def maybe_alert(self) -> FounderMessage | None:
        """Emit one executive alert per month when the warn threshold is crossed."""
        st = self.status()
        if not st.warn or self.store.get(self._warned_key):
            return None
        self.store.set(self._warned_key, "1")
        pct = int(st.fraction * 100)
        msg = FounderMessage(
            factory=self.factory,
            kind=MessageKind.FINANCE,
            sender="Finance Loompa",
            title=f"Já usamos {pct}% do orçamento mensal de IA (US$ {st.month_cost_usd:.2f} de US$ {st.cap_usd:.2f})",
            context=(
                "O consumo de IA está acima do ritmo previsto para este mês. "
                + (
                    "A esteira será pausada automaticamente ao atingir o teto."
                    if st.exhausted or self.config.budget.hard_stop
                    else ""
                )
            ).strip(),
            impact="Sem ação, novas entregas podem ficar aguardando até o próximo mês. Já apliquei prompts mais curtos nas tarefas rotineiras.",
            options=[
                Option(key="keep", label="Manter o teto (pausar ao atingir)", recommended=True),
                Option(key="raise_10", label="Aumentar o teto em US$ 10"),
                Option(key="raise_30", label="Aumentar o teto em US$ 30"),
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
        lines = [
            f"Gasto de hoje: US$ {t['cost_usd']:.2f} em {t['calls']} consultas de IA.",
            f"Acumulado do mês: US$ {st['month_cost_usd']:.2f} de US$ {st['cap_usd']:.2f} ({int(st['fraction'] * 100)}%).",
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
        by_role = self.store.usage_by("role", self.factory, month_start_iso())
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
        by_tier = self.store.usage_by("tier", self.factory, month_start_iso())
        t1 = next((r for r in by_tier if r["key"] == "tier1"), None)
        if t1 and total and t1["cost_usd"] / total > 0.4:
            out.append(
                "Escalações para Tier 1 já respondem por mais de 40% do custo: vale reforçar a Constituição com as lições recentes."
            )
        return out
