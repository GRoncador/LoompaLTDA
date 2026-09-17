"""Non-technical onboarding summary presented at the first morning meeting."""

from __future__ import annotations

from loompa.onboarding.scanner import RepoAudit


def executive_onboarding_summary(audit: RepoAudit, name: str) -> str:
    s = audit.stack
    lines = [f"# Relatório de Onboarding — {name}", ""]
    lines.append("## O que encontramos (em linguagem simples)")
    lines.append(
        f"- O projeto tem cerca de {audit.file_count} arquivos, escrito principalmente em {audit.primary_language}."
    )
    if s.frameworks:
        lines.append(f"- Ele é construído com: {', '.join(s.frameworks)}.")
    if s.databases:
        lines.append(f"- Guarda dados em: {', '.join(s.databases)}.")
    if audit.git.is_repo:
        lines.append(
            f"- Histórico: {audit.git.commits} alterações registradas por {audit.git.contributors} pessoa(s); "
            f"última em {audit.git.last_commit_iso[:10] or 'data desconhecida'}."
        )
    if audit.test_file_count:
        lines.append(
            f"- Existem {audit.test_file_count} arquivos de verificação automática (testes). Cobertura: {audit.coverage_hint}."
        )
    else:
        lines.append(
            "- Não há verificações automáticas (testes). Vamos criar uma base antes de mexer em funcionalidades."
        )
    lines.append(
        "- Padrões de qualidade: "
        + (
            f"ferramentas de estilo já configuradas ({', '.join(s.linters)})."
            if s.linters
            else "nenhuma ferramenta de estilo configurada ainda."
        )
    )
    if audit.ci_files:
        lines.append("- Já existe uma esteira de verificação automática a cada alteração.")
    lines.append("")
    lines.append("## O que a fábrica fará com isso")
    lines.append(
        "- Registrou a arquitetura observada na Constituição do projeto para evitar invenções de bibliotecas."
    )
    lines.append("- Indexou documentação, decisões e esquemas na memória para consultas futuras.")
    if audit.warnings:
        lines.append("")
        lines.append("## Pontos de atenção")
        lines.extend(f"- {w}" for w in audit.warnings)
    lines.append("")
    lines.append("## Para validar rapidamente")
    lines.append(
        "- A descrição acima está correta? Responda na Caixa de Entrada com ‘confirmo’ ou ajuste o que estiver errado."
    )
    return "\n".join(lines) + "\n"
