"""OpenCode backend for the Worker Loompa (Fase 2 spike, ADR-0007).

Same job as `WorkerAgent` (one commit per pending task) but the coding step is delegated to the
`opencode` CLI running inside the story's isolated worktree, instead of Loompa's own ACI tool
loop. Ops still owns retries/cooldowns (`OpsAgent.on_failure`): this agent never retries by
itself, it just raises and lets the crash bubble up to the runtime's generic handler.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.finance import UsageRecord
from loompa.speckit import story_dir, tasks_from_markdown
from loompa.speckit.artifacts import mark_task_done
from loompa.worktrees import DEPLOYER_ONLY, Worktree

AGENT_SYSTEM = """You are the Worker Loompa, a senior full-stack engineer executing ONE task from a
checklist inside an isolated git worktree that IS your entire workspace. Work surgically:
1. Read the relevant files first. Search before assuming names or signatures.
2. Implement exactly the task, with tests. Do not touch files outside these paths: {allowed_paths}.
3. Run the test command and fix failures until they pass: `{test_command}`.
4. Never rewrite unrelated code, never add dependencies, keep diffs minimal.
Finish your answer with one line: `DONE: <one-sentence summary in {language}>`, or, if a human
decision is required (ambiguous requirement, missing credential, destructive change),
`BLOCKED: <plain-language reason in {language}>`. Do not guess.
"""


@dataclass
class _ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


class OpenCodeWorker(LoompaAgent):
    role = "worker"
    display = "Worker Loompa (OpenCode)"

    def __init__(self, ctx, *, name: str | None = None, tier_override: str | None = None):
        super().__init__(ctx, name=name or self.display)
        self.tier_override = tier_override

    async def run(self, state: StoryState, wt: Worktree) -> AgentResult:
        cfg = self.ctx.config.worker
        binary = shutil.which(cfg.opencode_bin)
        if not binary:
            raise RuntimeError(
                f"binário `{cfg.opencode_bin}` do OpenCode não foi encontrado no PATH"
            )
        paths = story_dir(self.ctx.root, state.story_id)
        tasks_md = paths.tasks.read_text(encoding="utf-8") if paths.tasks.is_file() else ""
        tasks = tasks_from_markdown(tasks_md)
        pending = [t for t in tasks if t.number not in state.tasks_done]
        if not pending:
            return AgentResult(ok=True, summary="todas as tarefas já concluídas")
        spec = paths.spec.read_text(encoding="utf-8") if paths.spec.is_file() else ""
        plan = paths.plan.read_text(encoding="utf-8") if paths.plan.is_file() else ""
        model = self._model_for(state)
        self._write_agent_config(wt, state)
        summaries: list[str] = []
        for task in pending:
            self.set_state(
                "WORKING",
                state,
                model=f"opencode:{model}",
                detail=f"T{task.number}: {task.text[:60]}",
            )
            result = await self._run_task(
                state, wt, binary, model, task.number, task.text, spec, plan, tasks_md
            )
            if result.blocked_reason:
                self.set_state("BLOCKED", state, detail="aguardando decisão")
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
        self.set_state("IDLE")
        state.worker_summary = "\n".join(summaries)
        return AgentResult(ok=True, summary=state.worker_summary)

    def _model_for(self, state: StoryState) -> str:
        _, candidates = self.ctx.router.candidates(
            self.role, self.tier_override, str(state.complexity)
        )
        if not candidates:
            raise RuntimeError("nenhum modelo configurado para o Worker")
        c = candidates[0]
        return f"{c.provider}/{c.model}"

    def _write_agent_config(self, wt: Worktree, state: StoryState) -> None:
        cfg = self.ctx.config.worker
        allowed = state.allowed_paths or ["**"]
        agents_dir = wt.path / ".opencode" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        edit_globs = "\n".join(f'    "{p}": allow' for p in allowed) + '\n    "*": deny'
        # Same rule as the Loompa-side guard: only the Deployer changes shared history. OpenCode
        # applies the last matching pattern, so the denials come after the wildcard.
        bash_rules = '    "*": allow\n' + "\n".join(
            f'    "{pattern}": deny'
            for sub in sorted(DEPLOYER_ONLY)
            for pattern in (f"git {sub}*", f"git * {sub}*")
        )
        bash_rules += '\n    "git branch -D*": deny\n    "git branch -d*": deny\n    "gh *": deny'
        body = (
            "---\n"
            "description: Loompa Worker — one task at a time, minimal diff, tests green.\n"
            "mode: subagent\n"
            "permission:\n"
            "  read: allow\n"
            "  bash:\n"
            f"{bash_rules}\n"
            "  edit:\n"
            f"{edit_globs}\n"
            "---\n\n"
            + AGENT_SYSTEM.format(
                allowed_paths=json.dumps(allowed, ensure_ascii=False),
                test_command=self.ctx.config.quality.test_command or "(nenhum configurado)",
                language=self.language,
            )
        )
        (agents_dir / f"{cfg.opencode_agent}.md").write_text(body, encoding="utf-8")

    async def _run_task(
        self,
        state: StoryState,
        wt: Worktree,
        binary: str,
        model: str,
        number: int,
        text: str,
        spec: str,
        plan: str,
        tasks_md: str,
    ) -> AgentResult:
        cfg = self.ctx.config.worker
        prompt = (
            f"# Story {state.story_id} — {state.title}\n\n"
            f"## Spec\n{spec[:4000]}\n\n## Plan\n{plan[:4000]}\n\n"
            f"## Task T{number}\n{text}\n\n## Checklist\n{tasks_md[:2000]}\n"
        )
        argv = [
            binary,
            "run",
            "--format",
            "json",
            "--agent",
            cfg.opencode_agent,
            "--model",
            model,
            prompt,
        ]
        res = await self._exec(
            argv, cwd=wt.path, timeout=cfg.opencode_timeout_s, env=self._child_env(model)
        )
        self._record_cost(state, model, prompt, res.output)
        if res.timed_out:
            raise TimeoutError(f"opencode excedeu {cfg.opencode_timeout_s}s na tarefa T{number}")
        if not res.ok:
            raise RuntimeError(
                f"opencode terminou com erro na tarefa T{number}: {res.output[-800:]}"
            )
        summary, blocked = self._parse_output(res.stdout)
        if blocked:
            return AgentResult(ok=False, blocked_reason=blocked)
        return AgentResult(ok=True, summary=summary[:300] or "tarefa encerrada")

    @staticmethod
    def _parse_output(stdout: str) -> tuple[str, str | None]:
        """`opencode run --format json` prints one JSON object (or a JSON array of steps); fall
        back to plain text if the shape is unexpected — the DONE:/BLOCKED: marker still works."""
        text = stdout.strip()
        try:
            data = json.loads(text)
            if isinstance(data, list):
                data = data[-1] if data else {}
            if isinstance(data, dict):
                text = str(data.get("text") or data.get("summary") or data.get("output") or text)
        except (json.JSONDecodeError, TypeError):
            pass
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.upper().startswith("BLOCKED:"):
                return "", line.split(":", 1)[1].strip()
            if line.upper().startswith("DONE:"):
                return line.split(":", 1)[1].strip(), None
        return text[:300], None

    def _child_env(self, model: str) -> dict[str, str]:
        """OpenCode is another process and does not read Loompa's secrets files, so a key stored by
        `loompa setup` would never reach it. It gets the one key its model needs, not the others:
        the agent runs shell commands and should not hold keys it has no use for."""
        provider = self.ctx.config.providers.get(model.partition("/")[0])
        key = (
            self.ctx.secrets.get(provider.api_key_env)
            if provider and provider.api_key_env
            else None
        )
        return {provider.api_key_env: key} if provider and key else {}

    async def _exec(
        self, argv: list[str], *, cwd: Path, timeout: int, env: dict[str, str] | None = None
    ) -> _ExecResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **(env or {}), "CI": "1", "NO_COLOR": "1"},
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RuntimeError(f"não foi possível executar `{argv[0]}`: {exc}") from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            timed_out = False
        except TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            timed_out = True
        return _ExecResult(
            returncode=proc.returncode or 0,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            timed_out=timed_out,
        )

    def _record_cost(self, state: StoryState, model: str, prompt: str, output: str) -> None:
        """No token counts from the subprocess: approximate via chars/4, same pricing table as
        the router. Tagged tier `opencode` so the finance view can tell the spike calls apart."""
        if not self.ctx.tracker:
            return
        provider, _, model_name = model.partition("/")
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(output) // 4)
        cost = self.ctx.tracker.record(
            UsageRecord(
                agent=self.name,
                role=self.role,
                provider=provider,
                model=model_name,
                tier="opencode",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                story_id=state.story_id,
            )
        )
        self.ctx.emit(
            "llm.call",
            agent=self.name,
            role=self.role,
            model=model_name,
            tier="opencode",
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            approx=True,
        )
