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

import re
import shutil
from pathlib import Path

from loompa.aci import run_command, summarize_lint, summarize_tests, summarize_typecheck
from loompa.agents.base import AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.hygiene import blocking, render, scan_diff, weak_tests
from loompa.speckit import story_dir
from loompa.worktrees import Worktree

JUDGE_SYSTEM = """<!-- role:inspector -->
You are the Inspector Loompa (QA). The automated checks below already ran: their result is a fact.
Never claim that tests fail or pass against it; judge only what a test suite cannot tell.

1. Acceptance criteria. For EACH criterion decide whether the diff plus its tests demonstrably
   satisfy it. Strict and binary. A failed criterion needs a concrete reason: what is missing and
   where (file, function, test). "Not demonstrated" alone is not a reason.

2. Findings: defects the checks cannot catch. Report ONLY what you can anchor in the diff: `file`
   must be a path in the diff and `evidence` a short quote of the added or changed line. Rubric:
   - SEC high: injection (SQL, shell, path traversal) from user input; a secret or credential in
     code; permission or authentication bypass; unsafe deserialization of untrusted data.
   - PERF high: N+1 queries or I/O per item over unbounded data; blocking I/O inside async code;
     unbounded loop or memory driven by user input.
   - TEST high: a tautological test (asserts a constant, asserts what a mock was told to
     return, re-implements the function under test); a criterion whose only test does not run
     the changed code.
   - ARCH high: breaks an explicit rule of the constitution or the plan's contract (a
     dependency the constitution does not allow, code in a layer the plan forbids).
   - medium: a real defect risk to fix soon: an error path of a criterion left unhandled, a
     stated edge case untested, logic duplicated from an existing function.
   - Never report style, naming, formatting, wording of messages, "could be more explicit"
     tests, or anything outside the diff. Those are not findings. At most 3 findings, most
     severe first; an empty list is the normal case for a clean change.
   Changes inside the paths the plan allows are in scope.

Respond with JSON only: {{"criteria": [{{"text": str, "pass": bool, "reason": str}}],
"findings": [{{"prefix": "SEC"|"PERF"|"TEST"|"ARCH", "severity": "high"|"medium", "file": str,
"evidence": str, "text": str}}], "summary": str}}. Write reasons and texts in {language}, one
sentence each, no stack traces.
"""

MAX_FINDINGS = 3
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

    async def baseline(self, state: StoryState, wt: Worktree, *, at: Path | None = None) -> dict:
        """Run the Tier 3 checks on the untouched worktree so pre-existing failures are not
        blamed on the story (brownfield repos are often red on main). `at` runs them on another
        checkout of the base instead (the worktree already holds the story's changes)."""
        q = self.ctx.config.quality
        where = at or wt.path
        base: dict = {"tests_ok": True, "failing": [], "lint_ok": True}
        if q.test_command:
            res = await run_command(q.test_command, where, timeout=900)
            summary = summarize_tests(res.output, res.returncode)
            base["tests_ok"] = summary.ok and not res.timed_out
            base["failing"] = sorted({f.name for f in summary.failures})
        if q.lint_command:
            res = await run_command(q.lint_command, where, timeout=300)
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
        lint_failed = False
        if q.lint_command:
            res = await run_command(q.lint_command, wt.path, timeout=300)
            s = summarize_lint(res.output, res.returncode)
            if s.ok or (state.extra.get("baseline") or {}).get("lint_ok") is False:
                parts.append(
                    s.compact() if s.ok else "[lint] PASS (apontamentos pré-existentes na base)"
                )
            else:
                lint_failed = ok  # the only failure so far: a candidate for `fix_lint`
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
        # 7.10: leftovers no test notices (debris files, machine paths, debugger calls, markers)
        full_diff = (
            self.ctx.worktrees.diff(wt, max_chars=400_000)
            + "\n"
            + self.ctx.worktrees.diff_working(wt, max_chars=200_000)
        )
        issues = scan_diff(full_diff)
        if issues:
            stop = blocking(issues)
            ok = ok and not stop
            parts.append(
                ("[hygiene] FAIL\n" if stop else "[hygiene] notes\n") + render(stop or issues)
            )
            self.ctx.emit(
                "inspector.hygiene",
                story_id=state.story_id,
                agent=self.name,
                blocking=[i.line() for i in stop[:8]],
                warnings=len(issues) - len(stop),
            )
        tooling_ok = ok
        criteria_ok = True
        findings: list[dict[str, str]] = []
        if ok:
            findings = _numbered(weak_tests(full_diff))
        if ok and state.acceptance and not self.ctx.dry_run:
            judge = await self._judge(state, wt, "\n".join(parts), full_diff)
            if judge is not None:
                failed = [c for c in judge.get("criteria", []) if not c.get("pass")]
                criteria_ok = not failed
                findings = _numbered(
                    [*(f for f in findings if f["severity"] == "high"), *judge["findings"]]
                    + [f for f in findings if f["severity"] != "high"]
                )[:MAX_FINDINGS]
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
                + "\n".join(f"- {f['id']} ({f['severity']}): {f['text'][:200]}" for f in findings)
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
        return AgentResult(
            ok=ok,
            summary=report,
            data={
                "verdict": verdict,
                "findings": findings,
                # tests green, no debris: only the linter complained, mechanical fixes may do
                "lint_only": lint_failed and not blocking(issues),
            },
        )

    async def _judge(
        self, state: StoryState, wt: Worktree, checks: str = "", full_diff: str | None = None
    ) -> dict | None:
        diff = self.ctx.worktrees.diff(wt, max_chars=16000)
        if not diff.strip():
            return {
                "verdict": "FAIL",
                "criteria": [
                    {"text": c, "pass": False, "reason": "nenhuma alteração de código foi feita"}
                    for c in state.acceptance
                ],
                "findings": [],
                "summary": "diff vazio",
            }
        paths = story_dir(self.ctx.root, state.story_id)
        spec = paths.spec.read_text(encoding="utf-8")[:3000] if paths.spec.is_file() else ""
        user = (
            "## Acceptance criteria\n"
            + "\n".join(f"- {c}" for c in state.acceptance)
            + f"\n\n## Spec excerpt\n{spec}\n\n"
            + (f"## Automated checks (already run; facts)\n{checks}\n\n" if checks else "")
            # the plan's fence as it stands now (re-planned or amended at the founder's request):
            # without it the judge guessed the scope from the spec and flagged allowed changes
            + (
                "## Paths the plan allows (changes inside them are in scope)\n"
                + "\n".join(f"- {p}" for p in state.allowed_paths)
                + "\n\n"
                if state.allowed_paths
                else ""
            )
            + f"## Diff\n```diff\n{diff}\n```"
        )
        try:
            data = await self.ask_json(
                JUDGE_SYSTEM.format(language=self.language), user, story=state, max_tokens=2500
            )
        except Exception:  # noqa: BLE001 - judge is advisory when the LLM is unavailable
            return None
        criteria = [
            c
            for c in data.get("criteria") or []
            if isinstance(c, dict)
            # a failure with no reason is a claim, not a finding (`contas`: FAIL "sem achados")
            and (c.get("pass") or str(c.get("reason") or "").strip())
        ]
        in_diff = set(re.findall(r"^\+\+\+ b/(.+)$", full_diff or diff, re.M))
        findings: list[dict[str, str]] = []
        dropped = 0
        for f in data.get("findings") or []:
            if not isinstance(f, dict) or not str(f.get("text", "")).strip():
                continue
            prefix = str(f.get("prefix", "ARCH")).upper().rstrip("-")
            severity = str(f.get("severity", "low")).lower()
            text = str(f["text"]).strip()
            file = str(f.get("file") or "").strip().removeprefix("b/")
            file = file or next((path for path in sorted(in_diff) if path in text), "")
            if severity not in ("medium", "high") or (in_diff and file not in in_diff):
                dropped += 1  # a nit, or not anchored in the change: noise, not a card
                continue
            findings.append(
                {
                    "prefix": prefix if prefix in PREFIXES else "ARCH",
                    "severity": severity,
                    "file": file,
                    "text": _with_file(text, file),
                }
            )
        if dropped:
            self.ctx.emit(
                "inspector.findings_dropped", story_id=state.story_id, agent=self.name, n=dropped
            )
        return {"criteria": criteria, "findings": findings, "summary": str(data.get("summary", ""))}


def _with_file(text: str, file: str) -> str:
    """The file goes into the text: Kaizen folds cards that name the same file into one."""
    return text if not file or file in text else f"{file}: {text}"


def _numbered(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    counters: dict[str, int] = {}
    out = []
    for f in findings:
        prefix = str(f.get("prefix") or "TEST")
        counters[prefix] = counters.get(prefix, 0) + 1
        out.append({**f, "prefix": prefix, "id": f"{prefix}-{counters[prefix]}"})
    return out
