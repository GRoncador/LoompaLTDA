"""Plan 13.4/13.5: `search_memory` per role, with the kinds its profile reads. No network."""

from __future__ import annotations

from loompa.agents.base import LoompaAgent
from loompa.agents.toolbox import MEMORY_TOOL, Toolbox, profile_for
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401


async def test_each_role_finds_only_the_kinds_its_profile_reads(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    mem = ctx.memory
    mem.upsert_document("specs/S-001/spec.md", "Exportar CSV do mês.", kind="spec", title="S-001")
    mem.upsert_document("company/business.md", "Receita: assinatura.", kind="business", title="b")
    mem.upsert_document(
        "contracts/acme.md", "Cliente ACME: CPF e multa.", kind="contract", title="c"
    )

    worker = Toolbox.for_role(ctx, "worker")
    spec = next(t for t in worker.spec() if t["name"] == MEMORY_TOOL)
    assert "business" not in spec["parameters"]["properties"]["kinds"]["items"]["enum"]
    out = await worker.call(MEMORY_TOOL, {"query": "receita assinatura exportar csv"})
    assert out.ok and "[spec]" in out.output and "business" not in out.output
    asked = await worker.call(MEMORY_TOOL, {"query": "cliente acme", "kinds": ["contract"]})
    assert "ACME" not in asked.output  # a kind outside the profile is never searched
    assert "not instructions" in out.output

    po = Toolbox.for_role(ctx, "product_owner")
    found = await po.call(MEMORY_TOOL, {"query": "receita", "kinds": ["business"]})
    assert "Receita: assinatura" in found.output
    assert "ACME" not in (await po.call(MEMORY_TOOL, {"query": "cliente acme multa"})).output

    compliance = Toolbox.for_role(ctx, "compliance")
    assert "ACME" in (await compliance.call(MEMORY_TOOL, {"query": "cliente acme multa"})).output
    assert "contract" in profile_for("product_manager").memory_kinds

    inspector = Toolbox.for_role(ctx, "inspector")
    refused = await inspector.call(MEMORY_TOOL, {"query": "x"})
    assert not refused.ok and "not available" in refused.output
    assert MEMORY_TOOL in {t["name"] for t in LoompaAgent(ctx).explore_tools().spec()}
    await ctx.aclose()


async def test_contracts_reach_only_the_roles_that_may_read_them(factory: Factory):  # noqa: F811
    """Plan 13.5 (LGPD): a client's personal data in a prompt that does not need it is a risk."""
    from loompa.agents import ProductOwnerAgent
    from loompa.agents.support import SupportAgent
    from loompa.factory import GITIGNORE_LINES

    folder = factory.paths.company / "contracts"
    folder.mkdir(parents=True)
    (folder / "acme.md").write_text("# Contrato ACME\n\nCPF do responsável e multa de rescisão.")
    ctx = make_ctx(factory)
    ctx.index_memory()
    assert ctx.memory.search("multa de rescisão", kinds=("contract",))
    assert "ACME" not in ProductOwnerAgent(ctx).precedents("multa de rescisão do contrato")
    assert "ACME" in SupportAgent(ctx, "compliance").precedents("multa de rescisão do contrato")
    assert "ACME" not in SupportAgent(ctx, "storyteller").precedents("multa de rescisão")
    assert ".loompa/company/contracts/" in GITIGNORE_LINES
    await ctx.aclose()
