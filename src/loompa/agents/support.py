"""On-demand support Loompas: Compliance (LGPD), Metrics (SQL/analysis), Storyteller (copy/docs).

One-shot Tier 2 calls with organizational memory as context. Output goes to the Founder inbox
as an INFO message and, optionally, to a file the Founder names.
"""

from __future__ import annotations

from loompa.agents.base import LoompaAgent
from loompa.comms import FounderMessage, MessageKind, sanitize_for_founder

PROMPTS: dict[str, str] = {
    "compliance": """<!-- role:compliance -->
You are the Compliance Loompa (LGPD/GDPR, terms of use, privacy policy, consent). Answer the
founder's request with concrete, actionable text for this product, citing the principle or article
that applies. Base it on the constitution and precedents given; when something depends on facts you
do not have (what data is collected, where it is stored), say so instead of assuming. This is
guidance, not legal advice: say when a lawyer should review it.
Respond with JSON only: {{"title": str, "body": str}}. Write both in {language}.
""",
    "metrics": """<!-- role:metrics -->
You are the Metrics Loompa (product analytics, SQL). Given the request and the project's data model
from memory, define the metric, write the read-only SQL (SELECT only) that computes it, and explain
it to a non-technical founder in two sentences. Use only tables and columns the data model shows;
when one is missing, say so instead of inventing it.
Respond with JSON only: {{"title": str, "body": str, "sql": str}}. Write `title` and `body` in
{language}.
""",
    "storyteller": """<!-- role:storyteller -->
You are the Storyteller Loompa (copywriting, SEO, product documentation). Produce the requested copy
or document for this product, true to its constitution and mission. Describe only what the product
does according to the material given; never invent features, numbers or testimonials.
Respond with JSON only: {{"title": str, "body": str}}. Write both in {language}.
""",
}


class SupportAgent(LoompaAgent):
    display = "Support Loompa"

    def __init__(self, ctx, role: str):
        if role not in PROMPTS:
            raise ValueError(f"papel de suporte desconhecido: {role}")
        super().__init__(ctx, name=f"{role.capitalize()} Loompa")
        self.role = role  # type: ignore[assignment]

    async def ask(self, request: str) -> FounderMessage:
        self.set_state("WORKING", detail=request[:60])
        precedents = self.precedents(request)
        data = await self.ask_json(
            PROMPTS[self.role].format(language=self.language),
            f"# Founder's request\n{request}\n\n## Constitution (excerpt)\n{self.constitution(3000)}\n\n{precedents}",
            max_tokens=2500,
        )
        body = str(data.get("body") or "")
        if data.get("sql"):
            body += f"\n\nConsulta sugerida:\n{data['sql']}"
        msg = FounderMessage(
            factory=self.ctx.slug,
            kind=MessageKind.INFO,
            sender=self.name,
            title=sanitize_for_founder(str(data.get("title") or request), max_chars=160),
            context=body[:6000],
            options=[],
            allow_free_text=False,
        )
        self.ctx.inbox(msg)
        self.set_state("IDLE")
        return msg
