"""Decide whether a directory is a greenfield or brownfield project."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loompa.config.schema import Mode

_IGNORABLE = {".git", ".loompa", ".DS_Store", ".gitignore", ".venv", "node_modules", "__pycache__"}


def _tracked_file_count(path: Path) -> int | None:
    if not (path / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return len([line for line in out.splitlines() if line.strip()])


def detect_mode(path: Path) -> Mode:
    """Greenfield when the directory has no meaningful files; brownfield otherwise.

    A repo with a `.git` folder but no tracked files (fresh `git init`) is still greenfield.
    """
    path = Path(path)
    if not path.exists():
        return "greenfield"
    visible = [p for p in path.iterdir() if p.name not in _IGNORABLE]
    meaningful = [
        p for p in visible if not (p.is_file() and p.name.lower().startswith(("readme", "license")))
    ]
    tracked = _tracked_file_count(path)
    if tracked:
        return "brownfield"
    return "brownfield" if meaningful else "greenfield"
