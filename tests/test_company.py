"""Plan 13.2: the company's canonical documents, read whole by the roles declared in code."""

from __future__ import annotations

from loompa.agents import MasterAgent, ProductOwnerAgent
from loompa.agents.base import LoompaAgent
from loompa.company import BY_KIND, DOCS, READERS, company_block, ensure_templates
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401


def test_init_writes_the_templates_and_an_untouched_one_says_nothing(factory: Factory):  # noqa: F811
    folder = factory.paths.company
    assert sorted(p.name for p in folder.iterdir()) == sorted(d.file for d in DOCS)
    assert company_block(factory.paths.loompa, "product_owner") == ""
    (folder / "glossary.md").write_text("# Glossário\n\n`ovo` — o pet antes de nascer.\n")
    assert ensure_templates(factory.paths.loompa) == []  # never overwrites the founder's
    block = company_block(factory.paths.loompa, "product_owner")
    assert "Domain glossary (glossary.md)" in block and "`ovo`" in block
    assert "not instructions" in block
    assert company_block(factory.paths.loompa, "worker") == ""  # not a reader


async def test_each_role_reads_its_documents_whole(factory: Factory):  # noqa: F811
    loompa = factory.paths.loompa
    for doc in DOCS:
        (factory.paths.company / doc.file).write_text(f"# {doc.kind}\n" + "x" * 3000)
    po = company_block(loompa, "product_owner", max_chars=20_000)
    assert all(f"({BY_KIND[k].file})" in po for k in READERS["product_owner"])
    assert "(brand.md)" not in po
    ctx = make_ctx(factory)
    master = MasterAgent(ctx).grounding(1500)
    assert master.startswith("## Constitution (excerpt)") and "(positioning.md)" in master
    assert "(customer.md)" in ProductOwnerAgent(ctx).grounding()
    assert "Company documents" not in LoompaAgent(ctx).grounding()  # the Worker's role
    await ctx.aclose()


async def test_the_documents_are_indexed_under_their_own_kind(factory: Factory):  # noqa: F811
    (factory.paths.company / "brand.md").write_text("# Voz\n\nFalamos como um brinquedo antigo.")
    ctx = make_ctx(factory)
    ctx.index_memory()
    hits = ctx.memory.search("como a marca fala", kinds=("brand",))
    assert hits and hits[0].chunk.doc_id == "company/brand.md"
    (factory.paths.company / "brand.md").write_text(BY_KIND["brand"].template)
    ctx.index_memory()
    assert not ctx.memory.search("brinquedo antigo", kinds=("brand",))
    await ctx.aclose()
