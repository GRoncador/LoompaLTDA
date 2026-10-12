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
import subprocess
from pathlib import Path
from typing import Any

from loompa.comms import FounderMessage, MessageKind
from loompa.engine.context import EngineContext
from loompa.engine.state import TERMINAL
from loompa.store import now_iso

log = logging.getLogger(__name__)

# kv: {"sha", "red", "failing", "card", "targets", "since"} of the base last checked
LINE_KEY = "line"
# kv: {story id: {"why", "by"}}: line cards whose failures left the base while they were in the
# pipeline; their next node is `resolve` (ADR-0037 §4)
RESOLVED_KEY = "line_resolved"
SENDER = "Inspector Loompa"
_STORY_IN_SUBJECT = re.compile(r"^\w+\((s-\d+\w*)\)", re.I)
MIN_NAME = 12  # a failing test's name shorter than this ("CA1") says nothing in another card


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


def _resolved_cards(ctx: EngineContext) -> dict[str, Any]:
    raw = ctx.store.get(RESOLVED_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def line_resolved(ctx: EngineContext, story_id: str) -> dict[str, Any] | None:
    """Why the line card `story_id` has nothing left to fix, when the line watch saw its failures
    leave the base while it was in the pipeline; None otherwise."""
    return _resolved_cards(ctx).get(story_id)


def forget_resolved(ctx: EngineContext, story_id: str) -> None:
    cards = _resolved_cards(ctx)
    if cards.pop(story_id, None) is not None:
        ctx.store.set(RESOLVED_KEY, json.dumps(cards))


def test_files_holding(root: Path, failing: list[str]) -> dict[str, str]:
    """The test files that hold the failing tests, each with its text: the file a pytest id
    names, or the one whose source carries a runner's test title (node's "AC6: …"). Those that
    read the factory's records under `.loompa/` are named in the card (ADR-0037 §4): such a test
    passes only where those files happen to be (tamagotchi's AC6 read
    `.loompa/specs/S-077/plan.md`, which the merge leaves out)."""
    from loompa.hygiene import is_test_path

    try:
        files = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {}
    names = [n.split("::", 1)[0] if "::" in n else n for n in failing]
    out: dict[str, str] = {}
    for rel in files:
        if not is_test_path(rel) or rel.startswith(".loompa/"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(n == rel or (len(n) >= MIN_NAME and n[:80] in text) for n in names):
            out[rel] = text
    return out


# The short label a failing test goes by in a story: "AC6" of node's "AC6: o plano/tasks …", the
# function of a pytest id.
_LABEL = re.compile(r"^\s*([\w.-]{2,12}):\s")


def _label(name: str) -> str:
    if "::" in name:
        return name.rsplit("::", 1)[-1]
    m = _LABEL.match(name)
    return m.group(1) if m else ""


def _stem(rel: str) -> str:
    """`casca_body_scripts` of tests/casca_body_scripts.test.js: how a story names a test file."""
    return Path(rel).name.split(".", 1)[0]


def line_targets(ctx: EngineContext, story_id: str) -> list[str]:
    """The failures the line card `story_id` was opened for; [] for any other story."""
    state = line_state(ctx)
    if state.get("card") != story_id:
        return []
    return list(state.get("targets") or state.get("failing") or [])


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
            holding = test_files_holding(path, failing) if red else {}
            holders = list(holding)
            records = [rel for rel, text in holding.items() if ".loompa" in text]
        card = line_card(self.ctx)
        targets = list(before.get("targets") or before.get("failing") or [])
        if card and targets and not set(targets) & set(failing):
            # what the card was opened for left the base, fixed by another delivery: the card
            # closes instead of going on with nothing to fix (tamagotchi S-094, ADR-0037 §4)
            await self._resolved(card, self._merged_since(before.get("sha") or "", sha))
            card, targets = None, []
        now = {
            "sha": sha,
            "red": red,
            "failing": failing[:40],
            "card": card if red else None,
            "targets": targets if card and red else [],
            "since": before.get("since") if red and before.get("red") else now_iso(),
        }
        if red and card is None:
            now["card"] = self._stop(before.get("sha") or "", sha, failing, records, holders)
            now["targets"] = failing[:40] if now["card"] else []
        elif not red and before.get("red"):
            self._green(before)
        self.ctx.store.set(LINE_KEY, json.dumps(now))
        self.ctx.emit("line.checked", sha=sha[:12], red=red, failing=len(failing))
        return now

    def _already_fixing(self, failing: list[str], holders: list[str] | None = None) -> str | None:
        """An open story that already names one of these failures: the line adopts it instead of
        opening another card for the same tests (ADR-0037 §4). One in the pipeline first, then
        one waiting in the backlog (a finding filed before the line red). Named word for word,
        or by the test's label in the file that holds it: tamagotchi S-101 ("Trocar o critério
        AC6 de casca_body_scripts…", 11/10) was in the sprint for the failing "AC6: o
        plano/tasks da T8 …" of tests/casca_body_scripts.test.js, and the line opened S-106
        for the same test; both ran at once."""
        names = [n.casefold() for n in failing if len(n) >= MIN_NAME]
        labels = [
            re.compile(rf"(?<![\w-]){re.escape(lb)}(?![\w-])", re.I)
            for lb in dict.fromkeys(_label(n) for n in failing)
            if lb
        ]
        files = [
            (rel.casefold(), _stem(rel).casefold()) for rel in holders or [] if len(_stem(rel)) >= 6
        ]
        if not names and not (labels and files):
            return None
        found: list[tuple[int, str]] = []
        for row in self.ctx.store.list_stories(self.ctx.slug):
            if row["stage"] in TERMINAL:
                continue
            text = f"{row['title']}\n{row.get('description', '')}".casefold()
            named = any(n in text for n in names) or (
                any(rel in text or stem in text for rel, stem in files)
                and any(lb.search(text) for lb in labels)
            )
            if named:
                found.append((1 if row["stage"] == "BACKLOG" else 0, row["id"]))
        return min(found)[1] if found else None

    async def _resolved(self, card: str, by: list[str]) -> None:
        """Close the line card whose failures left the base. Waiting (in the backlog or for the
        founder) it closes now; in the pipeline its next node is `resolve`, which closes it there
        (a state written from here would be overwritten by the node running it)."""
        from loompa.engine.graph import close_resolved
        from loompa.engine.scheduler import runtime_for
        from loompa.engine.state import Stage, StoryState

        row = self.ctx.store.get_story(card)
        if row is None or row["stage"] in TERMINAL:
            return
        why = "as verificações que o card ia consertar voltaram a passar na versão principal"
        if row["stage"] in (Stage.BACKLOG, Stage.AWAITING_FOUNDER):
            state = StoryState.from_row(row)
            if state.blocked_message_id:
                self.ctx.store.archive_message(state.blocked_message_id)
            close_resolved(self.ctx, state, why, by=by)
            if row["stage"] == Stage.AWAITING_FOUNDER:
                # its graph waits at the pause: told the way an answer is, it ends there
                await runtime_for(self.ctx).inject_founder_answer(state)
        else:
            cards = _resolved_cards(self.ctx)
            cards[card] = {"why": why, "by": by}
            self.ctx.store.set(RESOLVED_KEY, json.dumps(cards))
        self.ctx.emit("line.resolved", card=card, by=by)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                story_id=card,
                kind=MessageKind.INFO,
                sender=SENDER,
                title="O conserto da versão principal não é mais necessário",
                context=(
                    f"As verificações que o card {card} ia consertar voltaram a passar na versão "
                    "principal"
                    + (f", com a entrada de {', '.join(by)}" if by else "")
                    + ". A equipe encerra esse card sem entrega."
                ),
                allow_free_text=False,
            )
        )

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

    def _stop(
        self,
        old: str,
        new: str,
        failing: list[str],
        records: list[str] | None = None,
        holders: list[str] | None = None,
    ) -> str | None:
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
        if records:
            # said in the card, where the spec is written from: S-094's spec asked to create the
            # records the test read, the one fix the factory can never deliver (ADR-0037 §4)
            description += (
                f" Atenção: {', '.join(records[:5])} lê(em) os registros internos da fábrica "
                "(a pasta .loompa), que não fazem parte do produto e não chegam à versão "
                "principal. Ali o conserto é reescrever esse teste para conferir o comportamento "
                "do produto, ou removê-lo; nunca criar ou editar registros da fábrica."
            )
        card = self._already_fixing(failing, holders)
        if card is not None:
            try:
                po.backlog.pin(card, True)
                po.resolve_finding(card, "sprint")  # a waiting finding joins the running sprint
            except Exception:  # noqa: BLE001 - adopted even if it could not be moved
                log.exception("could not move the card that already fixes the base")
            self.ctx.emit("line.adopted", sha=new[:12], failing=failing[:20], card=card)
            return card
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
                title="A versão principal ficou com verificações falhando",
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
        from loompa.agents.inspector import BASE_RED_TITLE
        from loompa.engine.graph import close_resolved
        from loompa.engine.state import Stage, StoryState

        self.ctx.emit("line.green", card=before.get("card"), since=before.get("since"))
        # the findings filed while the base was red and no card was open say nothing any more
        # (tamagotchi S-091 waited in the backlog after the base it described was fixed, §8)
        for row in self.ctx.store.list_stories(self.ctx.slug):
            if (
                row["stage"] == Stage.BACKLOG
                and row.get("origin") == "kaizen"
                and BASE_RED_TITLE in row["title"]
            ):
                close_resolved(
                    self.ctx,
                    StoryState.from_row(row),
                    "a versão principal voltou a passar em todas as verificações",
                )
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
