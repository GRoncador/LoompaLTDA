"""Builds the candidate prompts of plan 14.3 from today's code, so every change is a reviewable edit.

P0 takes every factory, story id and product example out of the prompts (the founder's rule of
06/10: Loompa serves any web product), keeping each rule and a general reason. Every other
candidate is P0 plus one idea, so it is measured against P0 and not against two changes at once.

    uv run python docs/bench/make_candidates.py      # writes docs/bench/candidates/*.txt
"""

from __future__ import annotations

from pathlib import Path

from loompa.bench import registry

OUT = Path(__file__).parent / "candidates"

TEST_FILE_EXAMPLE = (
    "named after what it tests and so the test runner finds it (`tests/casca-retorno.test.js`),",
    "named after what it tests and so the test runner finds it,",
)
TEST_RISK = (
    "one past it, invalid input) — that is where bugs live (an OFX code read wrongly, \"12.50\" with a\n"
    "   point). Leave out",
    "one past it, invalid input) — that is where bugs live (an external code read with the wrong\n"
    "   meaning, a number written in another locale's format). Leave out",
)
TEST_BLOAT = (
    "   code's data as the expected value. Tamagotchi S-053 wrote 689 test lines for 20 lines of code,\n"
    "   and the tests pinning the pet's pixels broke on the next legitimate change. How the page looks",
    "   code's data as the expected value. Such tests multiply the lines to maintain many times over the\n"
    "   code they cover, and the ones pinning pixels break on the next legitimate change. How the page looks",
)

# P0: (old, new) per prompt. Each `old` must be found, or the script stops.
P0: dict[str, list[tuple[str, str]]] = {
    "architect.SYSTEM": [TEST_FILE_EXAMPLE, TEST_RISK, TEST_BLOAT],
    "architect.SIMPLE_SYSTEM": [TEST_FILE_EXAMPLE, TEST_RISK, TEST_BLOAT],
    "architect.AMEND_SYSTEM": [
        (
            "because of this change. Why: tamagotchi S-061 asked the founder three times, one old test file at a\ntime.",
            "because of this change. Why: asked one file at a time, the founder is interrupted once per\nfile for the same decision.",
        )
    ],
    "architect.PREFLIGHT_SYSTEM": [
        (
            "a task that names its file, so the Worker may change it. Why: `valor` moved from reais to cents\n"
            "  and three commands outside the plan showed amounts 100 times too big while every test passed.",
            "a task that names its file, so the Worker may change it. Why: when a value changes its unit,\n"
            "  the uses the plan did not list keep reading the old one and show wrong numbers while every\n"
            "  test passes.",
        )
    ],
    "deployer.SUMMARY_SYSTEM": [
        (
            'So state appearance as what was changed ("o pet foi movido para o meio da tela"), never as a\n'
            'verified result ("está bem centralizado", "ficou bonito"), and invite the founder to look: the\n'
            "Worker notes say what it meant to do, which the founder may find was not achieved (tamagotchi\n"
            'S-048 was announced "bem centralizado" while it sat at three quarters of the width).',
            "So state appearance as what was changed (an element moved, a colour changed), never as a\n"
            "verified result (well centred, looks good), and invite the founder to look: the Worker notes\n"
            "say what it meant to do, which the founder may find was not achieved.",
        )
    ],
    "inspector.JUDGE_SYSTEM": [
        (
            "   Why: a command that failed for every real user (`converter -40 C`: \"No such option: -4\")\n"
            "   passed review because its test called it as `converter -- -40 C`.\n"
            "   A test runner that invokes the command with exactly the user's arguments (Typer's or Click's\n"
            "   `CliRunner`, a subprocess) IS the user's path.",
            "   Why: a command that failed for every real user with a negative number passed review because\n"
            "   its test added a separator no user types.\n"
            "   A test that drives the product exactly as its user does (the framework's command or HTTP\n"
            "   test client, a subprocess, a browser) IS the user's path.",
        ),
        (
            "   Why: two models spent 128k tokens each recalling Click's parser instead of reading the tests.",
            "   Why: reconstructing a library's behaviour from memory has cost over 100k tokens a call and\n"
            "   still guessed wrong.",
        ),
        (
            "the changed line in the diff and name the stale use in `text`. Why: `valor` moved from reais\n"
            "     to cents, and the summary, the export and the list total, untouched, showed every amount 100\n"
            "     times too big while every test passed.",
            "the changed line in the diff and name the stale use in `text`. Why: when a value changes its\n"
            "     unit, the untouched screens and exports that read it show wrong numbers while every test\n"
            "     passes.",
        ),
    ],
    "master.EXEC_SYSTEM": [
        (
            "cannot back is a false statement to the founder (tamagotchi S-053 was told \"os testes continuam\n"
            'passando" while the product had a merge left half-done in it). When it does not say, leave it out.',
            "cannot back is a false statement to the founder, who decides on it. When it does not say,\n"
            "leave it out.",
        )
    ],
    "product.SYSTEM": [
        (
            "criterion that many small steps give the same result as one step of the same total size. Why: a\n"
            "  pet's needs decayed 100 times too fast in the running page, rounded on every tick, while every\n"
            "  test called the simulation once with a large interval.",
            "criterion that many small steps give the same result as one step of the same total size. Why:\n"
            "  rounding on every tick can make the running product drift far from the tested one while every\n"
            "  test calls it once with a large interval.",
        ),
        (
            "documentation; never invent column names, field order, signs, units or date formats. Why: a\n"
            "  spec guessed a bank's CSV headers in the wrong language and the sign of purchases, and every\n"
            "  test agreed with the guess.",
            "documentation; never invent column names, field order, signs, units or date formats. Why: tests\n"
            "  written from the same guess as the code agree with it, so a wrong guess passes every check.",
        ),
        (
            "that reading is an open question for the founder, never a criterion. Why: a spec read the OFX\n"
            '  type PAYMENT as "credit-card bill payment" and dropped a utility bill the standard calls a\n'
            "  plain electronic payment.",
            "that reading is an open question for the founder, never a criterion. Why: a code's name often\n"
            "  suggests a narrower meaning than the standard gives it, and the wrong reading drops real data.",
        ),
    ],
}

# The other ideas, each applied on top of P0. Sources in the bench report (docs/bench/README.md).
IDEAS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # P1 — Anthropic: "tell Claude what to do instead of what not to do"; "use examples effectively"
    "p1-positive-example": (
        "deployer.SUMMARY_SYSTEM",
        [
            (
                "show. No file names, jargon or code.",
                "show. Write the way a person tells a friend what changed in an app they both use: what the\n"
                "user can now see or do, in everyday words, leaving file names, code and jargon out.",
            ),
            (
                "Respond with JSON only:",
                "When the story changed only tests, comments or internal organisation, say plainly that\n"
                "nothing changes for the user and what became safer.\n"
                "<example>\n"
                'Worker notes: "T1: add GET /orders/export returning CSV; T2: add an Export button to the\n'
                'orders page that calls it"\n'
                'Summary (in English here; yours is in the language asked below): "The orders page now has an\n'
                'Export button that downloads the list as a spreadsheet. Worth trying it on a long list."\n'
                "</example>\n"
                "Respond with JSON only:",
            ),
        ],
    ),
    # P2 — Matt Pocock `tdd` / `to-tickets`: tracer-bullet vertical slices
    "p2-tracer": (
        "architect.SYSTEM",
        [
            (
                "   the old one) so each still leaves the suite green; never one task that rewrites everything.\n",
                "   the old one) so each still leaves the suite green; never one task that rewrites everything.\n"
                "   Order the tasks as a tracer bullet: when the story crosses layers (data, logic, what the\n"
                "   user sees), the first task builds one thin path through all of them for one case of the\n"
                "   main criterion, with its test, and the next tasks widen it case by case. Why: a slice that\n"
                "   already works end to end shows a wrong assumption at the first task, while a layer built\n"
                "   alone is only proven when the last task joins it.\n",
            )
        ],
    ),
    # P3 — Matt Pocock `code-review`, Spec axis (b): behaviour nobody asked for
    "p3-scope": (
        "inspector.JUDGE_SYSTEM",
        [
            (
                "   - medium: a real defect risk to fix soon",
                "   - ARCH medium: behaviour the spec did not ask for (a new option, a changed default, an extra\n"
                "     element on the screen, a rewrite of code no criterion needed): quote the added line and\n"
                "     say which spec line it goes beyond. Why: nobody approved it — the Product Owner reviewed\n"
                "     the spec, not this — and it is what the founder finds changed without having asked.\n"
                "   - medium: a real defect risk to fix soon",
            )
        ],
    ),
    # P4 — INVEST "Testable" and Wolpers' examples-first: two strengths of the same rule
    "p4a-testable-strict": (
        "product_owner.REVIEW_SYSTEM",
        [
            (
                "- Feasibility: it is deliverable as ONE story",
                "- Testability: each acceptance criterion names an observable result a test can check — a\n"
                "  given/when/then, a concrete example with its values, a number. A criterion that rests on a\n"
                '  word with no measure ("fast", "easy", "nice", "works well") is missing its example: quote\n'
                "  it under `missing` with the example it needs. Why: the builder writes the test from the\n"
                "  criterion, and a vague one becomes a test of whatever the code happens to do.\n"
                "- Feasibility: it is deliverable as ONE story",
            )
        ],
    ),
    "p4b-testable-note": (
        "product_owner.REVIEW_SYSTEM",
        [
            (
                "- Feasibility: it is deliverable as ONE story",
                "- Testability: each acceptance criterion names an observable result a test can check — a\n"
                "  given/when/then, a concrete example with its values, a number. A criterion that rests on a\n"
                '  word with no measure ("fast", "easy", "nice", "works well") gets a note with the example\n'
                "  it needs; it is a rejection only when it is the story's main criterion. Why: the builder\n"
                "  writes the test from the criterion, and a vague one becomes a test of whatever the code\n"
                "  happens to do.\n"
                "- Feasibility: it is deliverable as ONE story",
            )
        ],
    ),
    # P5 — Anthropic: "Avoid focusing on passing tests and hardcoding"
    "p5-no-hardcode": (
        "worker.SYSTEM",
        [
            (
                "3. Each write reports syntax errors",
                "   Make the behaviour right for every valid input, not only for the values the tests or the\n"
                "   factory's checks use: never special-case a test's values, and never nudge a number (a\n"
                "   pixel, a timeout) only until a check passes. When a test or a check looks wrong, say so\n"
                "   with `blocked` instead of working around it. Why: a value tuned until a check passes hides\n"
                "   the defect from every later check.\n"
                "3. Each write reports syntax errors",
            )
        ],
    ),
}

# P6 — conflicting instructions (OpenAI GPT-5 prompting guide), one fix per prompt.
P6: dict[str, list[tuple[str, str]]] = {
    "architect.SYSTEM": [
        (
            "   the old one) so each still leaves the suite green; never one task that rewrites everything.\n",
            "   the old one) so each still leaves the suite green; never one task that rewrites everything.\n"
            "   When that takes more than 8 tasks, the story is too big for one plan: say so in `blocker`\n"
            "   with the split you propose.\n",
        )
    ],
    "architect.PREFLIGHT_SYSTEM": [
        (
            "  test for untested code about to change, a backup or a reversible migration step). Each names the\n"
            "  files it writes; nothing already in the plan.",
            "  test for untested code about to change, a backup or a reversible migration step). Each names the\n"
            "  files it writes; nothing already in the plan. One task may cover several uses in the same\n"
            "  module; when the uses to protect still need more than three, say so in `question`.",
        )
    ],
    "product.SYSTEM": [
        (
            "- `needs_decision`: true only for a genuinely blocking product decision (two viable paths with\n"
            "  business impact);",
            "- `needs_decision`: true only for a genuinely blocking product decision (two viable paths with\n"
            "  business impact) or for the missing source of an external format, above;",
        )
    ],
    "product_owner.PROPOSE_SYSTEM": [
        (
            "  when you pick a card whose dependency waits in the backlog, pick the dependency too.",
            "  when you pick a card whose dependency waits in the backlog, pick the dependency too. When that\n"
            "  dependency is not ready to build, leave both out and say why in their notes.",
        )
    ],
    "product_owner.REVIEW_SYSTEM": [
        (
            "  the constitution or an existing spec. Anything else is invented scope.",
            "  the constitution or an existing spec. Anything else is invented scope. A criterion for an\n"
            "  edge case of the story's own input (empty, invalid, extreme) traces to the edge-case rule\n"
            "  below and is not invented.",
        )
    ],
}


def apply(text: str, edits: list[tuple[str, str]], label: str) -> str:
    for old, new in edits:
        if old not in text:
            raise SystemExit(f"{label}: trecho não encontrado:\n{old[:200]}")
        text = text.replace(old, new, 1)
    return text


def p0(key: str) -> str:
    return apply(registry.get(key).template, P0.get(key, []), f"P0 {key}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    for key in P0:
        (OUT / f"{key}.p0-agnostic.txt").write_text(p0(key), encoding="utf-8")
    for name, (key, edits) in IDEAS.items():
        (OUT / f"{key}.{name}.txt").write_text(apply(p0(key), edits, name), encoding="utf-8")
    for key, edits in P6.items():
        (OUT / f"{key}.p6-conflicts.txt").write_text(apply(p0(key), edits, "P6"), encoding="utf-8")
    print("\n".join(sorted(p.name for p in OUT.glob("*.txt"))))


if __name__ == "__main__":
    main()
