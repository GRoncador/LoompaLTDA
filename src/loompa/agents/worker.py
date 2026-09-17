"""Worker Loompa: implements tasks.md inside an isolated worktree using only ACI tools.

Each task runs in a fresh, ephemeral conversation (zero-context paradigm) and ends in one
semantic commit. The Worker never sees raw terminal output — only ACI-compacted results.
"""

from __future__ import annotations

import json

from loompa.aci import ACI
from loompa.agents.base import AgentResult, LoompaAgent
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
        user = (
            f"# Task T{number} of story {state.story_id} — {state.title}\n\n**{text}**\n{retry_ctx}{notes}\n"
            f"## Allowed paths\n{json.dumps(state.allowed_paths, ensure_ascii=False)}\n\n"
            f"## Spec\n{spec[:4000]}\n\n## Plan\n{plan[:4000]}\n\n## Checklist\n{tasks_md[:2000]}\n\n"
            f"## Constitution (excerpt)\n{self.constitution(3000)}\n"
        )
        messages = [Message("system", SYSTEM.format(language=self.language)), Message("user", user)]
        max_iter = self.ctx.config.schedule.worker_max_iterations
        last_text = ""
        for i in range(max_iter):
            routed = await self.ctx.router.complete(
                self.role,
                messages,
                agent=self.name,
                story_id=state.story_id,
                tools=aci.spec(),
                tier_override=self.tier_override,
            )
            resp = routed.response
            last_text = resp.text or last_text
            if not resp.tool_calls:
                # model stopped without calling done: nudge once, then accept
                if i < max_iter - 1 and "done" not in (resp.text or "").lower():
                    messages += [
                        Message("assistant", resp.text),
                        Message(
                            "user",
                            "Continue com as ferramentas, ou chame `done` se a tarefa está completa e verde.",
                        ),
                    ]
                    continue
                return AgentResult(ok=True, summary=last_text[:200] or "tarefa encerrada")
            messages.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                if call.name == "done":
                    return AgentResult(
                        ok=True, summary=str(call.arguments.get("summary", ""))[:300]
                    )
                if call.name == "blocked":
                    opts = call.arguments.get("options") or []
                    return AgentResult(
                        ok=False,
                        blocked_reason=str(call.arguments.get("reason", "")),
                        blocked_options=[str(o) for o in opts][:3] or None,
                    )
                result = await aci.call(call.name, call.arguments)
                self.ctx.emit(
                    "tool.call",
                    story_id=state.story_id,
                    agent=self.name,
                    tool=call.name,
                    ok=result.ok,
                )
                messages.append(
                    Message("tool", result.output[:12000], tool_call_id=call.id, name=call.name)
                )
        return AgentResult(ok=False, summary="limite de iterações atingido", blocked_reason=None)

    def _flush_learnings(self, state: StoryState, aci: ACI) -> None:
        for item in aci.learnings:
            if item not in state.learnings:
                state.learnings.append(item)
        aci.learnings.clear()
