"""Deployer Loompa: semantic commits, rebase, PR and delivery message (Tier 3 + optional Tier 2)."""

from __future__ import annotations

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.agents.kaizen import KaizenAgent
from loompa.comms import compose_delivery_message, sanitize_for_founder
from loompa.engine.state import StoryState
from loompa.worktrees import Worktree

SUMMARY_SYSTEM = """<!-- role:deployer -->
Summarize this delivery for a non-technical founder in {language}: 2-4 short sentences about what the
product can now do and anything they should try. No file names, no jargon, no code.
Respond with JSON: {{"summary": str}}
"""


class DeployerAgent(LoompaAgent):
    role = "deployer"
    display = "Deployer Loompa"

    async def run(self, state: StoryState, wt: Worktree) -> AgentResult:
        self.set_state("WORKING", state, detail="preparando entrega")
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
        state.pr_url = self._maybe_open_pr(state, wt)
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
                tier_override="tier2",
                max_tokens=400,
            )
            text = str(data.get("summary") or "").strip()
            return sanitize_for_founder(text) if text else fallback
        except Exception:  # noqa: BLE001
            return fallback
