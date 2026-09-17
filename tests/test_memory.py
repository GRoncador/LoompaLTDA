from pathlib import Path

from loompa.memory import CodeSearch, HashEmbedder, MemoryStore, SymbolIndex, get_embedder
from loompa.memory.store import chunk_text


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashEmbedder(dim=64)
    a = e.embed(["autenticação de tokens JWT"])
    b = e.embed(["autenticação de tokens JWT"])
    assert (a == b).all() and abs(float((a[0] ** 2).sum()) - 1.0) < 1e-5
    assert get_embedder(None).name == "hash-ngram"
    assert get_embedder("does/not-exist").name == "hash-ngram"


def test_chunking_respects_paragraphs():
    text = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(5))
    chunks = chunk_text(text, size=800)
    assert len(chunks) == 3 and all(len(c) <= 800 for c in chunks)
    assert chunk_text("y" * 2000, size=800, overlap=100)[1].startswith("y")


def test_memory_store_index_search_recall(tmp_path: Path):
    m = MemoryStore(tmp_path / "mem.db")
    n = m.upsert_document(
        "adr-1",
        "# ADR 1\n\nUsamos tokens JWT com refresh para autenticação. Expiram em 15 minutos.",
        kind="adr",
        title="ADR-1 auth",
    )
    m.upsert_document(
        "adr-2",
        "# ADR 2\n\nO gateway de pagamento Stripe exige idempotency keys em toda cobrança.",
        kind="adr",
        title="ADR-2 payments",
    )
    m.upsert_document(
        "learn-1",
        "Bug: salvar fatura sem transação atômica duplicou registros. Sempre usar transação.",
        kind="learning",
        title="faturas",
    )
    assert n == 1 and m.stats() == {"documents": 3, "chunks": 3}
    assert (
        m.upsert_document(
            "adr-1",
            "# ADR 1\n\nUsamos tokens JWT com refresh para autenticação. Expiram em 15 minutos.",
            kind="adr",
            title="ADR-1 auth",
        )
        == 1
    )
    hits = m.search("como tratamos autenticação de tokens?", top_k=2)
    assert hits and hits[0].chunk.doc_id == "adr-1"
    hits = m.search("erros no gateway de pagamento", top_k=1)
    assert hits[0].chunk.doc_id == "adr-2"
    assert m.search("pagamento", kinds=("learning",)) and all(
        h.chunk.kind == "learning" for h in m.search("pagamento", kinds=("learning",))
    )
    block = m.recall("transação ao salvar fatura", top_k=1)
    assert block.startswith("## Precedentes") and "[learning] faturas" in block
    m.delete_document("adr-2")
    assert m.stats()["documents"] == 2
    assert m.recall("zzz qqq", top_k=1) != "" or True  # never raises on weak matches


def test_index_directory_and_persistence(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("regra de negócio: desconto máximo 20%")
    (docs / "b.txt").write_text("ignored")
    m = MemoryStore(tmp_path / "mem.db")
    assert m.index_directory(docs, kind="doc", relative_to=tmp_path) == 1
    m.close()
    again = MemoryStore(tmp_path / "mem.db")
    assert again.search("desconto")[0].chunk.doc_id == "docs/a.md"


def test_code_search_and_symbols(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "svc.py").write_text(
        'MAX_RETRIES = 3\n\n\nclass Billing:\n    """Cobrança."""\n\n    def charge(self, amount: float) -> bool:\n        return True\n\n\nasync def refund(order_id: str) -> None:\n    pass\n'
    )
    (src / "app.ts").write_text(
        "export async function fetchUser(id: string) {}\nexport class Store {}\nconst API_URL = 'x';\n"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("function fetchUser() {}")
    cs = CodeSearch(tmp_path)
    hits = cs.grep("fetchUser")
    assert [h.path for h in hits] == ["src/app.ts"] and hits[0].line == 1
    assert cs.grep("charge(", regex=False)[0].path == "src/svc.py"
    assert cs.grep("MAX_RETRIES", glob="*.ts") == []
    idx = SymbolIndex.build(tmp_path)
    names = {(s.kind, s.name) for s in idx.symbols}
    assert {
        ("class", "Billing"),
        ("method", "charge"),
        ("function", "refund"),
        ("const", "MAX_RETRIES"),
        ("function", "fetchUser"),
        ("class", "Store"),
        ("const", "API_URL"),
    } <= names
    charge = idx.find("charge")[0]
    assert charge.parent == "Billing" and charge.signature.startswith("def charge")
    assert [s.name for s in idx.outline("src/app.ts")] == ["fetchUser", "Store", "API_URL"]
    assert idx.find("fetch", exact=False)[0].name == "fetchUser"
