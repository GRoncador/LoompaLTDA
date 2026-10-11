"""What the factory's kind turns on (plan 16.1).

The founder answers one question when a factory opens, and can change it in the settings:
is the product **commercial** (sold to a market) or **personal** (for the founder and the people
around them)? The answer is read here, in one place, by everything that depends on it:

* the life cycle (16.2, 16.3): a commercial product goes through market discovery and phases
  with exit criteria; a personal one has a single phase, "in use", and no market discovery;
* growth (Phase 17): only a commercial product gets marketing pieces;
* the design gate (15.5): full on a commercial product (alternatives, every state), light on a
  personal one (one screen per story, its main state only).

The product manager and the epics serve both kinds: they organise the work even without a market.
Factories created before the question existed load as personal, which keeps what they did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from loompa.config.schema import FactoryKind, LoompaConfig

DesignGate = Literal["full", "light", "off"]

KINDS: tuple[FactoryKind, ...] = ("personal", "commercial")

KIND_LABEL: dict[str, str] = {"personal": "pessoal", "commercial": "comercial"}

KIND_HINT: dict[str, str] = {
    "personal": (
        "Para você e quem está por perto: sem pesquisa de mercado nem growth, uma fase só "
        "(em uso) e telas aprovadas no modo leve."
    ),
    "commercial": (
        "Para vender a um mercado: descoberta com concorrentes e hipóteses, fases com critério "
        "de saída, peças de marketing e telas aprovadas no modo completo."
    ),
}


@dataclass(frozen=True)
class KindProfile:
    kind: FactoryKind
    market_discovery: bool  # 16.2: Lean Canvas, competitors, hypotheses in the opening
    growth: bool  # Phase 17: marketing pieces the founder reviews
    phases: tuple[str, ...]  # 16.3: the life-cycle phases, in order
    design_gate: DesignGate  # 15.5: the gate's default mode


PROFILES: dict[str, KindProfile] = {
    "personal": KindProfile(
        kind="personal",
        market_discovery=False,
        growth=False,
        phases=("in_use",),
        design_gate="light",
    ),
    "commercial": KindProfile(
        kind="commercial",
        market_discovery=True,
        growth=True,
        phases=("discovery", "mvp", "validation", "growth", "maturity"),
        design_gate="full",
    ),
}


def profile(config: LoompaConfig) -> KindProfile:
    return PROFILES.get(config.factory.kind, PROFILES["personal"])


def parse_kind(value: str | None) -> FactoryKind | None:
    """The kind a person typed (English or pt-BR, any case); None when it is none of them."""
    if value is None:
        return None
    v = value.strip().lower()
    for kind in KINDS:
        if v in (kind, KIND_LABEL[kind]):
            return kind
    return None
