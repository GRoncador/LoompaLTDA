"""Builds the candidate prompts of plan 14.3 from today's code, so every change is a reviewable edit.

P0 takes every factory, story id and product example out of the prompts (the founder's rule of
06/10: Loompa serves any web product), keeping each rule and a general reason. Every other idea is
P0 plus that one idea, so it is measured against P0 and not against two changes at once. The
`proposed` file of a prompt is P0 plus every idea marked `proposed`, in order: the text the review
in `PROPOSTA-14.3.md` recommends, still to be measured.

Every candidate is checked before it is written: the JSON contract the code reads is unchanged,
the dry-run marker phrases survive, `{language}` still formats, and no product word is left.

    uv run python docs/bench/make_candidates.py   # candidates/*.txt and PROMPTS-14.3.md
"""

from __future__ import annotations

import ast
import difflib
import importlib
import re
import subprocess
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from loompa import prompt_ab  # P2, P5 and P7 live there, for the live A/B (ADR-0035)
from loompa.bench import registry

HERE = Path(__file__).parent
OUT = HERE / "candidates"
REPORT = HERE / "PROMPTS-14.3.md"
ROOT = HERE.parent.parent

# Phrases the dry-run provider routes on (agents/dryrun.py, CLAUDE.md): a rewrite keeps them.
MARKERS = (
    "Brainstorming session",
    "Classify the story",
    "guardian of the spec",
    "preliminary opinion",
    "Propose the sprint",
    "Propose the stack for this product",
    "rank the backlog",
    "review of the running sprint",
    "Review old backlog cards",
    "Split the direction",
    "Sprint Meeting",
    "Triage the findings",
    "Triage the founder's request",
    "Pre-flight",
)
# Product words that must not survive P0 (the founder's rule of 06/10). A quoted id ("S-004") is the
# format of a reference in a JSON example, not a story.
PRODUCT_WORDS = re.compile(
    r"tamagotchi|\bcontas\b|\bpet\b|\bOFX\b|`valor`|\breais\b|(?<!\")\bS-0\d\d\b|12\.50|10\.99|"
    r"fundadora|casca-retorno|converter -|\bClick\b|\bTyper\b",
    re.I,
)

# --------------------------------------------------------------------------- references

REFS: dict[str, tuple[str, str]] = {
    "anthropic-best": (
        "Anthropic — Prompting best practices (Claude)",
        "https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices",
    ),
    "anthropic-context": (
        "Anthropic — Effective context engineering for AI agents",
        "https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents",
    ),
    "anthropic-tools": (
        "Anthropic — Writing effective tools for agents",
        "https://www.anthropic.com/engineering/writing-tools-for-agents",
    ),
    "anthropic-skill-creator": (
        "Anthropic — skill-creator (anthropics/skills)",
        "https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md",
    ),
    "openai-gpt5": (
        "OpenAI — GPT-5 prompting guide",
        "https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide",
    ),
    "openai-optimizer": (
        "OpenAI — GPT-5 prompt optimization cookbook",
        "https://developers.openai.com/cookbook/examples/gpt-5/prompt-optimization-cookbook",
    ),
    "openai-gpt41": (
        "OpenAI — GPT-4.1 prompting guide",
        "https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide",
    ),
    "openai-codex": (
        "OpenAI — Codex prompting guide",
        "https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide",
    ),
    "google-gemini": (
        "Google — Prompt design strategies (Gemini API)",
        "https://ai.google.dev/gemini-api/docs/prompting-strategies",
    ),
    "google-whitepaper": (
        "Lee Boonstra (Google) — Prompt Engineering whitepaper, 2025 (resumo)",
        "https://justbeingresourceful.com/2025/04/15/mastering-prompt-engineering-key-insights-from-googles-must-read-whitepaper/",
    ),
    "mp-writing": (
        "Matt Pocock — writing-for-agents",
        "https://github.com/mattpocock/skills/tree/main/skills/productivity/writing-for-agents",
    ),
    "mp-tdd": (
        "Matt Pocock — tdd (tests.md, mocking.md)",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd",
    ),
    "mp-review": (
        "Matt Pocock — code-review",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review",
    ),
    "mp-bugs": (
        "Matt Pocock — diagnosing-bugs",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/diagnosing-bugs",
    ),
    "mp-tickets": (
        "Matt Pocock — to-tickets",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/to-tickets",
    ),
    "mp-brief": (
        "Matt Pocock — triage/AGENT-BRIEF",
        "https://github.com/mattpocock/skills/blob/main/skills/engineering/triage/AGENT-BRIEF.md",
    ),
    "mp-retro": (
        "Matt Pocock — retro",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/retro",
    ),
    "mp-grilling": (
        "Matt Pocock — grilling",
        "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling",
    ),
    "mp-domain": (
        "Matt Pocock — domain-modeling",
        "https://github.com/mattpocock/skills/tree/main/skills/engineering/domain-modeling",
    ),
    "sp-debug": (
        "Jesse Vincent (obra/superpowers) — systematic-debugging",
        "https://github.com/obra/superpowers/tree/main/skills/systematic-debugging",
    ),
    "sp-tdd": (
        "Jesse Vincent (obra/superpowers) — test-driven-development",
        "https://github.com/obra/superpowers/tree/main/skills/test-driven-development",
    ),
    "speckit-specify": (
        "GitHub Spec Kit — /specify",
        "https://github.com/github/spec-kit/blob/main/templates/commands/specify.md",
    ),
    "speckit-clarify": (
        "GitHub Spec Kit — /clarify",
        "https://github.com/github/spec-kit/blob/main/templates/commands/clarify.md",
    ),
    "sbe": (
        "Gojko Adzic — Specification by Example (2011)",
        "https://gojko.net/books/specification-by-example/",
    ),
    "example-mapping": (
        "Matt Wynne — Introducing Example Mapping (Cucumber, 2015)",
        "https://cucumber.io/blog/bdd/example-mapping-introduction/",
    ),
    "invest": (
        "Bill Wake — INVEST in Good Stories (2003)",
        "https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/",
    ),
    "pragmatic": (
        "Hunt & Thomas — The Pragmatic Programmer (tracer bullets)",
        "https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/",
    ),
}

# --------------------------------------------------------------------------- P0

TEST_FILE_EXAMPLE = (
    "named after what it tests and so the test runner finds it (`tests/casca-retorno.test.js`),",
    "named after what it tests and so the test runner finds it,",
)
TEST_RISK = (
    'one past it, invalid input) — that is where bugs live (an OFX code read wrongly, "12.50" with a\n'
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
    "architect.SYSTEM": [
        TEST_FILE_EXAMPLE,
        TEST_RISK,
        TEST_BLOAT,
        (
            "and one undid what the founder\nhad approved there (accepting a decimal point, reading old data files).",
            "so one can undo, unnoticed,\nbehaviour the founder approved there.",
        ),
    ],
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
            'right. So state appearance as what was changed ("o pet foi movido para o meio da tela"), never as a\n'
            'verified result ("está bem centralizado", "ficou bonito"), and invite the founder to look: the\n'
            "Worker notes say what it meant to do, which the founder may find was not achieved (tamagotchi\n"
            'S-048 was announced "bem centralizado" while it sat at three quarters of the width).',
            "right.\nSo state appearance as what was changed (an element moved, a colour changed), never as a\n"
            "verified result (well centred, looks good), and invite the founder to look: the Worker notes\n"
            "say what it meant to do, which the founder may find was not achieved.",
        )
    ],
    "inspector.JUDGE_SYSTEM": [
        (
            '   Why: a command that failed for every real user (`converter -40 C`: "No such option: -4")\n'
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
            'cannot back is a false statement to the founder (tamagotchi S-053 was told "os testes continuam\n'
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
            "  test agreed with the guess. The meaning",
            "documentation; never invent column names, field order, signs, units or date formats. Why: tests\n"
            "  written from the same guess as the code agree with it, so a wrong guess passes every check.\n"
            "  The meaning",
        ),
        (
            "that reading is an open question for the founder, never a criterion. Why: a spec read the OFX\n"
            '  type PAYMENT as "credit-card bill payment" and dropped a utility bill the standard calls a\n'
            "  plain electronic payment.",
            "that reading is an open question for the founder, never a criterion. Why: a code's name often\n"
            "  suggests a narrower meaning than the standard gives it, and the wrong reading drops real data.",
        ),
    ],
    "product_owner.ALIGN_SYSTEM": [
        (
            '(a\ncriterion "rejects 10.99" after the founder said "accept 12.50 with a point")',
            "(a\ncriterion that still rejects an input the founder has just asked to accept)",
        )
    ],
    "product_owner.REVIEW_SYSTEM": [
        (
            "Why: a spec dropped the decimal point and\n"
            '  the reading of old data files that the founder had just approved, as "assumptions".',
            "Why: listed as an assumption, the removal\n"
            "  reaches the builder as a default, and the founder loses what they approved without being asked.",
        )
    ],
    "product_owner.REVIEW_CARDS_SYSTEM": [
        (
            'Address the founder as "você" and never guess their gender (a live reply said "a fundadora").',
            'Address the founder as "you" (in Portuguese "você", never "o senhor" or "a senhora"), without\n'
            "assuming their gender: a guessed gender is wrong for some founders.",
        )
    ],
}

# --------------------------------------------------------------------------- ideas


@dataclass
class Idea:
    id: str
    title: str  # pt-BR, for the review document
    refs: list[str]
    why: str  # pt-BR: what the references say and why it should be better here
    evidence: str  # pt-BR: what our own data shows, or "" when there is none yet
    edits: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    proposed: bool = True  # part of the recommended text, or kept only as an alternative to measure
    note: str = ""  # pt-BR: condition or caveat
    detail: dict[str, str] = field(default_factory=dict)  # pt-BR, per prompt: what changes there
    data: dict[str, str] = field(default_factory=dict)  # pt-BR, per prompt: evidence from our runs

    def evidence_for(self, key: str) -> str:
        return self.data.get(key, "") if self.data else self.evidence


RECOMMENDED = "the one you recommend first: the founder sees it marked as recommended"

IDEAS: list[Idea] = [
    Idea(
        "p1-founder-voice",
        "Texto ao Founder: dizer o que fazer, na voz certa",
        ["anthropic-best", "google-whitepaper", "mp-writing"],
        'Anthropic: "diga o que fazer em vez do que não fazer" e "o estilo do prompt contamina o da '
        'resposta". Google: instruções em vez de restrições. Matt Pocock (Negation): proibir traz a '
        "palavra proibida para o contexto; a proibição fica só como trava, ao lado do alvo positivo. "
        "Aqui o alvo positivo é nomear as coisas como o Founder as conhece e falar com ele na segunda "
        "pessoa; a lista do que a auditoria recusa continua, agora completa (números de tarefa, "
        "palavras de git).",
        "",
        detail={
            "deployer.SUMMARY_SYSTEM": 'Troca a proibição ("No file names, jargon or code") pelo alvo '
            "positivo, que ainda diz o que fica de fora. É o P1 antigo sem o exemplo e sem a regra nova "
            "sobre histórias só de testes: três mudanças juntas não dizem qual delas pesou.",
            "master.EXEC_SYSTEM": "Diz como nomear (pelo que a coisa é no produto) e completa a lista do "
            "que a auditoria recusa com o que hoje reprova e o prompt não nomeia: número de tarefa e "
            "palavra de git. E fala com o Founder sem supor o gênero.",
            "master.SPRINT_REPORT_SYSTEM": 'O prompt dizia "waiting for the founder" e o resumo repetia '
            '"espera pelo fundador" (o estilo do prompt passa para a resposta). Agora o texto se dirige '
            "ao Founder.",
        },
        data={
            "deployer.SUMMARY_SYSTEM": "O P1 antigo (com um exemplo) perdeu do P0 por 13×15, nota 3,79 "
            "contra 4,0, e custou 60% a mais.",
            "master.EXEC_SYSTEM": "Bancada de 06/10: 10 de 36 respostas do prompt atual reprovam na "
            'auditoria, 6 por número de tarefa ("T1") e 4 por jargão que o prompt não nomeia '
            '("checklist"); 5 de 36 falam do Founder na terceira pessoa ou supõem o gênero ("o senhor").',
            "master.SPRINT_REPORT_SYSTEM": "Em 8 a 10 de 11 resumos o Founder aparece na terceira pessoa "
            '("houve 24 minutos de espera pelo fundador").',
        },
        edits={
            "deployer.SUMMARY_SYSTEM": [
                (
                    "show. No file names, jargon or code.",
                    "show. Write the way a person tells a friend what changed in an app they both use: what the\n"
                    "user can now see or do, in everyday words, leaving file names, code and jargon out.",
                )
            ],
            "master.EXEC_SYSTEM": [
                (
                    "No file names, code, error names or stack traces: the message is audited and rejected\n"
                    "when it has any.",
                    "Call each thing by what it is for the product and its users (a screen, a button,\n"
                    "the tests of another feature) and the team's work in everyday words (the main version of the\n"
                    "product, a saved step, the list of steps). The team's own labels stay out (task numbers such\n"
                    "as T1, git and tool words, file names, code, error names, stack traces): the message is\n"
                    'audited and rejected when it has any. Address the founder as "you", without assuming their\n'
                    "gender.",
                )
            ],
            "master.SPRINT_REPORT_SYSTEM": [
                (
                    "for the founder, who is not\ntechnical. In at most five short sentences of plain {language}: "
                    "what the sprint delivered against\nits goal, what took longest or cost most and why (rework, "
                    "blocks, waiting for the founder), what",
                    "for the founder, who is not\n"
                    'technical and reads it as addressed to them ("you", without assuming their gender). In at\n'
                    "most five short sentences of plain {language}: what the sprint delivered against its goal,\n"
                    "what took longest or cost most and why (rework, blocks, time waiting for your answers), what",
                ),
            ],
        },
    ),
    Idea(
        "p2-tracer",
        "Plano do Architect: tracer bullet",
        ["mp-tdd", "mp-tickets", "pragmatic"],
        "Matt Pocock (tdd, to-tickets) e The Pragmatic Programmer: a primeira fatia atravessa todas as "
        "camadas num caso só, e as seguintes alargam; uma suposição errada aparece na primeira tarefa. "
        "O to-tickets abre uma exceção que o P2 antigo não tinha: a refatoração larga (um nome ou unidade "
        "em muitos módulos) segue expand–contract, que é a regra que o prompt já tem.",
        "O resultado antigo do P2 não vale (o juiz lia planos cortados). O efeito real é no retrabalho "
        "do Worker, que a bancada não mede: o juiz só opina sobre o plano.",
        {"architect.SYSTEM": [prompt_ab.TRACER]},
    ),
    Idea(
        "p3-scope",
        "Inspector: comportamento que ninguém pediu (eixo Spec do code-review)",
        ["mp-review"],
        "O code-review do Matt Pocock separa o eixo Spec em três: o que falta, o que foi feito errado e "
        "o que ninguém pediu (scope creep). O Inspector já cobre os dois primeiros pelos critérios.",
        "Não medido (a rodada parou em 10 de 40).",
        {
            "inspector.JUDGE_SYSTEM": [
                (
                    "   - medium: a real defect risk to fix soon",
                    "   - ARCH medium: behaviour the user can see or use that neither the spec nor the founder's\n"
                    "     guidance asked for (scope creep: a new option, a changed default, an extra element on the\n"
                    "     screen): quote the added line and name the spec section it goes beyond. Why: nobody\n"
                    "     approved it, and the founder finds it changed without having asked.\n"
                    "   - medium: a real defect risk to fix soon",
                )
            ]
        },
        proposed=False,
        note="Só depois de o Inspector ler a spec inteira: hoje ele recebe os primeiros 3.000 caracteres "
        '(inspector.py:380) e a seção "fora do escopo" costuma ficar de fora, o que geraria achados '
        'falsos. E a frase "Changes inside the paths the plan allows are in scope" nasceu para '
        "tirar falsos alarmes de escopo; os dois precisam ser medidos juntos.",
    ),
    Idea(
        "p4-examples",
        "Spec: critério com um exemplo concreto",
        ["sbe", "example-mapping", "speckit-specify", "mp-brief", "invest"],
        "Specification by Example (Adzic) e Example Mapping (Wynne): cada regra ilustrada por um "
        'exemplo concreto, um comportamento por cenário. Spec Kit: "pense como um testador: todo '
        'requisito vago reprova". Matt Pocock (AGENT-BRIEF): critérios concretos, cada um verificável '
        "sozinho. INVEST: o T de Testable. Pôr a regra em quem escreve evita a rodada de revisão que "
        "o P4a criaria ao reprovar a spec.",
        "",
        {
            "product.SYSTEM": [
                (
                    "- `acceptance`: BDD sentences (Given ..., when ..., then ...) written in {language}, each testable\n"
                    "  and binary.",
                    "- `acceptance`: BDD sentences (Given ..., when ..., then ...) written in {language}, each testable\n"
                    "  and binary: one behaviour per sentence, with the concrete values of an example wherever the\n"
                    "  behaviour has values (this input gives that result, a limit as a number). Why: the tests are\n"
                    "  written from the criteria, and a criterion without its example becomes a test of whatever the\n"
                    "  code happens to do.",
                )
            ]
        },
    ),
    Idea(
        "p4b-testable-note",
        "Revisão da spec: critério sem exemplo vira nota (alternativa ao P4)",
        ["invest", "speckit-specify"],
        "A mesma ideia do P4 do lado de quem revisa. Fica só como alternativa: medir se o P4 sozinho "
        "não bastar. O P4a antigo (reprovar a spec) sai: cada reprovação custa uma rodada inteira.",
        "",
        {
            "product_owner.REVIEW_SYSTEM": [
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
            ]
        },
        proposed=False,
    ),
    Idea(
        "p5-general-solution",
        "Worker: a lógica geral, não o valor que passa na checagem",
        ["anthropic-best"],
        'Anthropic, "Avoid focusing on passing tests and hard-coding": "os testes verificam a '
        'correção, não definem a solução"; quando um teste parece errado, avisar em vez de contornar. '
        "Reescrito com o alvo positivo na frente (o P5 antigo era só proibição).",
        "O Worker moveu um sprite 1 px até passar no smoke (Fase 15), e a S-065 precisou de "
        "autorização para mexer em testes que fixavam pixels. A bancada mede só a primeira chamada do "
        "Worker; o efeito aparece no fim do laço, ao vivo.",
        {"worker.SYSTEM": [prompt_ab.GENERAL_SOLUTION]},
    ),
    Idea(
        "p6-conflicts",
        "Regras que se contradizem",
        ["openai-gpt5", "openai-optimizer", "mp-tickets"],
        "OpenAI (guia do GPT-5): instruções contraditórias custam raciocínio a modelos que pensam, que "
        "gastam tokens tentando conciliá-las em vez de escolher uma; o otimizador de prompts da OpenAI "
        "começa por elas. Os pares vêm de `scripts/prompt_contradictions.py` e da leitura dos prompts.",
        "",
        detail={
            "architect.SYSTEM": "Par: 2-8 tarefas × uma tarefa por módulo numa mudança que cruza muitos "
            "módulos. O P6 antigo devolvia a história como grande demais (`blocker`), o que contradiz o "
            "CLASSIFY (a mudança que atravessa as camadas é UMA história) e a própria definição de "
            "`blocker`. A saída vem do to-tickets: os módulos vão em lotes (por pacote, por diretório), um "
            'lote por tarefa. "expand–contract" é uma palavra-guia (writing-for-agents): o modelo já '
            "conhece o padrão pelo nome.",
            "architect.PREFLIGHT_SYSTEM": "Par: no máximo 3 tarefas extras × uma tarefa para cada uso de "
            "fora do plano que a mudança quebraria. O P6 antigo mandava perguntar em `question`, que o "
            "prompt reserva para o irreversível (uma contradição nova). Aqui uma tarefa protege vários "
            "usos, agrupados por módulo.",
            "product_owner.PROPOSE_SYSTEM": "Par: puxar a dependência junto × deixar de fora o que não "
            "está pronto. Igual ao P6 antigo: quando a dependência não está pronta, as duas ficam de fora, "
            "com o motivo.",
            "product_owner.REVIEW_SYSTEM": 'Par (marcado "high"): todo critério rastreia ao pedido × '
            "história com dinheiro ou dados pessoais precisa de critérios de borda que ninguém pediu. Igual "
            "ao P6 antigo.",
            "master.EXEC_SYSTEM": 'Par: o `impact` pede "o que segue normal" × não tranquilizar sem base '
            "no material. O `impact` passa a dizer o que segue normal só quando o material diz.",
            "analyst.SYSTEM": 'Ambiguidade: "queries genéricas, sem nomes internos" vale para a web, mas '
            "se lia como valendo também para a busca no repositório, que precisa dos nomes internos.",
            "worker.SYSTEM": 'Ambiguidade: "edição de arquivo não lido é recusada" × criar o arquivo de '
            "teste novo que a tarefa pede. O código (aci/tools.py:247) só exige ler o arquivo que já "
            "existe; o prompt passa a dizer o mesmo.",
        },
        data={
            "master.EXEC_SYSTEM": '19 de 36 respostas do prompt atual dizem que o resto "continua '
            'funcionando normalmente"; no P0, que tirou o caso concreto, foram 26 de 36.',
        },
        edits={
            "architect.SYSTEM": [
                (
                    "kept compatible in between (accept the old and the new form until the last task drops\n"
                    "   the old one) so each still leaves the suite green; never one task that rewrites everything.\n",
                    "kept compatible in between (expand–contract: accept the old and the new form until the\n"
                    "   last task drops the old one) so each still leaves the suite green; never one task that\n"
                    "   rewrites everything. When the modules outnumber the tasks, each task migrates one batch of\n"
                    "   them (a package, a directory), so the plan still fits in 8 tasks.\n",
                ),
            ],
            "architect.PREFLIGHT_SYSTEM": [
                (
                    "files it writes; nothing already in the plan.",
                    "files it writes; nothing already in the plan. One task may protect several uses: group them\n"
                    "  by module so they fit in the three.",
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
            "master.EXEC_SYSTEM": [
                (
                    "- impact: what it means for the product and what keeps going normally;",
                    "- impact: what it means for the product, and what keeps going normally only when the material\n"
                    "  says so;",
                )
            ],
            "analyst.SYSTEM": [
                ("- Keep search queries generic", "- Keep web search queries generic")
            ],
            "worker.SYSTEM": [
                (
                    "an edit to a\n   file you have not read in this task is refused. Locate names with "
                    "`find_symbol`/`search` and the\n   repository outline",
                    "an edit to an\n   existing file you have not read in this task is refused. Locate names with\n"
                    "   `find_symbol`/`search` and the repository outline",
                )
            ],
        },
    ),
    Idea(
        "p7-test-seams",
        "Worker: testar pela interface, com valor esperado independente",
        ["mp-tdd", "sp-tdd"],
        "Matt Pocock (tdd): teste pela interface pública, nunca por dentro; o valor esperado vem de uma "
        "fonte independente (um literal, um exemplo feito à mão, a spec), nunca recalculado como o "
        "código calcula (o teste tautológico). mocking.md: mock só na fronteira do sistema (rede, "
        "relógio, aleatoriedade). Superpowers diz o mesmo. O Inspector já reprova o teste tautológico "
        "(TEST high); dizer isso a quem escreve o teste evita a rodada de retrabalho.",
        "",
        {"worker.SYSTEM": [prompt_ab.TEST_SEAMS]},
    ),
    Idea(
        "p8-ask-round",
        "Brainstorm: perguntas numa rodada, cada uma com a resposta recomendada",
        ["mp-grilling", "speckit-clarify"],
        "Matt Pocock (grilling): perguntar de uma vez tudo o que já dá para perguntar, numerado, com a "
        "resposta recomendada; uma pergunta que depende de outra em aberto espera a rodada seguinte; "
        "fato não é o usuário quem busca. Spec Kit (/clarify): a opção recomendada em destaque, e o "
        'usuário pode só dizer "sim". Para um Founder não técnico, concordar custa menos que formular.',
        "",
        {
            "brainstorm.BRAINSTORM_SYSTEM": [
                (
                    "- When unsure what the founder means, ask one short question instead of guessing.",
                    "- When unsure what the founder means, ask instead of guessing: the questions you need now in one\n"
                    "  message, numbered, each with the answer you recommend, so the founder can simply agree. A\n"
                    "  question that depends on another open one waits for the next round, and a fact is never the\n"
                    "  founder's to find: consult the role that knows it.",
                )
            ]
        },
    ),
    Idea(
        "p9-recommended-first",
        "Opções ao Founder: a recomendada primeiro",
        ["speckit-clarify", "mp-grilling"],
        "Spec Kit (/clarify) e Matt Pocock (grilling): toda pergunta vem com a resposta recomendada. "
        "Aqui é também acertar o prompt com o código: `master.blocked_message` marca a PRIMEIRA opção "
        "como recomendada (master.py:945), e nenhum prompt diz isso a quem escreve as opções. Hoje o "
        'selo "recomendada" cai na opção que o modelo listou primeiro por acaso.',
        "",
        {
            "analyst.SYSTEM": [
                (
                    "2-3 short `options` in plain, non-technical {language}.",
                    f"2-3 short `options` in plain, non-technical {{language}},\n  {RECOMMENDED}.",
                )
            ],
            "architect.PREFLIGHT_SYSTEM": [
                (
                    "Only then do `question` and 2-3 `options` ask the founder, in plain non-technical {language}.",
                    "Only then do `question` and 2-3 `options` ask the founder, in plain non-technical {language},\n"
                    f"  {RECOMMENDED}.",
                )
            ],
        },
    ),
    Idea(
        "p10-when-to-ask",
        "Spec: quando perguntar ao Founder (Spec Kit), num lugar só",
        ["speckit-specify", "speckit-clarify", "mp-writing", "openai-gpt5"],
        "Spec Kit (/specify): marcar dúvida só quando muda o escopo ou o que o usuário vê, há leituras "
        "com resultados diferentes e não existe um padrão razoável; no resto, escolher o padrão e "
        "registrá-lo nas premissas. Matt Pocock (co-location): as regras de um conceito juntas; hoje os "
        "gatilhos de `needs_decision` estão em dois tópicos e se contradizem (formato externo exige "
        "perguntar; o tópico de `needs_decision` só permite com dois caminhos de negócio). Inclui a "
        "recomendada primeiro (P9).",
        'A auditoria de contradições marcou este par como "high".',
        {
            "product.SYSTEM": [
                (
                    "choice in `assumptions`; it is technical, not the founder's decision.",
                    "choice in `assumptions`; it is technical, not the founder's decision, unless it is a format\n"
                    "  owned by someone else (next rule).",
                ),
                (
                    "- `needs_decision`: true only for a genuinely blocking product decision (two viable paths with\n"
                    "  business impact); then `question`, `context` and 2-3 short `options` in plain, non-technical\n"
                    "  {language} for the founder. Otherwise false, and open points go to `questions` as information.",
                    "- `needs_decision`: true in two cases only: a product decision with no reasonable default, where\n"
                    "  the request supports readings that would build different things for the user; or a format\n"
                    "  owned by someone else with no source (above). Then `question`, `context` and 2-3 short\n"
                    "  `options` in plain, non-technical {language} for the founder, the one you recommend first (the\n"
                    "  founder sees it marked as recommended) and why in `context`. Otherwise false: choose the\n"
                    "  reasonable default, list it under `assumptions`, and put open points in `questions` as\n"
                    "  information. Why: every question stops the story until the founder answers.",
                ),
            ]
        },
    ),
    # Round 3 (06/10): alternatives where the first proposal was not tuned
    Idea(
        "p1c-team-voice",
        "Relatório do sprint: o time relata, o Founder aparece pelo que fez",
        ["anthropic-best", "mp-writing"],
        "O P1 dirigia o relatório ao Founder e o modelo passou a pôr o Founder como sujeito "
        '("Você entregou as 2 histórias", "Você esperou 3d 23h por nossas respostas"). Aqui o time '
        "relata na primeira pessoa e o Founder aparece só pelo que fez (os pedidos, as respostas), na "
        'segunda pessoa e sem supor o gênero; e "rework" ganha a palavra em português, que o juiz '
        "cobrava.",
        'Rodada 3: P1b (a mesma voz) subiu a nota de 3,73 para 4,36 e o "fundador" na 3ª pessoa de '
        "9/11 para 1/11. Com o rótulo dos dados corrigido no código (d1efdfd), o P1c zera a 3ª pessoa "
        "e não inverte quem esperou quem; o juiz empata (5×4, n=11).",
        {
            "master.SPRINT_REPORT_SYSTEM": [
                (
                    "for the founder, who is not\ntechnical. In at most five short sentences of plain {language}: "
                    "what the sprint delivered against\nits goal, what took longest or cost most and why (rework, "
                    "blocks, waiting for the founder), what",
                    "for the founder, who is not\ntechnical. Write it as the team reporting to them: the team "
                    'delivered, spent and got blocked\n("we delivered"); the founder appears only for what they '
                    "did, in the second person and without\nassuming their gender (the changes you asked for, the "
                    "time the stories waited for your answers).\nIn at most five short sentences of plain "
                    "{language}: what the sprint delivered against its goal,\nwhat took longest or cost most and "
                    "why (rework, blocks, time waiting for the founder's answers), what",
                ),
                (
                    '(in Portuguese a\nstory is a "história", never "story").',
                    '(in Portuguese a\nstory is a "história", never "story", and rework is "retrabalho").',
                ),
            ]
        },
        proposed=False,
    ),
    Idea(
        "p10b-minimal",
        "Spec: só a contradição do formato externo, e a recomendada primeiro",
        ["openai-gpt5", "speckit-clarify"],
        "O P10 mexia em três coisas de uma vez (onde perguntar, o limiar do Spec Kit e a recomendada "
        "primeiro) e perdeu para o P0 (14×23). Aqui fica o mínimo: o formato de terceiros sem fonte é "
        'motivo para perguntar (a contradição "high"), e a opção recomendada vem primeiro, como o '
        "inbox já mostra.",
        "",
        {
            "product.SYSTEM": [
                (
                    "choice in `assumptions`; it is technical, not the founder's decision.",
                    "choice in `assumptions`; it is technical, not the founder's decision, unless it is a format\n"
                    "  owned by someone else (next rule).",
                ),
                (
                    "- `needs_decision`: true only for a genuinely blocking product decision (two viable paths with\n"
                    "  business impact); then `question`, `context` and 2-3 short `options` in plain, non-technical\n"
                    "  {language} for the founder.",
                    "- `needs_decision`: true only for a genuinely blocking product decision (two viable paths with\n"
                    "  business impact) or for the missing source of a format owned by someone else (above); then\n"
                    "  `question`, `context` and 2-3 short `options` in plain, non-technical {language} for the\n"
                    "  founder, the one you recommend first: the founder sees it marked as recommended.",
                ),
            ]
        },
        proposed=False,
    ),
]

# What the bench approved (rounds 3 and 3b, PROPOSTA-14.3.md "Resultado"): the ideas each prompt's
# final text carries, on top of P0 when the prompt has P0 edits. A prompt not listed stays as it is.
FINAL: dict[str, list[str]] = {
    "analyst.SYSTEM": ["p6-conflicts", "p9-recommended-first"],
    "architect.AMEND_SYSTEM": [],
    "architect.PREFLIGHT_SYSTEM": ["p6-conflicts", "p9-recommended-first"],
    "architect.SIMPLE_SYSTEM": [],
    "architect.SYSTEM": ["p6-conflicts"],
    "deployer.SUMMARY_SYSTEM": [],
    "inspector.JUDGE_SYSTEM": [],
    "master.EXEC_SYSTEM": ["p1-founder-voice", "p6-conflicts"],
    "master.SPRINT_REPORT_SYSTEM": ["p1c-team-voice"],
    "product.SYSTEM": ["p4-examples", "p10b-minimal"],
    "product_owner.ALIGN_SYSTEM": [],
    "product_owner.PROPOSE_SYSTEM": ["p6-conflicts"],
    "product_owner.REVIEW_CARDS_SYSTEM": [],
    "product_owner.REVIEW_SYSTEM": ["p6-conflicts"],
    "worker.SYSTEM": ["p6-conflicts"],
}

# What the bench said, idea by idea (pt-BR, for the review documents). The juiz split is
# current×candidate unless said otherwise; p is the sign test (judge) or McNemar (checks).
APROVADA, NEUTRA, REPROVADA, FORA = "aprovada", "neutra, adotada", "reprovada", "não medível"
RESULTS: dict[tuple[str, str], tuple[str, str]] = {
    ("p0-agnostic", "master.EXEC_SYSTEM"): (NEUTRA, "juiz 14×9 (p 0,40); auditoria 72→75%."),
    ("p1-founder-voice", "master.EXEC_SYSTEM"): (
        APROVADA,
        "auditoria 75→97% (p 0,01); gênero/3ª pessoa 5→0; número de tarefa 5→0; juiz 11×13. "
        "Confirmada no texto final (rodada 4): juiz 4×16 (p 0,01).",
    ),
    ("p6-conflicts", "master.EXEC_SYSTEM"): (
        APROVADA,
        "juiz 5×21 (p < 0,01); tranquilizações 20→11. Confirmada: tranquilizações 18→9.",
    ),
    ("p1-founder-voice", "deployer.SUMMARY_SYSTEM"): (
        REPROVADA,
        "sem ganho: juiz 21×20, auditoria 98→97%. O original (com o P0) fica.",
    ),
    ("p0-agnostic", "deployer.SUMMARY_SYSTEM"): (
        NEUTRA,
        "quatro rodadas somam 66×79; a auditoria já passa 93-100% nos dois.",
    ),
    ("p1-founder-voice", "master.SPRINT_REPORT_SYSTEM"): (
        REPROVADA,
        'tirou o "fundador" (7→0), mas pôs o Founder como sujeito ("Você entregou", "Você '
        'esperou por nossas respostas"); nota 3,86→3,59. Trocada pelo P1c.',
    ),
    ("p1c-team-voice", "master.SPRINT_REPORT_SYSTEM"): (
        APROVADA,
        "P1b: nota 3,73→4,36, 3ª pessoa 9→1. P1c com o rótulo corrigido (d1efdfd): 3ª pessoa "
        "3→0, nenhuma inversão; texto final: juiz 2×4, 3ª pessoa 8→1 (n=11).",
    ),
    ("p0-agnostic", "inspector.JUDGE_SYSTEM"): (
        NEUTRA,
        "duas rodadas iguais deram 5×15 (p 0,04) e 12×9: somadas 17×24.",
    ),
    ("p3-scope", "inspector.JUDGE_SYSTEM"): (
        REPROVADA,
        "com a spec inteira: juiz 8×14 (p 0,29), achados 22→26, decisão igual à gravada "
        "89,7→86,2%. Sem ganho claro; o original fica.",
    ),
    ("p6-conflicts", "architect.PREFLIGHT_SYSTEM"): (
        APROVADA,
        "juiz 1×8 (p 0,04); confirmada no texto final: 3×7. Somadas 4×15 (p ≈ 0,02).",
    ),
    ("p9-recommended-first", "architect.PREFLIGHT_SYSTEM"): (
        NEUTRA,
        "juiz 6×4 (p 0,75); nenhum caso irreversível, então o texto novo não entrou em jogo.",
    ),
    ("p0-agnostic", "architect.PREFLIGHT_SYSTEM"): (NEUTRA, "medido junto com P6 e P9."),
    ("p6-conflicts", "product_owner.PROPOSE_SYSTEM"): (
        APROVADA,
        "juiz 2×5 e, no texto final, 2×4; nota 3,55→3,86. Pequeno (n=11), mesmo lado duas vezes.",
    ),
    ("p6-conflicts", "product_owner.REVIEW_SYSTEM"): (
        NEUTRA,
        "juiz 21×14 e, no final, 19×17; aprovações 28→34 (critérios de borda deixam de ser "
        "escopo inventado).",
    ),
    ("p0-agnostic", "product_owner.REVIEW_SYSTEM"): (NEUTRA, "juiz 18×18."),
    ("p4b-testable-note", "product_owner.REVIEW_SYSTEM"): (
        REPROVADA,
        "juiz 17×18, aprovações 32→31: nada muda. O P4 (na escrita) cobre a ideia.",
    ),
    ("p0-agnostic", "product_owner.ALIGN_SYSTEM"): (NEUTRA, "juiz 5×6; no final, 3×8."),
    ("p0-agnostic", "architect.AMEND_SYSTEM"): (NEUTRA, "juiz 14×11 duas vezes (p 0,69)."),
    ("p0-agnostic", "architect.SIMPLE_SYSTEM"): (NEUTRA, "juiz 3×1 com 4 casos."),
    ("p0-agnostic", "product_owner.REVIEW_CARDS_SYSTEM"): (
        NEUTRA,
        'a primeira versão ("second person") trouxe "o senhor" de volta; com "você" como '
        "exemplo do português: juiz 0×1 (3 casos).",
    ),
    ("p6-conflicts", "analyst.SYSTEM"): (NEUTRA, "medido junto com o P9."),
    ("p9-recommended-first", "analyst.SYSTEM"): (
        NEUTRA,
        "duas rodadas: 5×2 e 1×5 (somadas 6×7); texto final 3×3. Nenhum caso pediu decisão.",
    ),
    ("p6-conflicts", "worker.SYSTEM"): (
        NEUTRA,
        "uma palavra que repete o que o código faz; a primeira chamada do Worker não muda.",
    ),
    ("p5-general-solution", "worker.SYSTEM"): (
        FORA,
        "a primeira chamada fica igual (read_file 53→52) e o Worker não bloqueia mais cedo; o "
        "efeito está no fim do laço, que a bancada não vê. Em A/B ao vivo na tamagotchi (ADR-0035).",
    ),
    ("p7-test-seams", "worker.SYSTEM"): (
        FORA,
        "primeira chamada igual (read_file 53→49); o efeito está nos testes escritos depois. "
        "Em A/B ao vivo na tamagotchi (ADR-0035).",
    ),
    ("p8-ask-round", "brainstorm.BRAINSTORM_SYSTEM"): (
        FORA,
        "5 casos, primeira chamada, sem juiz. Lidas à mão, as respostas assumem e recomendam "
        "mais, mas 5 não validam.",
    ),
    ("p0-agnostic", "product.SYSTEM"): (
        NEUTRA,
        "juiz 19×13 (p 0,38). Num caso da `contas`, sem o exemplo do OFX o modelo inventou o "
        "formato: o exemplo concreto ajuda na fábrica de onde veio, e é por isso que sai.",
    ),
    ("p4-examples", "product.SYSTEM"): (
        APROVADA,
        "critérios com valor concreto 58→83% (382/660 → 649/786); juiz 12×23 (p 0,09). Gera "
        "~19% mais critérios. Texto final (P0+P4+P10b) confirmado: juiz 17×28 (p 0,14), contrato "
        "90→95%, valores 60→83%; pergunta mais ao Founder (11→17 de 60) e sai ~43% mais longa.",
    ),
    ("p10-when-to-ask", "product.SYSTEM"): (
        REPROVADA,
        "juiz 23×14 para o P0, contrato 97→90%; perguntas ao Founder 16→12. Mudava três coisas; "
        "trocada pelo P10b.",
    ),
    ("p10b-minimal", "product.SYSTEM"): (
        NEUTRA,
        "juiz 21×16 para o P0 (p 0,51), contrato 87→90%. Tira a contradição sem custo.",
    ),
    ("p0-agnostic", "architect.SYSTEM"): (NEUTRA, "juiz 12×15 (p 0,70)."),
    ("p2-tracer", "architect.SYSTEM"): (
        FORA,
        "o juiz empata o plano (14×17, p 0,72; nota 3,93→4,18) e há ~10% mais tarefas por plano; "
        "o efeito que importa (retrabalho) só aparece ao vivo. Em A/B ao vivo na tamagotchi (ADR-0035).",
    ),
    ("p6-conflicts", "architect.SYSTEM"): (
        NEUTRA,
        "juiz 16×23 (p 0,34); tarefas por plano 4,0→3,8. Texto final (P0+P6) confirmado: 13×20.",
    ),
}

# --------------------------------------------------------------------------- outside the bench

OUTSIDE_RESULTS: dict[str, tuple[str, str]] = {
    "o1-reproducer-symptom": (
        FORA,
        "a bancada só repete chamadas gravadas; fica para um A/B ao vivo.",
    ),
    "o1b-reproducer-plan": (FORA, "idem."),
    "o2-diagnose-retry": (
        FORA,
        "só aparece nas novas tentativas, ao vivo; pede a checagem de `[debug]`.",
    ),
    "o3-blocked-tool": (
        NEUTRA,
        "o mesmo alinhamento com o inbox que o P9 (neutro na bancada); adotada em 8f7f683.",
    ),
    "o4-ops-check": (FORA, "o EXPLAIN_SYSTEM fica fora do registro da bancada."),
    "o5-glossary": (FORA, "texto montado no código, fora dos casos gravados."),
}


@dataclass
class Outside:
    id: str
    title: str
    where: str  # "module:ATTR" whose runtime text holds `old`, or a repository file path
    refs: list[str]
    why: str
    old: str
    new: str
    note: str = ""


OUTSIDE: list[Outside] = [
    Outside(
        "o1-reproducer-symptom",
        "Reprodutor de bug: afirma o sintoma exato (Worker)",
        "loompa.agents.worker:REPRODUCER_TASK",
        ["mp-bugs", "sp-debug"],
        'diagnosing-bugs: a fase 1 só termina com um teste "red-capable", que afirma o sintoma exato '
        'que o usuário relatou, e não só que algo falha. O texto atual já pede falhar "pelo motivo do '
        'bug"; isto diz como reconhecer esse motivo.',
        "for the bug's reason (not an\nimport error or a typo).",
        "for the bug's reason: it asserts the\nexact symptom the spec reports (the wrong value, the message, "
        "the state), so it turns green only\nwhen that symptom is gone (an import error or a typo is not the bug).",
    ),
    Outside(
        "o1b-reproducer-plan",
        "Reprodutor de bug: afirma o sintoma exato (Architect)",
        "loompa.agents.architect:REPRODUCER_NOTE",
        ["mp-bugs"],
        "O mesmo no plano: a primeira tarefa de um bugfix nomeia o sintoma que o teste afirma.",
        "write an automated test that reproduces the bug and FAILS on the current code.",
        "write an automated test that reproduces the bug, asserting the exact symptom it reports, and "
        "FAILS on the current code.",
    ),
    Outside(
        "o2-diagnose-retry",
        "Worker numa nova tentativa: reproduzir, hipóteses, instrumentar",
        "src/loompa/agents/worker.py",
        ["mp-bugs", "sp-debug"],
        "diagnosing-bugs e systematic-debugging: antes de corrigir, reproduzir; depois 2-3 hipóteses "
        "com a previsão de cada uma e testar a mais provável; instrumentar com marca, para a limpeza "
        "ser um grep. É o passo de instrumentar que o plano pede para completar o 7.7. Entra só no "
        "bloco de falha, que aparece nas novas tentativas: não pesa nas tarefas que dão certo de "
        "primeira.",
        '"\\n## Last failure (filtered) — fix this first\\n"\n'
        "                + state.failure_history[-1][:2500]\n"
        '                + "\\n"',
        '"\\n## Last failure (filtered) — fix this first\\n"\n'
        "                + state.failure_history[-1][:2500]\n"
        '                + "\\nReproduce it with `run_tests` before changing code. When the output does not '
        "show the cause, list two or three likely causes, each with the result that would confirm it, "
        "and check the likeliest first; a temporary print marked `[debug]` is fine if you remove it "
        'before `done`.\\n"',
        note="Pede, junto, uma checagem em código: um `[debug]` no diff reprova a autoconferência (DoD).",
    ),
    Outside(
        "o3-blocked-tool",
        "Ferramenta `blocked`: a recomendada primeiro",
        "src/loompa/aci/tools.py",
        ["anthropic-tools", "speckit-clarify"],
        "A descrição da ferramenta é prompt (Anthropic, Writing tools for agents) e é o único lugar onde o "
        "Worker lê o formato das opções. Mesma razão do P9.",
        "`options` are 2-3 choices.",
        f"`options` are 2-3 choices, {RECOMMENDED}.",
    ),
    Outside(
        "o4-ops-check",
        "Ops: uma checagem em código antes de uma frase no prompt",
        "loompa.factory_health:EXPLAIN_SYSTEM",
        ["mp-retro"],
        'retro: um erro mecânico ganha uma checagem determinística, "full stop"; escrever a regra é o '
        "último recurso. É o que a ADR-0026 já decidiu para o plano; o Ops, que propõe consertos da "
        "fábrica, não sabe disso.",
        "factory's code or configuration, naming the part that would change.",
        "factory's code or configuration, naming the part that would change. When the cause is a mistake "
        "a model\nkeeps making, prefer a fix that catches it in code (a check, a guard, a test) over a new "
        "sentence\nin a prompt: a check holds for every call and every model, a sentence only changes the "
        "odds.",
        note="O EXPLAIN_SYSTEM fica fora do registro da bancada (só `loompa.agents` é varrido).",
    ),
    Outside(
        "o5-glossary",
        "Glossário: usar os termos dele",
        "src/loompa/company.py",
        ["mp-domain", "mp-tdd"],
        "domain-modeling, tdd e to-tickets: nomes, testes e textos usam o vocabulário do glossário. "
        'O glossário (13.2) já chega ao Master, ao PO, ao Spec e ao Architect, mas como "dados", sem '
        "dizer que é para usar os termos.",
        'parts.append(f"### {BY_KIND[kind].title} ({BY_KIND[kind].file})\\n{text}")',
        "use = (\n"
        '            " — name things of the product with these terms, in the meaning given"\n'
        '            if kind == "glossary"\n'
        '            else ""\n'
        "        )\n"
        '        parts.append(f"### {BY_KIND[kind].title} ({BY_KIND[kind].file}){use}\\n{text}")',
    ),
]

# --------------------------------------------------------------------------- build and check


def apply(text: str, edits: list[tuple[str, str]], label: str) -> str:
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(
                f"{label}: trecho encontrado {text.count(old)}× (precisa ser 1):\n{old[:200]}"
            )
        text = text.replace(old, new, 1)
    return text


# The prompts as they were before 14.3 changed any of them: every candidate is an edit on this text,
# and the review documents compare against it, whatever the code holds today.
BASE_COMMIT = "d1efdfd"


@cache
def baseline(key: str) -> str:
    prompt = registry.get(key)
    rel = "src/" + prompt.module.replace(".", "/") + ".py"
    source = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{rel}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == prompt.const for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise KeyError(key)


def p0(key: str) -> str:
    return apply(baseline(key), P0.get(key, []), f"P0 {key}")


def check(key: str, text: str, label: str) -> list[str]:
    """What a candidate breaks: the contract the code reads, a dry-run marker, `{language}`."""
    prompt = registry.get(key)
    problems = []
    rendered = prompt.render(template=text)
    if registry.parse_contract(rendered) != prompt.contract:
        problems.append("contrato JSON mudou")
    for m in MARKERS:
        if m in baseline(key) and m not in text:
            problems.append(f"marcador do dry-run sumiu: {m}")
    try:
        baseline(key).format(language="pt-BR")
        text.format(language="pt-BR")
    except (KeyError, IndexError):
        pass  # a template with other fields (the consult prompt) is filled elsewhere
    except ValueError as e:
        problems.append(f"chaves desbalanceadas: {e}")
    if label != "atual" and (hit := PRODUCT_WORDS.search(text)):
        problems.append(f"palavra de produto: {hit.group(0)!r}")
    return problems


def runtime_text(where: str) -> str:
    if ":" in where:
        module, attr = where.split(":")
        return str(getattr(importlib.import_module(module), attr))
    return (ROOT / where).read_text(encoding="utf-8")


def tokens(text: str) -> int:
    return round(len(text) / 4)


def diff(a: str, b: str) -> str:
    return "\n".join(
        difflib.unified_diff(a.splitlines(), b.splitlines(), "atual", "proposto", lineterm="", n=2)
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    problems: list[str] = []
    keys = sorted({*P0, *(k for idea in IDEAS for k in idea.edits)})
    proposed: dict[str, str] = {}
    applied: dict[str, list[Idea]] = {k: [] for k in keys}
    for key in keys:
        base = p0(key)
        if key in P0:
            (OUT / f"{key}.p0-agnostic.txt").write_text(base, encoding="utf-8")
        text = base
        for idea in IDEAS:
            if key not in idea.edits:
                continue
            alone = apply(base, idea.edits[key], f"{idea.id} {key}")
            (OUT / f"{key}.{idea.id}.txt").write_text(alone, encoding="utf-8")
            problems += [f"{key}.{idea.id}: {p}" for p in check(key, alone, idea.id)]
            if idea.proposed:
                text = apply(text, idea.edits[key], f"proposto {idea.id} {key}")
                applied[key].append(idea)
        problems += [f"{key}.proposed: {p}" for p in check(key, text, "proposto")]
        (OUT / f"{key}.proposed.txt").write_text(text, encoding="utf-8")
        proposed[key] = text
    by_id = {i.id: i for i in IDEAS}
    finals: dict[str, str] = {}
    for key, ids in FINAL.items():
        text = p0(key)
        for idea_id in ids:
            text = apply(text, by_id[idea_id].edits[key], f"final {idea_id} {key}")
        problems += [f"{key}.final: {p}" for p in check(key, text, "final")]
        (OUT / f"{key}.final.txt").write_text(text, encoding="utf-8")
        finals[key] = text
    for o in OUTSIDE:
        text = runtime_text(o.where)
        if text.count(o.old) != 1 and o.new not in text:  # neither pending nor adopted
            problems.append(f"{o.id}: trecho não encontrado uma vez em {o.where}")
    REPORT.write_text(render_report(keys, finals), encoding="utf-8")
    print("\n".join(sorted(p.name for p in OUT.glob("*.txt"))))
    print(f"\n{REPORT.relative_to(ROOT)}")
    if problems:
        raise SystemExit("PROBLEMAS:\n" + "\n".join(problems))


# --------------------------------------------------------------------------- the review document


def refs_md(ids: list[str]) -> str:
    return "; ".join(f"[{REFS[r][0]}]({REFS[r][1]})" for r in ids)


def render_report(keys: list[str], finals: dict[str, str]) -> str:
    lines = [
        "# 14.3 — Prompts: antes, depois e o que a bancada disse",
        "",
        "Gerado por `uv run python docs/bench/make_candidates.py`; não editar à mão. A análise e o",
        "método estão em [PROPOSTA-14.3.md](PROPOSTA-14.3.md). **Antes** é o texto de",
        f"`{BASE_COMMIT}`, anterior à 14.3; **depois** é o texto final, que leva só as ideias que a",
        "bancada aprovou (ou que saíram neutras e corrigem uma regra) e é o que está no código.",
        "",
        "Cada prompt traz todas as ideias medidas nele, aprovadas ou não, com o resultado, a",
        "referência e o porquê; depois o diff e os dois textos inteiros.",
        "",
        "## Índice",
        "",
        "| Prompt | No texto final | Fora | Caracteres (antes → depois) | Tokens por chamada |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in keys:
        old, new = baseline(key), finals.get(key, baseline(key))
        kept = (["P0"] if key in P0 else []) + [short(i) for i in FINAL.get(key, [])]
        out = [short(i.id) for i in IDEAS if key in i.edits and i.id not in FINAL.get(key, [])]
        lines.append(
            f"| [`{key}`](#{key.lower().replace('.', '')}) | {', '.join(kept) or '—'} | "
            f"{', '.join(out) or '—'} | {len(old)} → {len(new)} | {tokens(new) - tokens(old):+d} |"
        )
    lines += ["", "Fora da bancada:", ""]
    lines += [f"- [`{o.id}`](#{o.id}) — {o.title}: **{OUTSIDE_RESULTS[o.id][0]}**" for o in OUTSIDE]
    for key in keys:
        old, new = baseline(key), finals.get(key, baseline(key))
        lines += ["", f"## {key}", ""]
        if key in P0:
            verdict, text = RESULTS.get(("p0-agnostic", key), (NEUTRA, ""))
            lines += [
                f"**P0 — sem fábrica, história ou exemplo de produto ({verdict}).** {text}",
                "",
                "Regra do Founder (06/10) e "
                + refs_md(["anthropic-skill-creator"])
                + ": generalizar a partir do caso em vez de remendos colados a ele.",
                "",
            ]
        for idea in IDEAS:
            if key not in idea.edits:
                continue
            verdict, text = RESULTS.get((idea.id, key), ("não medida", ""))
            lines += [f"**{short(idea.id)} — {idea.title} ({verdict}).** {text}", ""]
            lines += [f"*Por quê:* {idea.detail.get(key) or idea.why}", ""]
            lines += [f"*Referências:* {refs_md(idea.refs)}", ""]
            if idea.id not in FINAL.get(key, []):
                lines += [f"*Candidato medido:* `candidates/{key}.{idea.id}.txt`", ""]
        lines += [
            f"Tamanho: {len(old)} → {len(new)} caracteres (~{tokens(old)} → ~{tokens(new)} tokens).",
            "",
        ]
        if new == old:
            lines += ["O texto final é igual ao de antes.", ""]
            continue
        lines += [
            "<details><summary>Diff</summary>",
            "",
            "```diff",
            diff(old, new),
            "```",
            "",
            "</details>",
            "",
            "<details><summary>Antes (inteiro)</summary>",
            "",
            "```text",
            old.rstrip(),
            "```",
            "",
            "</details>",
            "",
            "<details><summary>Depois (inteiro)</summary>",
            "",
            "```text",
            new.rstrip(),
            "```",
            "",
            "</details>",
        ]
    lines += ["", "## Fora da bancada", ""]
    for o in OUTSIDE:
        verdict, text = OUTSIDE_RESULTS[o.id]
        lines += [f"### {o.id}", "", f"**{o.title}** (`{o.where}`): **{verdict}**. {text}", ""]
        lines += [f"*Por quê:* {o.why}", "", f"*Referências:* {refs_md(o.refs)}", ""]
        if o.note:
            lines += [f"*Atenção:* {o.note}", ""]
        lines += [
            "Antes:",
            "",
            "```text",
            o.old,
            "```",
            "",
            "Depois:",
            "",
            "```text",
            o.new,
            "```",
            "",
        ]
    return "\n".join(lines) + "\n"


def short(idea_id: str) -> str:
    return idea_id.split("-", 1)[0].upper()


if __name__ == "__main__":
    main()
