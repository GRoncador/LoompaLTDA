"""Deployer Loompa: semantic commits, rebase, PR and delivery message (Tier 3 + optional Tier 2)."""

from __future__ import annotations

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.agents.kaizen import KaizenAgent
from loompa.comms import (
    FounderMessage,
    MessageKind,
    compose_delivery_message,
    sanitize_for_founder,
)
from loompa.engine.state import StoryState
from loompa.hygiene import is_debris
from loompa.worktrees import Worktree

SUMMARY_SYSTEM = """<!-- role:deployer -->
You are the Deployer Loompa. Summarize this delivery for the founder, who is not technical, in
{language}: 2-4 short sentences on what the product can do now and anything worth trying. Base it
only on the Worker notes, commits and changed files below, and do not promise what they do not
show. No file names, jargon or code.
Respond with JSON only: {{"summary": str}}
"""


class DeployerAgent(LoompaAgent):
    role = "deployer"
    display = "Deployer Loompa"

    async def run(self, state: StoryState, wt: Worktree) -> AgentResult:
        self.set_state("WORKING", state, detail="preparando entrega")
        self._drop_debris(state, wt)
        commit = self.git.commit_all(wt, f"chore({state.story_id.lower()}): finalize story")
        if commit:
            state.commits.append(commit.sha)
        if not self.git.rebase_on_base(wt):
            self.set_state("BLOCKED", state, detail="conflito com a base")
            return AgentResult(
                ok=False,
                blocked_reason="conflict",
                summary="conflito ao integrar com a versão principal",
            )
        # A PR already open for this branch just got the new push; `gh pr create` refuses it.
        state.pr_url = self._maybe_open_pr(state, wt) or state.pr_url
        stat = self.git.diff_stat(wt)
        log = self.git.log(wt)
        state.delivery_summary = await self._summary(state, stat, log)
        story = self.ctx.store.get_story(state.story_id) or {}
        msg = compose_delivery_message(
            factory=self.ctx.slug,
            story_id=state.story_id,
            story_title=state.title,
            summary=state.delivery_summary,
            pr_url=state.pr_url,
            cost_usd=float(story.get("cost_usd") or 0.0),
            cards=KaizenAgent(self.ctx).suggested_cards(state),
        )
        self.ctx.inbox(msg)
        state.blocked_message_id = msg.id
        self.set_state("IDLE")
        self.ctx.emit(
            "story.delivered",
            story_id=state.story_id,
            agent=self.name,
            pr_url=state.pr_url,
            commits=len(state.commits),
        )
        return AgentResult(ok=True, summary=state.delivery_summary, data={"message_id": msg.id})

    def _drop_debris(self, state: StoryState, wt: Worktree) -> list[str]:
        """Untracked leftovers (a `debug.txt`, a `.orig`) were never part of any task's commit:
        the finalize commit must not be the one that ships them (7.10)."""
        dropped = []
        for line in self.git.status(wt):
            rel = line[3:].strip().strip('"')
            path = wt.path / rel
            if line.startswith("??") and is_debris(rel) and path.is_file():
                path.unlink()
                dropped.append(rel)
        if dropped:
            self.ctx.emit(
                "deployer.debris_dropped", story_id=state.story_id, agent=self.name, files=dropped
            )
        return dropped

    def bootstrap_repo(self) -> bool:
        """A repo with no commits cannot host story worktrees. Make the first commit here
        instead of asking the founder to do it from a terminal. True when a commit was made."""
        commit = self.git.initial_commit()
        if commit is None:
            return False
        self.ctx.emit("repo.bootstrapped", agent=self.name, sha=commit.sha, files=commit.files)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=self.name,
                title="Fiz o registro inicial do projeto",
                context="O projeto ainda não tinha nenhuma versão salva, e as entregas precisam de "
                "um ponto de partida. Salvei a estrutura inicial como primeira versão.",
                impact="Nenhuma ação necessária da sua parte; as entregas seguem normalmente.",
                allow_free_text=False,
            )
        )
        return True

    def sync_with_base(self, state: StoryState, wt: Worktree) -> list[str] | None:
        """Bring a story that is going back to work up to date with the base: a fix merged in
        the meantime (the red suite that failed every story, say) must reach it before its
        next test run. The base is merged into the story branch. None: nothing to do;
        []: merged; a list: files in conflict, waiting for `finish_sync`."""
        if self.git.merge_in_progress(wt):
            # an interrupted run stopped mid-merge: start the integration over, never commit
            # conflict markers as work in progress
            self.git.abort_merge(wt)
        if not self.git.behind_base(wt):
            return None
        if self.git.status(wt):
            # work left uncommitted (an interrupted run, a task cut short) is kept as a commit:
            # skipping the sync instead left the story testing on the old base
            commit = self.git.commit_all(
                wt, f"wip({state.story_id.lower()}): trabalho em andamento antes de integrar a base"
            )
            if commit:
                state.commits.append(commit.sha)
        conflicts = self.git.merge_base_into(wt)
        self.ctx.emit(
            "worktree.synced", story_id=state.story_id, agent=self.name, conflicts=conflicts
        )
        return conflicts

    def finish_sync(self, state: StoryState, wt: Worktree, files: list[str]) -> bool:
        """Commit the merge once the conflicts were resolved; abort it when markers remain,
        leaving the story on its old base (the delivery then reports the conflict)."""
        left = self.git.has_conflict_markers(wt, files)
        if left:
            self.git.abort_merge(wt)
            self.ctx.emit(
                "worktree.sync_failed", story_id=state.story_id, agent=self.name, files=left
            )
            return False
        sha = self.git.conclude_merge(wt)
        state.commits.append(sha)
        self.ctx.emit("worktree.merged_base", story_id=state.story_id, agent=self.name, sha=sha)
        return True

    def merge(self, state: StoryState, wt: Worktree) -> str:
        sha = self.git.merge_into_base(wt, message=f"feat({state.story_id.lower()}): {state.title}")
        state.merged_sha = sha
        self.ctx.worktrees.remove(state.story_id)
        self.ctx.emit("story.merged", story_id=state.story_id, agent=self.name, sha=sha)
        return sha

    def _maybe_open_pr(self, state: StoryState, wt: Worktree) -> str | None:
        if self.ctx.dry_run:
            return None
        body = (
            f"## {state.title}\n\n{state.delivery_summary or state.worker_summary}\n\n"
            f"Spec: `.loompa/specs/{state.story_id}/spec.md`\n\n🤖 Generated with Loompa LTDA"
        )
        return self.git.open_pull_request(
            wt, title=f"{state.story_id}: {state.title}", body=body, timeout=120
        )

    async def _summary(self, state: StoryState, stat: str, log: list[str]) -> str:
        fallback = sanitize_for_founder(
            f"{state.worker_summary or state.title}. {len(log)} passos concluídos e verificados pelos testes automáticos."
        )
        if self.ctx.dry_run:
            return fallback
        try:
            data = await self.ask_json(
                SUMMARY_SYSTEM.format(language=self.language),
                f"Story: {state.title}\n\nWorker notes:\n{state.worker_summary}\n\nCommits:\n"
                + "\n".join(log[:10])
                + f"\n\nChanged files:\n{stat[:1500]}",
                story=state,
                task="deployer.summary",
                max_tokens=400,
            )
            text = str(data.get("summary") or "").strip()
            return sanitize_for_founder(text) if text else fallback
        except Exception:  # noqa: BLE001
            return fallback
