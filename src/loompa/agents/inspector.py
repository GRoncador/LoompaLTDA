"""Inspector Loompa: binary PASS/FAIL quality gate.

Tier 3 first (tests, lint, typecheck — $0), then an optional Tier 2 acceptance-criteria judge over
the diff. Optional CodeRabbit CLI review when enabled in config.
"""

from __future__ import annotations

import shutil

from loompa.aci import run_command, summarize_lint, summarize_tests, summarize_typecheck
from loompa.agents.base import AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.speckit import story_dir
from loompa.worktrees import Worktree

JUDGE_SYSTEM = """<!-- role:inspector -->
You are the Inspector Loompa (QA). Given the acceptance criteria and the diff, decide for EACH criterion
whether the implementation plus its tests demonstrably satisfy it. Be strict and binary.
Respond with JSON only: {{"verdict": "PASS"|"FAIL", "criteria": [{{"text": str, "pass": bool, "reason": str}}],
"summary": str}}. Write reasons in {language}, one sentence each, no stack traces.
"""


class InspectorAgent(LoompaAgent):
    role = "inspector"
    display = "Inspector Loompa"

    async def run(self, state: StoryState, wt: Worktree) -> AgentResult:
        self.set_state("TESTING", state, detail="rodando testes e linters")
        q = self.ctx.config.quality
        parts: list[str] = []
        ok = True
        if q.test_command:
            res = await run_command(q.test_command, wt.path, timeout=900)
            summary = summarize_tests(res.output, res.returncode)
            if res.timed_out:
                ok = False
                parts.append("[tests] FAIL: tempo esgotado")
            else:
                ok = ok and summary.ok
                parts.append(summary.compact())
            self.ctx.emit(
                "inspector.tests",
                story_id=state.story_id,
                agent=self.name,
                ok=summary.ok,
                passed=summary.passed,
                failed=summary.failed,
            )
        else:
            parts.append("[tests] nenhum comando de teste configurado — gate de testes ignorado")
        if q.lint_command:
            res = await run_command(q.lint_command, wt.path, timeout=300)
            s = summarize_lint(res.output, res.returncode)
            ok = ok and s.ok
            parts.append(s.compact())
        if q.typecheck_command:
            res = await run_command(q.typecheck_command, wt.path, timeout=600)
            s = summarize_typecheck(res.output, res.returncode)
            ok = ok and s.ok
            parts.append(s.compact())
        if (
            ok
            and q.coderabbit.enabled
            and q.coderabbit.mode == "cli"
            and shutil.which("coderabbit")
        ):
            res = await run_command("coderabbit review --plain", wt.path, timeout=600)
            parts.append(
                "[coderabbit] " + ("sem apontamentos críticos" if res.ok else res.output[-1500:])
            )
            ok = ok and res.ok
        if ok and state.acceptance and not self.ctx.dry_run:
            judge = await self._judge(state, wt)
            if judge is not None:
                ok = ok and judge["verdict"] == "PASS"
                failed = [c for c in judge.get("criteria", []) if not c.get("pass")]
                parts.append(
                    f"[acceptance] {judge['verdict']}"
                    + (
                        "\n"
                        + "\n".join(
                            f"- {c.get('text', '')[:120]}: {c.get('reason', '')[:200]}"
                            for c in failed
                        )
                        if failed
                        else ""
                    )
                )
        report = "\n".join(parts)
        state.last_test_summary = report
        self.set_state("IDLE")
        self.ctx.emit(
            "inspector.verdict",
            story_id=state.story_id,
            agent=self.name,
            verdict="PASS" if ok else "FAIL",
        )
        return AgentResult(ok=ok, summary=report)

    async def _judge(self, state: StoryState, wt: Worktree) -> dict | None:
        diff = self.ctx.worktrees.diff(wt, max_chars=16000)
        if not diff.strip():
            return {
                "verdict": "FAIL",
                "criteria": [
                    {"text": c, "pass": False, "reason": "nenhuma alteração de código foi feita"}
                    for c in state.acceptance
                ],
                "summary": "diff vazio",
            }
        paths = story_dir(self.ctx.root, state.story_id)
        spec = paths.spec.read_text(encoding="utf-8")[:3000] if paths.spec.is_file() else ""
        user = (
            "## Acceptance criteria\n"
            + "\n".join(f"- {c}" for c in state.acceptance)
            + f"\n\n## Spec excerpt\n{spec}\n\n## Diff\n```diff\n{diff}\n```"
        )
        try:
            data = await self.ask_json(
                JUDGE_SYSTEM.format(language=self.language), user, story=state, max_tokens=2000
            )
        except Exception:  # noqa: BLE001 - judge is advisory when the LLM is unavailable
            return None
        verdict = str(data.get("verdict", "FAIL")).upper()
        data["verdict"] = "PASS" if verdict == "PASS" else "FAIL"
        return data
