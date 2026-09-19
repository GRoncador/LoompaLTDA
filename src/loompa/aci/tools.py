"""Agent-Computer Interface: the only tools a Worker/Inspector Loompa gets.

No raw bash. Reads are paginated, edits are structured (exact replace / unified diff),
searches are exact, and command output is compacted before it reaches the model.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loompa.aci.filters import summarize_lint, summarize_tests, summarize_typecheck
from loompa.aci.runner import run_command
from loompa.memory.lexical import CodeSearch, SymbolIndex


class ToolError(Exception):
    pass


_SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
_KEY_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_KEY_FILE_NAMES = {"secrets.env", "id_rsa", "id_ed25519", "id_ecdsa"}


def is_protected(rel: str | Path) -> bool:
    """Files no agent tool may read or write: credentials, git internals and the factory's own
    state. A model that browses the web must not be able to be talked into reading a key."""
    parts = Path(rel).parts
    if not parts:
        return False
    name = parts[-1]
    if ".git" in parts:
        return True
    if name == ".env" or (name.startswith(".env.") and name not in _SAFE_ENV_FILES):
        return True
    if name in _KEY_FILE_NAMES or name.endswith(_KEY_FILE_SUFFIXES):
        return True
    return parts[0] == ".loompa" and (
        name.endswith((".db", ".db-wal", ".db-shm")) or "logs" in parts or "worktrees" in parts
    )


@dataclass
class ToolResult:
    ok: bool
    output: str

    def __str__(self) -> str:
        return self.output


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Lê um arquivo paginado por linhas (máx 200 por chamada).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "default": 1},
                "lines": {"type": "integer", "default": 120},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "Lista arquivos e pastas (não recursivo, ignora node_modules/.venv).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
    },
    {
        "name": "search",
        "description": "Busca exata (regex) no código; retorna arquivo:linha e trecho.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "find_symbol",
        "description": "Localiza definição exata de função/classe/símbolo (AST).",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "write_file",
        "description": "Cria ou sobrescreve um arquivo inteiro (use só para arquivos novos ou pequenos).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Substitui um trecho exato (old) por outro (new) em um arquivo. `old` deve ser único.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Aplica um unified diff (formato `git diff`) em um ou mais arquivos.",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
        },
    },
    {
        "name": "run_tests",
        "description": "Roda a suíte de testes do projeto; retorna apenas as falhas relevantes.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "argumento extra opcional (ex.: tests/test_x.py)",
                }
            },
        },
    },
    {
        "name": "run_lint",
        "description": "Roda o linter/type-checker configurado; retorna apenas os apontamentos.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "done",
        "description": "Sinaliza que a tarefa atual foi concluída, com um resumo de uma frase.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
    {
        "name": "blocked",
        "description": "Sinaliza que não é possível prosseguir sem uma decisão humana; explique em linguagem simples.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "note_learning",
        "description": "Registra um bug colateral, débito técnico ou oportunidade fora do escopo (não corrija!).",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["bug", "tech_debt", "opportunity", "architecture"],
                },
            },
            "required": ["title"],
        },
    },
]


class ACI:
    def __init__(
        self,
        root: Path,
        *,
        test_command: str = "",
        lint_command: str = "",
        typecheck_command: str = "",
        allowed_paths: list[str] | None = None,
        max_read_lines: int = 200,
    ):
        self.root = Path(root).resolve()
        self.test_command = test_command
        self.lint_command = lint_command
        self.typecheck_command = typecheck_command
        self.allowed_paths = allowed_paths
        self.max_read_lines = max_read_lines
        self._search = CodeSearch(self.root)
        self._symbols: SymbolIndex | None = None
        self.learnings: list[dict[str, str]] = []
        self.touched: set[str] = set()

    # ------------------------------------------------------------------ helpers
    def _resolve(self, path: str, *, for_write: bool = False) -> Path:
        p = (self.root / path).resolve()
        if self.root not in (p, *p.parents):
            raise ToolError(f"caminho fora do repositório: {path}")
        if is_protected(p.relative_to(self.root)):
            raise ToolError(f"arquivo protegido (credenciais ou estado interno): {path}")
        if for_write and self.allowed_paths is not None:
            rel = str(p.relative_to(self.root))
            if not any(
                rel == a or rel.startswith(a.rstrip("/") + "/") or Path(rel).match(a)
                for a in self.allowed_paths
            ):
                raise ToolError(
                    f"edição fora do escopo do plano: {rel}. Use note_learning para registrar a necessidade."
                )
        return p

    def spec(self) -> list[dict[str, Any]]:
        return TOOL_SPECS

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return ToolResult(False, f"ferramenta desconhecida: {name}")
        try:
            out = await handler(**args) if name in ("run_tests", "run_lint") else handler(**args)
            return ToolResult(True, out)
        except ToolError as exc:
            return ToolResult(False, f"erro: {exc}")
        except TypeError as exc:
            return ToolResult(False, f"argumentos inválidos para {name}: {exc}")

    # -------------------------------------------------------------------- tools
    def tool_read_file(self, path: str, start: int = 1, lines: int = 120) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"arquivo não existe: {path}")
        lines = max(1, min(int(lines), self.max_read_lines))
        start = max(1, int(start))
        content = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(content)
        chunk = content[start - 1 : start - 1 + lines]
        body = "\n".join(f"{start + i:5d}| {line}" for i, line in enumerate(chunk))
        end = start + len(chunk) - 1
        more = f"\n… ({total - end} linhas restantes; use start={end + 1})" if end < total else ""
        return f"{path} [{start}-{end} de {total}]\n{body}{more}"

    def tool_list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            raise ToolError(f"pasta não existe: {path}")
        skip = {"node_modules", ".venv", "__pycache__", ".git", ".loompa", "dist", "build"}
        entries = sorted(e for e in p.iterdir() if e.name not in skip)
        return "\n".join(f"{e.name}/" if e.is_dir() else e.name for e in entries[:300]) or "(vazio)"

    def tool_search(self, pattern: str, glob: str | None = None) -> str:
        hits = self._search.grep(pattern, glob=glob)
        if not hits:
            return "nenhuma ocorrência"
        return "\n".join(f"{h.path}:{h.line}: {h.text}" for h in hits[:60])

    def tool_find_symbol(self, name: str) -> str:
        if self._symbols is None:
            self._symbols = SymbolIndex.build(self.root)
        syms = self._symbols.find(name) or self._symbols.find(name, exact=False)
        if not syms:
            return f"símbolo não encontrado: {name}"
        return "\n".join(
            f"{s.kind} {s.name} — {s.path}:{s.line}\n    {s.signature}" for s in syms[:20]
        )

    def tool_write_file(self, path: str, content: str) -> str:
        p = self._resolve(path, for_write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        self.touched.add(str(p.relative_to(self.root)))
        self._symbols = None
        return (
            f"{'sobrescrito' if existed else 'criado'}: {path} ({len(content.splitlines())} linhas)"
        )

    def tool_edit_file(self, path: str, old: str, new: str) -> str:
        p = self._resolve(path, for_write=True)
        if not p.is_file():
            raise ToolError(f"arquivo não existe: {path}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ToolError("trecho `old` não encontrado; leia o arquivo e copie o trecho exato")
        if count > 1:
            raise ToolError(
                f"trecho `old` aparece {count} vezes; inclua mais contexto para ser único"
            )
        updated = text.replace(old, new, 1)
        p.write_text(updated, encoding="utf-8")
        self.touched.add(str(p.relative_to(self.root)))
        self._symbols = None
        diff = difflib.unified_diff(text.splitlines(), updated.splitlines(), lineterm="", n=1)
        return "\n".join(list(diff)[2:20])

    def tool_apply_patch(self, patch: str) -> str:
        files = _parse_unified_diff(patch)
        if not files:
            raise ToolError("patch vazio ou em formato não reconhecido (use unified diff)")
        applied = []
        for rel, hunks in files.items():
            p = self._resolve(rel, for_write=True)
            original = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
            updated = _apply_hunks(original, hunks)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
            self.touched.add(rel)
            applied.append(rel)
        self._symbols = None
        return "patch aplicado em: " + ", ".join(applied)

    async def tool_run_tests(self, selector: str | None = None) -> str:
        if not self.test_command:
            return "[tests] nenhum comando de teste configurado (quality.test_command)"
        cmd = f"{self.test_command} {selector}".strip() if selector else self.test_command
        res = await run_command(cmd, self.root, timeout=900)
        if res.timed_out:
            return "[tests] FAIL: tempo esgotado (900s)"
        return summarize_tests(res.output, res.returncode).compact()

    async def tool_run_lint(self) -> str:
        parts = []
        if self.lint_command:
            res = await run_command(self.lint_command, self.root, timeout=300)
            parts.append(summarize_lint(res.output, res.returncode).compact())
        if self.typecheck_command:
            res = await run_command(self.typecheck_command, self.root, timeout=600)
            parts.append(summarize_typecheck(res.output, res.returncode).compact())
        return "\n".join(parts) or "[lint] nenhum comando configurado"

    def tool_done(self, summary: str) -> str:
        return f"DONE: {summary}"

    def tool_blocked(self, reason: str, options: list[str] | None = None) -> str:
        return "BLOCKED: " + json.dumps(
            {"reason": reason, "options": options or []}, ensure_ascii=False
        )

    def tool_note_learning(self, title: str, detail: str = "", kind: str = "opportunity") -> str:
        self.learnings.append({"title": title, "detail": detail, "kind": kind})
        return f"registrado para o backlog: {title}"


# ----------------------------------------------------------------------- patching

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(patch: str) -> dict[str, list[tuple[int, list[str]]]]:
    files: dict[str, list[tuple[int, list[str]]]] = {}
    current: str | None = None
    hunk_lines: list[str] = []
    hunk_start = 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name.startswith("b/"):
                name = name[2:]
            current = name
            files.setdefault(current, [])
            continue
        if line.startswith(("--- ", "diff --git", "index ")):
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            if hunk_lines:
                files[current].append((hunk_start, hunk_lines))
            hunk_start = int(m.group(1))
            hunk_lines = []
            continue
        if current is not None and line[:1] in (" ", "+", "-", "\\"):
            if not line.startswith("\\"):
                hunk_lines.append(line)
    if current is not None and hunk_lines:
        files[current].append((hunk_start, hunk_lines))
    return {k: v for k, v in files.items() if v}


def _apply_hunks(original: list[str], hunks: list[tuple[int, list[str]]]) -> list[str]:
    result = list(original)
    offset = 0
    for start, lines in hunks:
        before = [line[1:] for line in lines if line[0] in " -"]
        after = [line[1:] for line in lines if line[0] in " +"]
        idx = start - 1 + offset
        if result[idx : idx + len(before)] != before:
            found = _locate(result, before, idx)
            if found is None:
                raise ToolError(f"hunk @@ -{start} não casa com o arquivo atual; releia o arquivo")
            idx = found
        result[idx : idx + len(before)] = after
        offset += len(after) - len(before)
    return result


def _locate(haystack: list[str], needle: list[str], near: int) -> int | None:
    if not needle:
        return near
    candidates = [
        i for i in range(len(haystack) - len(needle) + 1) if haystack[i : i + len(needle)] == needle
    ]
    if not candidates:
        stripped = [n.strip() for n in needle]
        candidates = [
            i
            for i in range(len(haystack) - len(needle) + 1)
            if [h.strip() for h in haystack[i : i + len(needle)]] == stripped
        ]
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs(i - near))
