"""Worker Loompa: implements tasks.md inside an isolated worktree using only ACI tools.

Each task runs in a fresh, ephemeral conversation (zero-context paradigm) and ends in one
semantic commit. The Worker never sees raw terminal output — only ACI-compacted results.
"""

from __future__ import annotations

import json

from loompa.aci import ACI
from loompa.agents.base import AgentResult, LoompaAgent
from loompa.agents.toolbox import PROFILES, Toolbox, prune_tool_history  # noqa: F401 (re-exported)
from loompa.engine.state import StoryState
from loompa.llm import Message
from loompa.speckit import story_dir, tasks_from_markdown
from loompa.speckit.artifacts import mark_task_done
from loompa.worktrees import Worktree

SYSTEM = """<!-- role:worker -->
You are the Worker Loompa, a senior full-stack engineer executing ONE task from a checklist inside an
isolated git worktree. You have no shell — only the tools provided. Work surgically:
1. Read the relevant files first (paginated). Search before assuming names or signatures.
2. Implement exactly the task, with tests. Do not touch files outside the allowed paths; if you need
   to, call `note_learning` describing why and finish what you can.
3. Run `run_tests` (and `run_lint` when configured) and fix failures until they pass.
4. When the task is complete and green, call `done` with a one-sentence summary.
5. If a decision requires a human (ambiguous requirement, missing credential, destructive change),
   call `blocked` with a plain-language reason in {language} and 2-3 options. Do not guess.
Never rewrite unrelated code, never add dependencies, keep diffs minimal, follow the constitution.
"""


DOD_SYSTEM = """<!-- role:dod -->
You are the Worker Loompa doing a definition-of-done self-check right after finishing a task.
Given the task, your own summary and the diff you produced, answer honestly whether the task is
really complete: code AND tests present, nothing outside the task touched, no TODO left behind.
Respond with JSON only: {{"complete": bool, "missing": [str]}} — `missing` lists concrete things
still to do (in {language}); empty when complete.
"""


class WorkerAgent(LoompaAgent):
    role = "worker"
    display = "Worker Loompa"

    def __init__(self, ctx, *, name: str | None = None, tier_override: str | None = None):
        super().__init__(ctx, name=name)
        self.tier_override = tier_override

    async def run(self, state: StoryState, wt: Worktree) -> AgentResult:
        paths = story_dir(self.ctx.root, state.story_id)
        tasks_md = paths.tasks.read_text(encoding="utf-8") if paths.tasks.is_file() else ""
        tasks = tasks_from_markdown(tasks_md)
        pending = [t for t in tasks if t.number not in state.tasks_done]
        if not pending and state.failure_history:
            # every task is done but the Inspector failed the story: run a focused fix pass
            return await self._fix_pass(state, wt)
        if not pending:
            return AgentResult(ok=True, summary="todas as tarefas já concluídas")
        spec = paths.spec.read_text(encoding="utf-8") if paths.spec.is_file() else ""
        plan = paths.plan.read_text(encoding="utf-8") if paths.plan.is_file() else ""
        aci = self.ctx.aci_for(wt.path, allowed_paths=state.allowed_paths or None)
        summaries: list[str] = []
        tier_label = self.tier_override or "tier2"
        for task in pending:
            self.set_state(
                "WORKING", state, model=tier_label, detail=f"T{task.number}: {task.text[:60]}"
            )
            result = await self._run_task(state, aci, task.number, task.text, spec, plan, tasks_md)
            if result.blocked_reason:
                self.set_state("BLOCKED", state, detail="aguardando decisão")
                self._flush_learnings(state, aci)
                return result
            missing = await self._dod_check(state, wt, task.text, result.summary)
            if missing:
                self.ctx.emit(
                    "worker.dod_incomplete",
                    story_id=state.story_id,
                    agent=self.name,
                    task=task.number,
                    missing=missing[:5],
                )
                followup = await self._run_task(
                    state,
                    aci,
                    task.number,
                    task.text
                    + "\n\nSelf-check found these still missing; finish them:\n"
                    + "\n".join(f"- {m}" for m in missing[:5]),
                    spec,
                    plan,
                    tasks_md,
                )
                if followup.blocked_reason:
                    self.set_state("BLOCKED", state, detail="aguardando decisão")
                    self._flush_learnings(state, aci)
                    return followup
                result.summary = f"{result.summary} / {followup.summary}"
            commit = self.ctx.worktrees.commit_all(
                wt, f"feat({state.story_id.lower()}): {task.text[:60]}"
            )
            if commit:
                state.commits.append(commit.sha)
                self.ctx.emit(
                    "worktree.commit",
                    story_id=state.story_id,
                    agent=self.name,
                    sha=commit.sha,
                    files=commit.files,
                    task=task.number,
                )
            state.tasks_done.append(task.number)
            tasks_md = mark_task_done(tasks_md, task.number)
            paths.tasks.write_text(tasks_md, encoding="utf-8")
            summaries.append(f"T{task.number}: {result.summary}")
            if not result.ok:
                break
        self._flush_learnings(state, aci)
        self.set_state("IDLE")
        state.worker_summary = "\n".join(summaries)
        return AgentResult(ok=True, summary=state.worker_summary)

    async def _fix_pass(self, state: StoryState, wt: Worktree) -> AgentResult:
        paths = story_dir(self.ctx.root, state.story_id)
        spec = paths.spec.read_text(encoding="utf-8") if paths.spec.is_file() else ""
        plan = paths.plan.read_text(encoding="utf-8") if paths.plan.is_file() else ""
        tasks_md = paths.tasks.read_text(encoding="utf-8") if paths.tasks.is_file() else ""
        aci = self.ctx.aci_for(wt.path, allowed_paths=state.allowed_paths or None)
        tier_label = self.tier_override or "tier2"
        self.set_state(
            "WORKING", state, model=tier_label, detail="corrigindo falhas apontadas pelo Inspector"
        )
        result = await self._run_task(
            state,
            aci,
            0,
            "Corrigir as falhas apontadas pelo Inspector (abaixo) sem alterar o escopo; rode os testes até ficarem verdes.",
            spec,
            plan,
            tasks_md,
        )
        self._flush_learnings(state, aci)
        if result.blocked_reason:
            self.set_state("BLOCKED", state, detail="aguardando decisão")
            return result
        commit = self.ctx.worktrees.commit_all(
            wt, f"fix({state.story_id.lower()}): address inspector findings"
        )
        if commit:
            state.commits.append(commit.sha)
            self.ctx.emit(
                "worktree.commit",
                story_id=state.story_id,
                agent=self.name,
                sha=commit.sha,
                files=commit.files,
                task=0,
            )
        self.set_state("IDLE")
        state.worker_summary = f"fix: {result.summary}"
        return AgentResult(ok=True, summary=state.worker_summary)

    async def _run_task(
        self,
        state: StoryState,
        aci: ACI,
        number: int,
        text: str,
        spec: str,
        plan: str,
        tasks_md: str,
    ) -> AgentResult:
        retry_ctx = ""
        if state.failure_history:
            retry_ctx = (
                "\n## Última falha (já filtrada) — corrija isto primeiro\n"
                + state.failure_history[-1][:2500]
                + "\n"
            )
        notes = (
            (
                "\n## Orientações do Founder\n"
                + "\n".join(f"- {n}" for n in state.founder_notes)
                + "\n"
            )
            if state.founder_notes
            else ""
        )
        # Prompt-cache friendly layout: everything that is identical across the tasks of a story
        # (rules, constitution, spec, plan, allowed paths) goes first, in the system block, so the
        # provider's prefix cache (DeepSeek/Gemini automatic, Anthropic explicit) hits on every call.
        # Only the task-specific part changes per call.
        stable = (
            SYSTEM.format(language=self.language)
            + f"\n## Story {state.story_id} — {state.title}\n\n## Allowed paths\n{json.dumps(state.allowed_paths, ensure_ascii=False)}\n\n"
            f"## Spec\n{spec[:4000]}\n\n## Plan\n{plan[:4000]}\n\n## Constitution (excerpt)\n{self.constitution(3000)}\n"
        )
        user = (
            f"# Task T{number}\n\n**{text}**\n{retry_ctx}{notes}\n## Checklist\n{tasks_md[:2000]}\n"
        )
        messages = [Message("system", stable, cache=True), Message("user", user)]
        sched = self.ctx.config.schedule
        loop = await self.tool_loop(
            messages,
            Toolbox(aci, PROFILES["worker"]),
            story=state,
            max_iterations=sched.worker_max_iterations,
            tier_override=self.tier_override,
            terminal=("done", "blocked"),
            nudge="Continue com as ferramentas, ou chame `done` se a tarefa está completa e verde.",
            keep_tool_results=sched.worker_keep_tool_results,
        )
        if loop.ended_by == "done":
            return AgentResult(ok=True, summary=str(loop.args.get("summary", ""))[:300])
        if loop.ended_by == "blocked":
            opts = loop.args.get("options") or []
            return AgentResult(
                ok=False,
                blocked_reason=str(loop.args.get("reason", "")),
                blocked_options=[str(o) for o in opts][:3] or None,
            )
        if loop.ended_by == "text":  # the model stopped without `done`: accept what it said
            return AgentResult(ok=True, summary=loop.text[:200] or "tarefa encerrada")
        return AgentResult(ok=False, summary="limite de iterações atingido", blocked_reason=None)

    async def _dod_check(
        self, state: StoryState, wt: Worktree, task: str, summary: str
    ) -> list[str]:
        """One small tier2 call; empty list means done (or the check is unavailable)."""
        if self.ctx.dry_run:
            return []
        diff = self.ctx.worktrees.diff_working(wt, max_chars=8000)
        if not diff.strip():
            return ["nenhuma alteração de código foi feita para esta tarefa"]
        messages = [
            Message("system", DOD_SYSTEM.format(language=self.language)),
            Message(
                "user",
                f"# Task\n{task}\n\n# Worker summary\n{summary}\n\n# Diff\n```diff\n{diff}\n```",
            ),
        ]
        try:
            routed = await self.ctx.router.complete(
                self.role,
                messages,
                agent=self.name,
                story_id=state.story_id,
                json_mode=True,
                max_tokens=600,
                complexity=str(state.complexity),
            )
            from loompa.llm.providers import extract_json

            data = extract_json(routed.response.text)
        except Exception:  # noqa: BLE001 - advisory
            return []
        if not isinstance(data, dict) or data.get("complete", True):
            return []
        return [str(m).strip() for m in data.get("missing") or [] if str(m).strip()]

    def _flush_learnings(self, state: StoryState, aci: ACI) -> None:
        for item in aci.learnings:
            if item not in state.learnings:
                state.learnings.append(item)
        aci.learnings.clear()
