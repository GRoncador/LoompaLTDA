"""Does a plan write code in a language the product is not built in?

Cost review of 2026-10-03: Tamagotchi S-006 planned its persistence in Python inside a JavaScript
product, was re-planned the other way after two failures, and was cancelled at 17% of the
week's spend. The factory's stack (`config.stack.languages`, filled by the onboarding scanner) is
the fact; a plan whose source files leave it gets one re-plan with the mismatch named, then the
founder decides.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from loompa.onboarding.scanner import _LANG_BY_EXT

# In the story's `extra`: the note for the one re-plan, then "asked" (the founder was asked) or
# "accepted" (the founder let the plan go on).
STACK_KEY = "stack_check"
STACK_STATES = ("asked", "accepted")

# One family: a TypeScript product with a .js config file is not a second language.
_FAMILY = {"typescript": "javascript"}
# Languages that sit next to any stack (migrations, scripts): never a mismatch on their own.
_NEUTRAL = {"sql", "shell"}


def _family(language: str) -> str:
    name = language.strip().lower()
    return _FAMILY.get(name, name)


def foreign_files(paths: list[str], languages: list[str]) -> dict[str, list[str]]:
    """The plan's source files whose language is outside the stack, by language. Empty when the
    stack is unknown: no stack, nothing to compare against."""
    stack = {_family(lang) for lang in languages if lang.strip()}
    if not stack:
        return {}
    out: dict[str, list[str]] = {}
    for path in paths:
        language = _LANG_BY_EXT.get(PurePosixPath(path).suffix.lower())
        if not language or language.lower() in _NEUTRAL or _family(language) in stack:
            continue
        out.setdefault(language, []).append(path)
    return out


def mismatch_note(foreign: dict[str, list[str]], languages: list[str]) -> str:
    """For the Architect's re-plan (English: a model reads it)."""
    listed = "; ".join(f"{lang}: {', '.join(files[:4])}" for lang, files in foreign.items())
    return (
        f"The product is built in {', '.join(languages)}, but the previous plan wrote source "
        f"files in another language ({listed}). Plan in the product's language. Before adding a "
        "module, look for one that already does the job and extend or port it instead of "
        "writing a second implementation. Use another language only if the spec explicitly "
        "asks for it, and then say why in `approach`.\n\n"
    )
