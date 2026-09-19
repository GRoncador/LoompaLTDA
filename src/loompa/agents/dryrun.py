"""Scripted provider so the whole pipeline runs with zero API keys (`loompa run --dry-run`).

It routes on the `<!-- role:xxx -->` marker each agent puts in its system prompt and returns
plausible, deterministic answers. The Worker writes a tiny marker file and calls `done`, so the
worktree/commit/test/deliver path is exercised for real.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loompa.llm import LLMResponse, Message, MockProvider, ToolCall

_ROLE = re.compile(r"<!-- role:(\w+) -->")


def role_of(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "system":
            mm = _ROLE.search(m.content)
            if mm:
                return mm.group(1)
    return "unknown"


def _story_title(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "user":
            mm = re.search(r"(?:Story|story)(?: (S-\d+))?[:—\- ]+\s*(.+)", m.content)
            if mm:
                return mm.group(2).splitlines()[0].strip()
    return "história"


def _founder_message(messages: list[Message]) -> str:
    """The founder's latest chat message, as `run_turn` lays it out in the prompt."""
    return messages[-1].content.split("## Founder's message\n", 1)[-1].strip()


def dry_run_script(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
    role = role_of(messages)
    title = _story_title(messages)
    if role == "master":
        if "Classify the story" in messages[0].content:
            researching = re.search(
                r"^# Story S-\d+: (pesquis|research)", messages[-1].content, re.I
            )
            return json.dumps(
                {
                    "kind": "research" if researching else "feature",
                    "complexity": "STANDARD",
                    "children": [],
                    "reason": "simulação",
                }
            )
        if "Sprint Meeting" in messages[0].content:
            said = _founder_message(messages)
            parts = [p.strip(" -•*") for p in re.split(r"[;\n]+", said) if p.strip(" -•*")]
            return json.dumps(
                {
                    "reply": f"Simulação: anotei {len(parts)} história(s) no rascunho do sprint.",
                    "ops": [
                        {
                            "op": "add",
                            "title": p[:80],
                            "description": p,
                            "epic": "dia",
                            "priority": 3,
                            "in_sprint": True,
                        }
                        for p in parts
                    ],
                    "questions": [],
                }
            )
        user = messages[-1].content
        problem = user.split("Problem:\n", 1)[-1].split("\n\nSuggested options:", 1)[0].strip()
        suggested = (
            user.rsplit("Suggested options: ", 1)[-1].strip()
            if "Suggested options: " in user
            else "none"
        )
        options = [
            {"key": "retry", "label": "Tentar de novo", "recommended": True},
            {"key": "skip", "label": "Deixar para depois"},
        ]
        if suggested not in ("none", "None", "[]"):
            try:
                import ast

                labels = ast.literal_eval(suggested)
                options = [
                    {"key": f"opt{i + 1}", "label": str(o), "recommended": i == 0}
                    for i, o in enumerate(labels)
                ]
            except (ValueError, SyntaxError):
                pass
        first = problem.splitlines()[0] if problem else "precisamos de uma decisão"
        return json.dumps(
            {
                "title": f"Precisamos da sua orientação em “{title}”: {first[:120]}",
                "context": problem[:600] or "A equipe tentou algumas abordagens sem sucesso.",
                "impact": "As outras entregas seguem normalmente.",
                "options": options,
            }
        )
    if role == "product":
        return json.dumps(
            {
                "goal": f"Entregar {title}",
                "in_scope": [title],
                "out_of_scope": ["qualquer outra funcionalidade"],
                "acceptance": [
                    f"Dado o produto, quando '{title}' estiver implementado, então os testes automáticos passam."
                ],
                "rules": [],
                "questions": [],
                "needs_decision": False,
            }
        )
    if role == "architect":
        if "Falha (filtrada)" in messages[-1].content:
            return json.dumps(
                {
                    "rule": "Sempre rodar a suíte completa antes de concluir uma tarefa.",
                    "generalizable": True,
                }
            )
        return json.dumps(
            {
                "approach": "Implementação mínima e testada (simulação).",
                "files": ["loompa_dryrun/", "tests/"],
                "contracts": "",
                "risks": ["simulação: nenhum código real é gerado"],
                "tasks": [f"Registrar a entrega '{title}' (simulação)"],
                "adr_proposal": "",
            }
        )
    if role == "worker":
        has_tool_result = any(m.role == "tool" for m in messages)
        if not has_tool_result:
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "story"
            return [
                ToolCall(
                    "c1",
                    "write_file",
                    {
                        "path": f"loompa_dryrun/{slug}.md",
                        "content": f"# {title}\n\nEntrega simulada pelo dry-run.\n",
                    },
                )
            ]
        return [ToolCall("c2", "done", {"summary": f"'{title}' registrada (simulação)"})]
    if role == "analyst" and "Brainstorming session" in messages[0].content:
        said = _founder_message(messages)
        return json.dumps(
            {
                "reply": "Simulação: registrei uma ideia a partir do que você disse. "
                "Sem busca na web nem análise real.",
                "ops": [
                    {
                        "op": "add",
                        "title": f"Ideia: {said[:70]}",
                        "description": said,
                        "priority": 3,
                    }
                ],
                "questions": [],
            }
        )
    if role == "analyst":
        return json.dumps(
            {
                "question": title,
                "summary": f"Resposta simulada do Analyst para: {title}.",
                "findings": [{"text": "Achado simulado, sem busca real.", "sources": []}],
                "recommendation": "Repetir com a busca na web configurada para dados reais.",
                "limitations": [],
                "follow_ups": [],
                "needs_decision": False,
            }
        )
    if role == "product_owner" and "## Ideas to review" in messages[-1].content:
        keys = re.findall(r"^- (D\d+) ", messages[-1].content, re.M)
        return json.dumps(
            {"verdicts": [{"key": k, "admit": True, "reason": "", "priority": 3} for k in keys]}
        )
    if role == "product_owner":
        return json.dumps({"approved": True, "unsupported": [], "missing": [], "notes": "ok"})
    if role == "inspector":
        return json.dumps({"criteria": [], "findings": [], "summary": "ok"})
    if role == "dod":
        return json.dumps({"complete": True, "missing": []})
    if role in ("compliance", "metrics", "storyteller"):
        req = (
            messages[-1]
            .content.split("# Pedido do Founder\n", 1)[-1]
            .split("\n\n## Constitution", 1)[0]
            .strip()
        )
        out = {
            "title": f"{role}: {req[:60]}",
            "body": f"Resposta simulada do {role} Loompa para: {req}",
        }
        if role == "metrics":
            out["sql"] = "SELECT COUNT(*) FROM users;"
        return json.dumps(out)
    if role == "deployer":
        return json.dumps(
            {
                "summary": f"'{title}' está pronta para você olhar. Foi uma simulação, sem código real."
            }
        )
    return LLMResponse(
        content="{}",
        tool_calls=[],
        model=model,
        provider="dry-run",
        input_tokens=10,
        output_tokens=2,
    )


def dry_run_provider() -> MockProvider:
    return MockProvider("dry-run", script=dry_run_script)
