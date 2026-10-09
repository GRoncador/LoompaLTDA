"""Stop the line (ADR-0036 §4): the base's own test suite, run after every change of the base.

tamagotchi SP-013 (08/10): two merges left the base with 3 failing tests. Nobody looked at the
base itself, so every story started after them found the failures in its baseline: five
`inspector.baseline_red`, a Kaizen card per story ("A suíte de verificações já falha na versão
principal", 12 duplicates), and the founder asked again and again about "2 falhas vindas de outra
história". Here the engine runs the suite on the base whenever the base moved, and when it goes
red there is one card to fix it, at the top of the backlog and in the running sprint, one note to
the founder, and no other card about the same failures while it is open.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from loompa.comms import FounderMessage, MessageKind
from loompa.engine.context import EngineContext
from loompa.engine.state import TERMINAL
from loompa.store import now_iso

log = logging.getLogger(__name__)

LINE_KEY = "line"  # kv: {"sha", "red", "failing", "card", "since"} of the base last checked
SENDER = "Inspector Loompa"
_STORY_IN_SUBJECT = re.compile(r"^\w+\((s-\d+\w*)\)", re.I)


def line_state(ctx: EngineContext) -> dict[str, Any]:
    raw = ctx.store.get(LINE_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def line_card(ctx: EngineContext) -> str | None:
    """The open card that fixes a red base, or None (the base is green, or no card is open)."""
    state = line_state(ctx)
    card = state.get("card") if state.get("red") else None
    if not card:
        return None
    row = ctx.store.get_story(card)
    return card if row is not None and row["stage"] not in TERMINAL else None


class LineWatch:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def base_sha(self) -> str:
        wtm = self.ctx.worktrees
        try:
            return wtm.git("rev-parse", wtm.default_branch()).strip()
        except Exception:  # noqa: BLE001 - an empty repository has no base to check yet
            return ""

    def due(self) -> bool:
        """The base moved since it was last checked, and there is a base worth checking: one a
        story was merged into (a fresh factory's tests are the founder's, red or not)."""
        if self.ctx.dry_run:
            return False
        sha = self.base_sha()
        if not sha or sha == line_state(self.ctx).get("sha"):
            return False
        if self.ctx.store.story_ids_with_event(self.ctx.slug, "story.merged"):
            return True
        # nothing merged yet: this base is the founder's; remembered, so this is asked once
        self.ctx.store.set(LINE_KEY, json.dumps({"sha": sha, "red": False, "failing": []}))
        return False

    async def check(self) -> dict[str, Any]:
        """Run the base's suite once for its current commit and act on what changed."""
        from loompa.agents.inspector import InspectorAgent

        sha = self.base_sha()
        before = line_state(self.ctx)
        with self.ctx.worktrees.base_checkout(sha) as path:
            checks = await InspectorAgent(self.ctx).checks_at(path, lint=False)
        red = not checks["tests_ok"]
        failing = list(checks["failing"])
        now = {
            "sha": sha,
            "red": red,
            "failing": failing[:40],
            "card": before.get("card") if red else None,
            "since": before.get("since") if red and before.get("red") else now_iso(),
        }
        if red and not line_card(self.ctx):
            now["card"] = self._stop(before.get("sha") or "", sha, failing)
        elif not red and before.get("red"):
            self._green(before)
        self.ctx.store.set(LINE_KEY, json.dumps(now))
        self.ctx.emit("line.checked", sha=sha[:12], red=red, failing=len(failing))
        return now

    def _merged_since(self, old: str, new: str) -> list[str]:
        """The stories whose merges took the base from `old` to `new` (their commit subjects)."""
        if not old:
            return []
        try:
            subjects = self.ctx.worktrees.git(
                "log", "--first-parent", "--format=%s", f"{old}..{new}"
            ).splitlines()
        except Exception:  # noqa: BLE001 - an old sha that is gone: no names, still a stop
            return []
        out: list[str] = []
        for s in subjects:
            m = _STORY_IN_SUBJECT.match(s.strip())
            if m and m.group(1).upper() not in out:
                out.append(m.group(1).upper())
        return list(reversed(out))

    def _stop(self, old: str, new: str, failing: list[str]) -> str | None:
        """One card to make the base green again, first in line, and one note to the founder."""
        from loompa.agents.product_owner import ProductOwnerAgent
        from loompa.backlog import MIN_PRIORITY, TOP

        by = self._merged_since(old, new)
        po = ProductOwnerAgent(self.ctx)
        n = len(failing) or 1
        title = f"Consertar a versão principal: {n} verificação(ões) automática(s) falhando"
        description = (
            "A suíte de testes falha na versão principal"
            + (f" desde a entrada de {', '.join(by)}" if by else "")
            + ". Falhas: "
            + (", ".join(failing[:12]) or "a suíte não terminou verde")
            + ". Corrija o produto ou, quando o teste estiver errado (por exemplo, lendo um "
            "documento interno da fábrica), o próprio teste. Enquanto este card estiver aberto, "
            "as outras histórias não são cobradas por essas falhas."
        )
        card = None
        try:
            admission = po.add_item(
                title,
                description,
                priority=MIN_PRIORITY,
                origin="kaizen",
                kind="bugfix",
                after=TOP,
            )
            card = admission.story_id
            if card:
                po.backlog.pin(card, True)
                po.resolve_finding(card, "sprint")  # into the running sprint, if one runs
        except Exception:  # noqa: BLE001 - the note still goes out; Kaizen files it otherwise
            log.exception("could not file the card that fixes the base")
        self.ctx.emit("line.red", sha=new[:12], failing=failing[:20], merged=by, card=card or None)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                story_id=card,
                kind=MessageKind.INFO,
                sender=SENDER,
                title="A versão principal do produto ficou com verificações falhando",
                context=(
                    (f"Depois da entrada de {', '.join(by)}, " if by else "")
                    + f"{n} verificação(ões) automática(s) do produto passaram a falhar na versão "
                    "principal."
                    + (
                        f" A equipe abriu o card {card} para consertar isso primeiro."
                        if card
                        else ""
                    )
                ),
                impact=(
                    "As outras frentes seguem e não são cobradas por essas falhas, e nenhum card "
                    "repetido será aberto por elas. Se quiser, priorize o conserto na próxima "
                    "reunião."
                ),
                allow_free_text=False,
            )
        )
        return card

    def _green(self, before: dict[str, Any]) -> None:
        self.ctx.emit("line.green", card=before.get("card"), since=before.get("since"))
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                story_id=before.get("card"),
                kind=MessageKind.INFO,
                sender=SENDER,
                title="A versão principal voltou a passar em todas as verificações",
                context="As verificações automáticas do produto estão todas verdes de novo.",
                allow_free_text=False,
            )
        )
