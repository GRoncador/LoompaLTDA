# ADR-0031: The company's knowledge base — real embeddings, hybrid recall, documents read whole

Date: 2026-10-05 · Status: accepted · Amends ADR-0003

## Context

ADR-0003 gave each factory a local RAG (`.loompa/memory.db`), pushed into the prompts of the
Spec, Architect, Product Owner, Master, Analyst and support roles by `precedents()`. On
2026-10-05 it held 808 chunks in `contas` and 1,305 in `tamagotchi-retro`, and it was weaker
than it looked:

- `fastembed` was installed neither in the repository's environment nor in the `uv tool`
  install, so every factory ran on the hashed n-gram embedder, which matches letters, not
  meaning. Nothing said so.
- The configured model, `BAAI/bge-small-en-v1.5`, is English-only, and the founder's documents
  are in pt-BR.
- Memory only pushed: no role could look something up.
- Sprint reports, the founder's answers in the inbox and the deliveries were never indexed.
- The documents a company runs on (business model, ideal customer, positioning, brand voice,
  pricing, glossary) did not exist anywhere.

## Decisions

1. **Real, multilingual embeddings by default.** `fastembed` is a core dependency. The default
   model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (220 MB, 384
   dimensions). It is kept in `~/.loompa/models`; fastembed's default cache is the system temp
   folder, which the OS empties.
   - Configurations still naming `bge-small-en` drop it (config revision 5).
   - Models trained with prefixes get `query:` / `passage:` through fastembed's
     `query_embed` / `passage_embed`.
   - `loompa doctor` says when the model does not load, and why. Memory stays best-effort and
     falls back to hashed n-grams.
   - The test suite never downloads a model (`LOOMPA_MEMORY_HASH_ONLY`).
2. **Hybrid recall.** A BM25 ranking (SQLite FTS5, `unicode61 remove_diacritics`) runs over the
   same chunks as the vectors. The two rankings are fused by reciprocal rank (k = 60, 50
   candidates each).
   - The reported score stays the cosine similarity.
   - The FTS query is built from the query's words, each one quoted, so FTS syntax never comes
     from the input.
   - An older memory gets its lexical index when it is opened.
3. **Company documents, read whole** (`loompa/company.py`). Six documents live in
   `.loompa/company/`: `business.md` (Lean Canvas), `customer.md`, `positioning.md`,
   `brand.md`, `pricing.md` and `glossary.md`.
   - `init` writes pt-BR templates, and the engine writes them for older factories. A file
     that already exists is never overwritten.
   - A file still equal to its template is never sent.
   - Which role reads which document whole is declared in code (`READERS`). For example, the
     PO reads business, customer and glossary; the Architect only the glossary.
   - These documents go after the constitution excerpt (`grounding()`) as data, not
     instructions. A chunk cut by similarity would lose what they mean.
   - Only the founder edits them. No agent tool writes under `.loompa/`.
4. **More sources** (`memory/sources.py`). The index now also holds:
   - sprint reports, as `report`;
   - every answered question or block, as `decision`: the question, the options, the founder's
     choice and words, and what happened to each card;
   - every answered delivery, as `delivery`.

   An answer is indexed when it is given, not only at the next `loompa run`.
5. **`search_memory`, per role** (ADR-0009). The roles that read before they answer can search
   memory, within the kinds of document their profile reads (`ToolProfile.memory_kinds`):
   - the Worker: what concerns code (constitution, learnings, ADRs, specs, docs, glossary);
   - the Architect: also reports, decisions and deliveries;
   - the business roles: also the company's documents and research.

   A kind outside the profile is never searched, and results are marked as data.
6. **Client contracts are restricted** (LGPD). Files in `.loompa/company/contracts/` are
   indexed as `contract`.
   - Only Compliance and the product manager read that kind.
   - The precedents pushed to any other role exclude it.
   - The folder is in the factory's `.gitignore`.

## Measured

`scripts/memory_recall_eval.py` asks 20 questions about tamagotchi-retro's record. The questions
paraphrase the documents, in pt-BR plus one in English, and each has a known right document. The
table counts how often that document is among the first five hits:

| Embedder | Vectors only | With BM25 |
|---|---|---|
| hashed n-grams | 11 | 14 |
| MiniLM-L12 multilingual (default) | 12 | **16** |
| potion-multilingual-128M | 13 | 15 |
| mpnet-base multilingual (1 GB) | 15 | 17 |

`mpnet` gains one question for five times the indexing time (about 5 minutes for the whole record)
and 1 GB on disk. `multilingual-e5-large` did not load under the current onnxruntime: the external
data path check fails in fastembed's cache layout.

## Consequences

- The first `loompa run` or `loompa doctor` downloads about 220 MB once. A factory reindexes
  everything on its first run with the new embedder, because the content hash includes the
  embedder's name.
- An installed `uv tool` needs `uv tool upgrade loompa-core` to get `fastembed`.
- Filling `.loompa/company/` is the founder's job. Until then, nothing changes in any prompt.
