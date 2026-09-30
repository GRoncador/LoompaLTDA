"""Worker Loompa: implements tasks.md inside an isolated worktree using only ACI tools.

Each task runs in a fresh, ephemeral conversation (zero-context paradigm) and ends in one
semantic commit. The Worker never sees raw terminal output — only ACI-compacted results.
"""

from __future__ import annotations

import json

from loompa.aci import ACI, run_command, summarize_tests
from loompa.agents.architect import REPRO_KEY, writable_tests
from loompa.agents.base import AgentResult, LoompaAgent, repo_outline
from loompa.agents.loopguard import LoopGuard
from loompa.agents.toolbox import PROFILES, Toolbox, prune_tool_history  # noqa: F401 (re-exported)
from loompa.engine.state import StoryKind, StoryState
from loompa.hygiene import scan_diff
from loompa.llm import Message
from loompa.speckit import story_dir, tasks_from_markdown
from loompa.speckit.artifacts import mark_task_done
from loompa.worktrees import Worktree

SYSTEM = """<!-- role:worker -->
You are the Worker Loompa, a senior full-stack engineer executing ONE task from a checklist inside an
isolated git worktree. You have no shell — only the tools provided. Work surgically:
1. Read before you write: read the file you will change and the tests that cover it (an edit to a
   file you have not read in this task is refused). Use `find_symbol`/`search` to locate names and
   the repository outline below instead of listing directories. Read each thing once: results stay
   in your history, and a repeated read or test run with nothing changed is answered from it.
   Then decide: a few reads are enough for most tasks.
2. Implement exactly the task, with tests. Do not touch files outside the allowed paths; if you need
   to, call `note_learning` describing why and finish what you can. When you are fixing a failure,
   the first edit's `reason` states the root cause you found (not the symptom).
3. A write reports syntax errors and undefined names at once, under `[quick check]`: fix those
   first. Then run `run_tests` (and `run_lint` when configured) and fix failures until they pass. For
   mechanical lint findings (import order, spacing, formatting) call `fix_lint` instead of editing
   by hand.
4. When the task is complete and green, call `done` with a one-sentence summary.
5. If a decision requires a human (ambiguous requirement, missing credential, destructive change),
   call `blocked` with a plain-language reason in {language} and 2-3 options. Do not guess.
Never rewrite unrelated code, never add dependencies, keep diffs minimal, follow the constitution.
The commit is made for you after each task, with a semantic message: never try to commit, and a
task that mentions a commit is done when its code and tests are.
"""


MAX_CONFLICT_CHARS = 20_000

CONFLICT_SYSTEM = """<!-- role:worker -->
You resolve git merge conflicts. The base branch was merged into a story branch. In each file,
the part between `<<<<<<<` and `=======` is the story's version; between `=======` and `>>>>>>>`
the base's, which holds work already approved (fixes, other features). Keep the intent of both
sides: combine them, do not drop either unless they truly say the same thing. Every function,
class, constant and signature the base's version defines must still exist, unchanged, after the
merge: code already merged into the base calls them (in `contas` a merged `storage.py` lost a
function the base's CLI imported). Keep the story's additions next to them. Return every file in
full, with no conflict marker left.
Respond with JSON only: {{"files": {{"<path>": "<full resolved content>"}}}}
Comments in {language}, if any.
"""

DOD_SYSTEM = """<!-- role:dod -->
You are the Worker Loompa doing a definition-of-done self-check right after finishing a task.
Given the task, your own summary and the diff you produced, answer honestly whether the task is
really complete: code AND tests present, nothing outside the task touched, no TODO left behind.
What earlier tasks of the story already committed counts: if the task's result is already there,
the task is complete.
Committing is the orchestrator's job, done right after this check: never list a commit, a commit
message or running git as missing.
Respond with JSON only: {{"complete": bool, "missing": [str]}} — `missing` lists concrete things
still to do (in {language}); empty when complete.
"""


REPRODUCER_TASK = """

This is the reproducer task of a bugfix: write ONLY the automated test(s) that reproduce the bug
described in the spec. The test must FAIL on the current code, for the bug's reason (not an
import error or a typo). Only test files are writable in this task; the fix comes next."""

REPRODUCER_GREEN = """

The test you wrote PASSES on the current code, so it does not reproduce the bug. Change it so it
exercises the failing behaviour the spec describes, and check with `run_tests` that it fails."""

_BROKEN_TEST = ("ImportError", "ModuleNotFoundError", "SyntaxError", "NameError", "fixture '")


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
        outline = repo_outline(wt.path)  # once per run: the same text keeps the prefix cached
        summaries: list[str] = []
        tier_label = self.tier_override or "tier2"
        repro = state.extra.get(REPRO_KEY) or {}
        for task in pending:
            self.set_state(
                "WORKING", state, model=tier_label, detail=f"T{task.number}: {task.text[:60]}"
            )
            reproducer = repro.get("status") == "pending" and repro.get("task") == task.number
            task_aci = aci
            if reproducer:  # 7.7: tests only, and the code proves them red before the fix
                fence = writable_tests(state.allowed_paths) or ["tests/"]
                task_aci = self.ctx.aci_for(wt.path, allowed_paths=fence)
            result = await self._run_task(
                state,
                task_aci,
                task.number,
                task.text + (REPRODUCER_TASK if reproducer else ""),
                spec,
                plan,
                tasks_md,
                outline=outline,
                changed=self.ctx.worktrees.diff_stat(wt),
            )
            if reproducer and not result.blocked_reason:
                result = await self._prove_reproduced(
                    state, wt, task_aci, task.number, task.text, spec, plan, tasks_md, outline
                )
                self._flush_learnings(state, task_aci)
            if result.blocked_reason:
                self.set_state("BLOCKED", state, detail="aguardando decisão")
                self._flush_learnings(state, aci)
                return result
            # deterministic hygiene first ($0, also in dry-run), then the model's self-check
            dirty = scan_diff(self.ctx.worktrees.diff_working(wt, max_chars=200_000))
            if dirty:
                self.ctx.emit(
                    "worker.hygiene",
                    story_id=state.story_id,
                    agent=self.name,
                    task=task.number,
                    issues=[i.line() for i in dirty[:8]],
                )
            missing = [f"Diff hygiene: {i.line()}" for i in dirty[:5]]
            if not reproducer:  # a red test is the reproducer's goal, not something missing
                missing += await self._dod_check(state, wt, task.text, result.summary)
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
                    + "\n".join(f"- {m}" for m in missing[:8]),
                    spec,
                    plan,
                    tasks_md,
                    outline=outline,
                    changed=self.ctx.worktrees.diff_stat(wt),
                )
                if followup.blocked_reason:
                    self.set_state("BLOCKED", state, detail="aguardando decisão")
                    self._flush_learnings(state, aci)
                    return followup
                result.summary = f"{result.summary} / {followup.summary}"
            kind = "test" if reproducer else "feat"
            commit = self.ctx.worktrees.commit_all(
                wt, f"{kind}({state.story_id.lower()}): {task.text[:60]}"
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

    async def _prove_reproduced(
        self,
        state: StoryState,
        wt: Worktree,
        aci: ACI,
        number: int,
        text: str,
        spec: str,
        plan: str,
        tasks_md: str,
        outline: str,
    ) -> AgentResult:
        """Run the suite after the reproducer task: it must fail anew. A green run gets one more
        round; a test that still does not fail is recorded and the fix goes on (never blocks)."""
        repro = state.extra.setdefault(REPRO_KEY, {"task": number})
        result = AgentResult(ok=True, summary="teste de reprodução escrito")
        for attempt in range(2):
            failing = await self._new_failures(state, wt)
            if failing is None:
                repro["status"] = "unverified"  # no test command, or dry-run
                break
            if failing:
                repro.update(status="red", failing=failing[:10])
                break
            if attempt == 1:
                repro["status"] = "not_reproduced"
                state.learnings.append(
                    {
                        "kind": "tech_debt",
                        "title": "O teste de reprodução do bug não falhou antes da correção",
                        "detail": f"Em {state.story_id} o teste escrito para reproduzir o bug "
                        "passou no código antigo; a correção seguiu sem essa prova.",
                    }
                )
                break
            result = await self._run_task(
                state,
                aci,
                number,
                text + REPRODUCER_TASK + REPRODUCER_GREEN,
                spec,
                plan,
                tasks_md,
                outline=outline,
                changed=self.ctx.worktrees.diff_stat(wt),
            )
            if result.blocked_reason:
                return result
        self.ctx.emit(
            "bugfix.reproducer",
            story_id=state.story_id,
            agent=self.name,
            status=repro.get("status"),
            failing=repro.get("failing", []),
        )
        return result

    async def _new_failures(self, state: StoryState, wt: Worktree) -> list[str] | None:
        """Tests failing now that were not failing on the base; None when that cannot be run.
        A test that breaks on an import or a typo does not reproduce anything."""
        command = self.ctx.config.quality.test_command
        if not command or self.ctx.dry_run:
            return None
        res = await run_command(command, wt.path, timeout=900)
        summary = summarize_tests(res.output, res.returncode)
        baseline = set((state.extra.get("baseline") or {}).get("failing") or [])
        return [
            f.name
            for f in summary.failures
            if f.name not in baseline
            and not any(b in f"{f.message} {' '.join(f.frames)}" for b in _BROKEN_TEST)
        ]

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
            outline=repo_outline(wt.path),
            changed=self.ctx.worktrees.diff_stat(wt),
            diagnosis=True,
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

    async def autofix_lint(self, state: StoryState, wt: Worktree) -> bool:
        """Self-healing without a model (Fase 7, 7.2): the tests are green and only the linter
        complains (`contas` S-003 and S-006 failed on an import order), so its own fixes run,
        narrowed to the plan's paths, and are committed. True when something changed."""
        aci = self.ctx.aci_for(wt.path, allowed_paths=state.allowed_paths or None)
        await aci.tool_fix_lint()
        commit = self.ctx.worktrees.commit_all(wt, f"style({state.story_id.lower()}): lint fixes")
        if commit is None:
            return False
        state.commits.append(commit.sha)
        self.ctx.emit(
            "worktree.commit",
            story_id=state.story_id,
            agent=self.name,
            sha=commit.sha,
            files=commit.files,
            task=0,
        )
        return True

    async def resolve_conflicts(
        self, state: StoryState, wt: Worktree, files: list[str]
    ) -> AgentResult:
        """The base merged into the story left conflict markers in `files`. One structured call
        with every conflicted file in the prompt returns each file resolved; the ACI writes them
        (fenced to those files) and the Deployer checks for leftover markers. A tool loop was
        tried first: in `contas` S-001 it only kept re-reading the files and never wrote."""
        aci = self.ctx.aci_for(wt.path, allowed_paths=files)
        self.set_state("WORKING", state, detail=f"resolvendo conflito em {len(files)} arquivo(s)")
        blocks, skipped = [], []
        for rel in files:
            text = (wt.path / rel).read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_CONFLICT_CHARS:
                skipped.append(rel)
                continue
            blocks.append(f"## {rel}\n```\n{text}\n```")
        if not blocks:
            return AgentResult(ok=False, summary="arquivos grandes demais para resolver assim")
        data = await self.ask_json(
            CONFLICT_SYSTEM.format(language=self.language),
            f"# Story {state.story_id}: {state.title}\n\n" + "\n\n".join(blocks),
            story=state,
            max_tokens=max(2000, sum(len(b) for b in blocks) // 2),
        )
        resolved = data.get("files") if isinstance(data.get("files"), dict) else {}
        written = []
        for rel, content in resolved.items():
            if rel in files and rel not in skipped and isinstance(content, str):
                res = await aci.call("write_file", {"path": rel, "content": content})
                if res.ok:
                    written.append(rel)
        self.set_state("IDLE")
        return AgentResult(
            ok=bool(written), summary=f"resolvidos: {', '.join(written) or 'nenhum'}"
        )

    async def _run_task(
        self,
        state: StoryState,
        aci: ACI,
        number: int,
        text: str,
        spec: str,
        plan: str,
        tasks_md: str,
        *,
        outline: str = "",
        changed: str = "",
        diagnosis: bool = False,
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
            + (
                f"\n## Repository outline (at the start of this run)\n{outline}\n"
                if outline
                else ""
            )
        )
        done_before = (
            f"\n## Already changed in this story (earlier commits)\n{changed.strip()[-1500:]}\n"
            if changed.strip()
            else ""
        )
        user = f"# Task T{number}\n\n**{text}**\n{retry_ctx}{notes}{done_before}\n## Checklist\n{tasks_md[:2000]}\n"
        messages = [Message("system", stable, cache=True), Message("user", user)]
        sched = self.ctx.config.schedule
        aci.begin_task()
        aci.require_read = True
        # a fix (the Inspector's findings, a bug) starts from a diagnosis, stated with the edit
        toolbox = Toolbox(
            aci, PROFILES["worker"], diagnosis=diagnosis or state.kind == StoryKind.BUGFIX
        )
        guard = LoopGuard(aci, explore_nudge=sched.worker_explore_nudge)
        loop = await self.tool_loop(
            messages,
            toolbox,
            story=state,
            max_iterations=sched.worker_max_iterations,
            tier_override=self.tier_override,
            terminal=("done", "blocked"),
            nudge="Continue com as ferramentas, ou chame `done` se a tarefa está completa e verde.",
            keep_tool_results=sched.worker_keep_tool_results,
            keep_files_chars=sched.worker_keep_file_chars,
            guard=guard,
        )
        self.ctx.emit(
            "worker.task",
            story_id=state.story_id,
            agent=self.name,
            task=number,
            ended_by=loop.ended_by,
            tool_calls=loop.tool_calls,
            repeats=guard.repeats,
            nudges=guard.nudges,
            diagnosis=toolbox.diagnosis[:300],
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
        # A task whose result an earlier task already committed is done: judging only the
        # uncommitted diff sent such tasks into a whole follow-up round (`contas` S-005).
        committed = self.ctx.worktrees.diff(wt, max_chars=6000)
        if not diff.strip() and not committed.strip():
            return ["nenhuma alteração de código foi feita para esta tarefa"]
        messages = [
            Message("system", DOD_SYSTEM.format(language=self.language)),
            Message(
                "user",
                f"# Task\n{task}\n\n# Worker summary\n{summary}\n\n"
                f"# Diff of this task (not committed yet)\n```diff\n"
                f"{diff or '(nenhuma mudança nova nesta tarefa)'}\n```\n\n"
                f"# Already committed in this story by earlier tasks\n```diff\n"
                f"{committed or '(nada)'}\n```",
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
