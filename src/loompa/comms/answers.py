"""What the founder's answer to a team question makes the factory do (ADR-0037 §1).

tamagotchi S-094 (10/10): the founder picked "encerrar como já resolvida" four times and "encerrar
sem enviar nada" twice. Each option was the Master's wording of what the Worker suggested, and the
engine read every option of a question as guidance for the Architect, who can only add files and
tasks: each answer changed nothing and the Worker asked again, eight times in 24 hours. Now every
option of a question carries an action the code carries out, the founder reads what it does,
and the ways out (later, cancel) are always among them.
"""

from __future__ import annotations

import re

from loompa.comms.executive import Option

GUIDE = "guide"  # the label goes to the team as guidance (keys opt1, opt2, … as before)
RESPEC = "respec"  # the spec is rewritten with the guidance before the work goes on
RESOLVED = "resolved"  # the story closes without a delivery: what it asks already holds
SKIP = "skip"  # back to the backlog
DROP = "drop"  # cancelled
ACTIONS = (GUIDE, RESPEC, RESOLVED, SKIP, DROP)

# What the founder reads under an option: what the factory does when it is chosen.
ACTION_NOTE = {
    RESPEC: "A especificação é reescrita com essa orientação antes de o trabalho seguir.",
    RESOLVED: (
        "A história é encerrada sem entrega, como já resolvida. Se as verificações que a "
        "motivaram ainda falharem na versão principal, a fábrica avisa você em vez de encerrar."
    ),
}
SKIP_LABEL = "Deixar para depois (volta ao backlog)"
DROP_LABEL = "Cancelar esta entrega"

# For an option no model classified (the dry run, a rewrite that failed): its own words.
_BY_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        DROP,
        re.compile(
            r"\bcancel(?:ar|e)\s+(?:esta|essa|a)\s+(?:entrega|hist[óo]ria)|"
            r"\bcancel (?:this|the) (?:story|delivery)",
            re.I,
        ),
    ),
    (
        SKIP,
        re.compile(
            r"deixar (?:para|pra) depois|volt(?:a|ar) (?:ao|para o) backlog|back to the backlog",
            re.I,
        ),
    ),
    (
        RESOLVED,
        re.compile(
            r"j[áa] (?:est[áa] |foi |foram |)(?:resolvid|corrigid|consertad)|"
            r"(?:encerrar|fechar|concluir)\b.{0,60}\b(?:resolvid|sem (?:nenhuma )?(?:altera|mudan|entrega))|"
            r"already (?:resolved|fixed)|nothing (?:left )?to (?:do|fix)",
            re.I,
        ),
    ),
    (
        RESPEC,
        re.compile(
            r"(?:reescrever|refazer|rever|revisar) (?:a |as )?(?:especifica|crit[ée]rios)|"
            r"rewrite the (?:spec|criteria)",
            re.I,
        ),
    ),
)
_TRAILING_N = re.compile(r"\d+$")


def action_of(key: str | None) -> str:
    """The action an option key stands for: `resolved2` is `resolved`, `opt1` is guidance."""
    base = _TRAILING_N.sub("", (key or "").strip().lower())
    return base if base in ACTIONS and base != GUIDE else GUIDE


def by_words(label: str) -> str:
    for action, pattern in _BY_WORDS:
        if pattern.search(label or ""):
            return action
    return GUIDE


def executable_options(
    labels: list[str], actions: list[str | None] | None = None, *, ways_out: bool = True
) -> list[Option]:
    """The options of a team question, each keyed by the action the code takes. `actions` are a
    model's reading of each label, used when valid; otherwise the label's own words decide, and
    anything else is guidance. The first option stays the recommended one; "later" and "cancel"
    are added when no option already offers them."""
    out: list[Option] = []
    used: set[str] = set()
    guides = 0
    for i, label in enumerate(labels):
        given = (actions[i] if actions and i < len(actions) else None) or ""
        action = given if given in ACTIONS else by_words(label)
        if action == GUIDE:
            guides += 1
            key = f"opt{guides}"
        else:
            key, n = action, 2
            while key in used:
                key, n = f"{action}{n}", n + 1
        used.add(key)
        out.append(
            Option(
                key=key,
                label=label,
                description=ACTION_NOTE.get(action, ""),
                recommended=i == 0,
            )
        )
    if ways_out:
        have = {action_of(o.key) for o in out}
        if SKIP not in have:
            out.append(Option(key=SKIP, label=SKIP_LABEL))
        if DROP not in have:
            out.append(Option(key=DROP, label=DROP_LABEL))
    return out
