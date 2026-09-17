"""Shared plumbing for every Loompa agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loompa.engine.context import EngineContext
from loompa.engine.state import StoryState
from loompa.llm import LLMError, Message
from loompa.llm.providers import extract_json


@dataclass
class AgentResult:
    ok: bool
    summary: str = ""
    data: dict[str, Any] | None = None
    blocked_reason: str | None = None
    blocked_options: list[str] | None = None


class LoompaAgent:
    role: str = "worker"
    display: str = "Loompa"

    def __init__(self, ctx: EngineContext, *, name: str | None = None):
        self.ctx = ctx
        self.name = name or self.display

    @property
    def language(self) -> str:
        return self.ctx.config.factory.language

    def constitution(self, max_chars: int = 6000) -> str:
        text = self.ctx.factory.constitution_text()
        return text[:max_chars]

    def precedents(self, query: str, *, kinds: tuple[str, ...] | None = None) -> str:
        try:
            return self.ctx.memory.recall(query, top_k=self.ctx.config.memory.top_k, kinds=kinds)
        except Exception:  # noqa: BLE001 - memory is best-effort
            return ""

    def set_state(
        self, state: str, story: StoryState | None = None, *, model: str = "", detail: str = ""
    ) -> None:
        self.ctx.agent_state(
            self.name,
            self.role,
            state,
            story_id=story.story_id if story else None,
            model=model,
            detail=detail,
        )

    async def ask_json(
        self,
        system: str,
        user: str,
        *,
        story: StoryState | None = None,
        tier_override: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One-shot structured call; retries once asking for valid JSON."""
        messages = [Message("system", system), Message("user", user)]
        for attempt in range(2):
            routed = await self.ctx.router.complete(
                self.role,
                messages,
                agent=self.name,
                story_id=story.story_id if story else None,
                tier_override=tier_override,
                json_mode=True,
                max_tokens=max_tokens,
            )
            try:
                data = extract_json(routed.response.text)
                if isinstance(data, dict):
                    return data
                return {"items": data}
            except ValueError:
                if attempt == 1:
                    raise LLMError("modelo não devolveu JSON válido") from None
                messages += [
                    Message("assistant", routed.response.text),
                    Message(
                        "user", "Responda APENAS com um objeto JSON válido, sem texto ao redor."
                    ),
                ]
        raise LLMError("modelo não devolveu JSON válido")

    @staticmethod
    def _list(data: dict[str, Any], key: str) -> list[str]:
        val = data.get(key) or []
        if isinstance(val, str):
            val = [val]
        return [str(v).strip() for v in val if str(v).strip()]

    @staticmethod
    def dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2)
