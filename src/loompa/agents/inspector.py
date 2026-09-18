"""Inspector Loompa: graded quality gate (ADR-0006).

Layers by cost: deterministic tooling (tests, lint, typecheck — $0) → optional scanner
(CodeRabbit CLI) → LLM judge over the diff. The judge checks each acceptance criterion and lists
findings with a prefix (SEC-/PERF-/TEST-/ARCH-) and a severity. Verdicts:

* PASS      — everything green, no findings.
* CONCERNS  — green, but medium/low findings; the story ships and the findings feed Kaizen.
* FAIL      — red tooling or an unmet acceptance criterion; climbs the escalation ladder.
* WAIVED    — green tooling and criteria, but a high-severity finding; the Founder decides.
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
Also review the diff for findings a test suite would not catch, each with a prefix and a severity:
SEC- (security: injection, secrets, unsafe deserialization, auth bypass), PERF- (obvious N+1, unbounded
loops, blocking I/O in async code), TEST- (missing or tautological tests), ARCH- (violates the
constitution or the plan, wrong layer, duplicated logic). Severity: high = must not ship as is;
medium = should be fixed soon; low = nit.
Respond with JSON only: {{"criteria": [{{"text": str, "pass": bool, "reason": str}}],
"findings": [{{"prefix": "SEC"|"PERF"|"TEST"|"ARCH", "severity": "high"|"medium"|"low", "text": str}}],
"summary": str}}. Write reasons in {language}, one sentence each, no stack traces.
"""

SEVERITIES = ("low", "medium", "high")
PREFIXES = ("SEC", "PERF", "TEST", "ARCH")


def grade(criteria_ok: bool, findings: list[dict[str, str]], tooling_ok: bool) -> str:
    """The verdict rule, kept deterministic and testable (see module docstring)."""
    if not tooling_ok or not criteria_ok:
        return "FAIL"
    if any(f.get("severity") == "high" for f in findings):
        return "WAIVED"
    if findings:
        return "CONCERNS"
    return "PASS"


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
        tooling_ok = ok
        criteria_ok = True
        findings: list[dict[str, str]] = []
        if ok and state.acceptance and not self.ctx.dry_run:
            judge = await self._judge(state, wt)
            if judge is not None:
                failed = [c for c in judge.get("criteria", []) if not c.get("pass")]
                criteria_ok = not failed
                findings = judge.get("findings", [])
                parts.append(
                    f"[acceptance] {'PASS' if criteria_ok else 'FAIL'}"
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
                if findings:
                    parts.append(
                        "[findings]\n"
                        + "\n".join(
                            f"- {f['id']} ({f['severity']}): {f['text'][:200]}" for f in findings
                        )
                    )
        verdict = grade(criteria_ok, findings, tooling_ok)
        ok = verdict in ("PASS", "CONCERNS")
        report = "\n".join(parts)
        state.last_test_summary = report
        self.set_state("IDLE")
        self.ctx.emit(
            "inspector.verdict",
            story_id=state.story_id,
            agent=self.name,
            verdict=verdict,
            findings=len(findings),
        )
        return AgentResult(ok=ok, summary=report, data={"verdict": verdict, "findings": findings})

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
                JUDGE_SYSTEM.format(language=self.language), user, story=state, max_tokens=2500
            )
        except Exception:  # noqa: BLE001 - judge is advisory when the LLM is unavailable
            return None
        criteria = [c for c in data.get("criteria") or [] if isinstance(c, dict)]
        if not criteria and str(data.get("verdict", "")).upper() == "FAIL":
            criteria = [
                {"text": c, "pass": False, "reason": "não demonstrado"} for c in state.acceptance
            ]
        findings: list[dict[str, str]] = []
        counters: dict[str, int] = {}
        for f in data.get("findings") or []:
            if not isinstance(f, dict) or not str(f.get("text", "")).strip():
                continue
            prefix = str(f.get("prefix", "ARCH")).upper().rstrip("-")
            prefix = prefix if prefix in PREFIXES else "ARCH"
            severity = str(f.get("severity", "low")).lower()
            severity = severity if severity in SEVERITIES else "low"
            counters[prefix] = counters.get(prefix, 0) + 1
            findings.append(
                {
                    "id": f"{prefix}-{counters[prefix]}",
                    "severity": severity,
                    "text": str(f["text"]).strip(),
                }
            )
        return {"criteria": criteria, "findings": findings, "summary": str(data.get("summary", ""))}
