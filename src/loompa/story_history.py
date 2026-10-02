"""The story's history by phase (plan 11.3): one sentence per step, said by who did it.

The history tab used to list checkpoints as `node → stage` with a time, which says that a step
happened but not what came of it. Each checkpoint already saves the whole state, so the sentence is
written here, in code, from what changed since the checkpoint before and from the few events of that
interval that carry a verdict (spec review, Inspector, the founder's answer, the merge). No model call:
the numbers are the ones the phase left, and old stories get a history too.

Founder-facing pt-BR, like every text the panel shows (no paths, error names or task ids).
"""

from __future__ import annotations

from typing import Any

# who owns each step: the role whose Loompa did it (the panel draws its avatar)
OWNER = {
    "admit": "product_owner",
    "node_intake": "master",
    "node_spec": "product",
    "node_spec_review": "product_owner",
    "gate_plan": "factory",
    "gate_open": "factory",
    "node_plan": "architect",
    "node_preflight": "architect",
    "node_dev": "worker",
    "node_test": "inspector",
    "node_review": "deployer",
    "node_research": "analyst",
    "node_research_review": "product_owner",
    "coderabbit_review": "inspector",
    "review_feedback": "inspector",
    "runner_crash": "factory",
    "founder_answer": "founder",
    "restart": "founder",
    "withdrawn": "founder",
}

EVENTS = (
    "spec.reviewed",
    "inspector.verdict",
    "story.escalated",
    "story.blocked",
    "story.merged",
    "story.resumed",
    "inbox.answered",
    "worker.task",
    "story.retry",
)

KIND = {"feature": "funcionalidade", "bugfix": "correção", "research": "pesquisa"}
PHASE = {
    "intake": "classificação",
    "spec": "especificação",
    "spec_review": "revisão da spec",
    "plan": "plano",
    "preflight": "conferência do plano",
    "dev": "código",
    "test": "testes",
    "review": "entrega",
    "research": "pesquisa",
    "research_review": "revisão da pesquisa",
}
ANSWER = {
    "approve": "Você aprovou a entrega",
    "retry": "Você pediu para tentar de novo",
    "changes": "Você pediu ajustes",
    "skip": "Você devolveu a história ao backlog",
    "drop": "Você cancelou a história",
    "detach": "Você mandou seguir sem a dependência",
    "cancel": "Você cancelou a história",
}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _first(evs: list[dict[str, Any]], type_: str) -> dict[str, Any] | None:
    return next((e["payload"] for e in reversed(evs) if e["type"] == type_), None)


def _clip(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def summarize(
    node: str,
    prev: dict[str, Any],
    cur: dict[str, Any],
    stage: str,
    evs: list[dict[str, Any]],
    *,
    prev_node: str = "",
    chosen: str = "",
) -> str:
    """One sentence for the step `node` that took the story from `prev` to `cur`. `chosen` is the
    label of the option the founder picked, when the step is an answer."""
    asked = stage == "AWAITING_FOUNDER"
    done = len(cur.get("tasks_done") or [])
    total = int(cur.get("tasks_total") or 0)
    if node == "admit":
        return "Entrou no sprint."
    if node == "restart":
        return "Recomeçada do zero a seu pedido."
    if node == "withdrawn":
        return "Devolvida ao backlog."
    if node == "runner_crash":
        return "A execução foi interrompida por uma falha e retomada."
    if node == "node_intake":
        kind = KIND.get(cur.get("kind", ""), cur.get("kind", ""))
        route = [PHASE.get(p, p) for p in cur.get("route") or [] if p != "intake"]
        extra = (
            ""
            if cur.get("complexity") in (None, "", "STANDARD")
            else f", {cur['complexity'].lower()}"
        )
        return f"Classificada como {kind}{extra}: " + (" → ".join(route) or "sem etapas") + "."
    if node == "node_spec":
        n = len(cur.get("acceptance") or [])
        text = f"Spec escrita com {_plural(n, 'critério', 'critérios')} de aceite"
        return text + (", e uma dúvida para você." if asked else ".")
    if node == "node_spec_review":
        r = _first(evs, "spec.reviewed") or {}
        if r.get("approved") or (cur.get("spec_ready") and not r):
            return "Spec aprovada pelo Product Owner."
        gaps = []
        if r.get("missing"):
            gaps.append(_plural(int(r["missing"]), "critério faltando", "critérios faltando"))
        if r.get("unsupported"):
            gaps.append(
                _plural(
                    int(r["unsupported"]), "ponto sem base no pedido", "pontos sem base no pedido"
                )
            )
        why = f" ({', '.join(gaps)})" if gaps else ""
        if asked:
            return f"O Product Owner não aprovou a spec{why} e pediu a sua orientação."
        return f"Spec devolvida para ajuste{why}, rodada {cur.get('spec_review_rounds') or 1}."
    if node == "gate_plan":
        return "Esperando as histórias de que depende antes de planejar."
    if node == "gate_open":
        return "O que ela esperava ficou pronto: segue para o plano."
    if node == "node_plan":
        if asked:
            return "O plano parou numa dúvida para você."
        return f"Plano com {_plural(total, 'tarefa', 'tarefas')}."
    if node == "node_preflight":
        added = total - int(prev.get("tasks_total") or 0)
        if added > 0:
            return f"Conferência antes do código: {_plural(added, 'tarefa acrescentada', 'tarefas acrescentadas')} ao plano."
        return "Conferência antes do código: o plano segue como estava."
    if node == "node_dev":
        retry = next(
            (
                e["payload"]
                for e in reversed(evs)
                if e["type"] == "story.retry" and e["payload"].get("cause")
            ),
            None,
        )
        if retry and stage != "TEST":
            if asked:
                return f"Parou depois de várias tentativas ({retry['cause']}) e pediu a sua orientação."
            return f"Parou porque {retry['cause']}; a fábrica tenta de novo sozinha."
        blocked = _first(evs, "story.blocked") or {}
        if asked and blocked.get("reason") == "persistent_failure":
            return "Parou depois de várias tentativas sem sucesso e pediu a sua orientação."
        before = len(prev.get("tasks_done") or [])
        saved = len(cur.get("commits") or []) - len(prev.get("commits") or [])
        cut = sum(
            1 for e in evs if e["type"] == "worker.task" and e["payload"].get("ended_by") == "limit"
        )
        fixing = prev_node in ("node_test", "coderabbit_review", "review_feedback") or (
            prev_node == "founder_answer" and done == before and bool(prev.get("qa_verdict"))
        )
        if fixing:
            parts = [
                "Correções do que foi apontado na revisão"
                + (f", {done} de {total} tarefas" if done > before else "")
            ]
        else:
            parts = [f"{done} de {total} tarefas concluídas" if total else "Correções feitas"]
            if done > before and total:
                parts[0] += f" (+{done - before})"
        if saved > 0:
            parts.append(_plural(saved, "versão salva", "versões salvas"))
        if cut:
            parts.append(
                _plural(cut, "tarefa interrompida", "tarefas interrompidas")
                + " por excesso de passos"
            )
        if asked:
            parts.append("parou e pediu a sua orientação")
        elif stage == "TEST":
            parts.append("foi para os testes")
        return ", ".join(parts) + "."
    if node == "node_test":
        v = _first(evs, "inspector.verdict") or {}
        verdict = v.get("verdict") or cur.get("qa_verdict") or ""
        n = int(v.get("findings") or len(cur.get("qa_findings") or []))
        if verdict == "PASS":
            text = "Testes e revisão do código aprovados"
        elif verdict == "CONCERNS":
            text = f"Aprovada com {_plural(n, 'ressalva', 'ressalvas')}"
        elif verdict == "WAIVED":
            text = "Os testes passam, mas o Inspector viu um risco e pediu a sua decisão"
        elif n:
            text = f"Reprovada com {_plural(n, 'problema', 'problemas')}: volta para o código"
        else:
            text = "Reprovada: volta para o código"
        if _first(evs, "story.escalated"):
            text += ", agora com os modelos mais fortes"
        return text + "."
    if node == "node_review":
        if asked:
            return "Entrega pronta para a sua revisão."
        return "Entrega preparada."
    if node == "node_research":
        return "Pesquisa escrita." + (" Uma dúvida para você." if asked else "")
    if node == "node_research_review":
        return "Pesquisa revisada pelo Product Owner."
    if node in ("coderabbit_review", "review_feedback"):
        return "A revisão externa do código pediu ajustes."
    if node == "founder_answer":
        r = _first(evs, "story.resumed") or {}
        a = _first(evs, "inbox.answered") or {}
        key = r.get("answer") or a.get("option") or ""
        text = ANSWER.get(key) or (
            f"Você escolheu “{_clip(chosen, 80)}”" if chosen else "Você respondeu"
        )
        if key == "changes" and cur.get("founder_notes"):
            note = str(cur["founder_notes"][-1])
            head, sep, rest = note.partition(" / ")  # notes are saved as "option label / text"
            text += f": “{_clip(rest if sep and len(head) < 40 else note)}”"
        if _first(evs, "story.merged"):
            text += "; a história foi integrada à versão principal"
        elif stage == "DONE":
            text += "; a história foi concluída"
        return text + "."
    phase = PHASE.get(cur.get("phase", ""), "")
    return f"Etapa {phase or node.removeprefix('node_')} concluída."


def story_history(store: Any, story_id: str) -> list[dict[str, Any]]:
    """The history tab: one entry per checkpoint, oldest first."""
    points = store.checkpoint_states(story_id)
    events = store.story_events(story_id, EVENTS)
    out: list[dict[str, Any]] = []
    prev: dict[str, Any] = {}
    since = prev_node = ""
    for cp in points:
        evs = [e for e in events if since < e["created_at"] <= cp["created_at"]]
        chosen = ""
        answered = _first(evs, "inbox.answered") if cp["node"] == "founder_answer" else None
        if answered and (msg := store.get_message(answered.get("message_id", ""))):
            chosen = next((o.label for o in msg.options if o.key == answered.get("option")), "")
        out.append(
            {
                "id": cp["id"],
                "at": cp["created_at"],
                "node": cp["node"],
                "stage": cp["stage"],
                "role": OWNER.get(cp["node"], "factory"),
                "summary": summarize(
                    cp["node"],
                    prev,
                    cp["state"],
                    cp["stage"],
                    evs,
                    prev_node=prev_node,
                    chosen=chosen,
                ),
            }
        )
        prev, since, prev_node = cp["state"], cp["created_at"], cp["node"]
    return out
