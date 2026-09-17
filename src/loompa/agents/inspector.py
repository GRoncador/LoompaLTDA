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

    async def baseline(self, state: StoryState, wt: Worktree) -> dict:
        """Run the Tier 3 checks on the untouched worktree so pre-existing failures are not
        blamed on the story (brownfield repos are often red on main)."""
        q = self.ctx.config.quality
        base: dict = {"tests_ok": True, "failing": [], "lint_ok": True}
        if q.test_command:
            res = await run_command(q.test_command, wt.path, timeout=900)
            summary = summarize_tests(res.output, res.returncode)
            base["tests_ok"] = summary.ok and not res.timed_out
            base["failing"] = sorted({f.name for f in summary.failures})
        if q.lint_command:
            res = await run_command(q.lint_command, wt.path, timeout=300)
            base["lint_ok"] = summarize_lint(res.output, res.returncode).ok
        if not base["tests_ok"] or not base["lint_ok"]:
            state.learnings.append(
                {
                    "kind": "tech_debt",
                    "title": "A suíte de verificações já falha na versão principal",
                    "detail": "Falhas pré-existentes: "
                    + (", ".join(base["failing"][:8]) or "lint")
                    + ". A fábrica ignora essas falhas nas histórias até serem corrigidas.",
                }
            )
            self.ctx.emit(
                "inspector.baseline_red",
                story_id=state.story_id,
                agent=self.name,
                failing=base["failing"][:20],
            )
        return base

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
                baseline = set((state.extra.get("baseline") or {}).get("failing") or [])
                new_failures = [f for f in summary.failures if f.name not in baseline]
                if summary.ok or (baseline and not new_failures and not summary.errors):
                    parts.append(
                        summary.compact()
                        if summary.ok
                        else "[pytest] PASS (apenas falhas pré-existentes na base)"
                    )
                else:
                    ok = False
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
            if s.ok or (state.extra.get("baseline") or {}).get("lint_ok") is False:
                parts.append(
                    s.compact() if s.ok else "[lint] PASS (apontamentos pré-existentes na base)"
                )
            else:
                ok = False
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
