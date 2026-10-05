"""How well organizational memory finds the right document (plan 13.1).

Indexes a factory's `.loompa/` record (constitution, learnings, ADRs, specs) and README into a
throw-away database with each embedder, asks questions whose right document is known, and counts
how often that document is among the first five hits. The questions paraphrase the documents on
purpose, in pt-BR and English like the agents' queries: a word match is not what is measured.

    uv run python scripts/memory_recall_eval.py "/Users/gustavo/Projects/Tamagotchi retrô" \
        hash sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from loompa.memory import MemoryStore
from loompa.memory.embedders import FastEmbedEmbedder, HashEmbedder

# tamagotchi-retro, 2026-10-05: question -> the doc_id prefix that answers it
QUESTIONS: list[tuple[str, str]] = [
    ("como o jogo toca sons sem arquivos de áudio?", "specs/S-004/"),
    ("o que acontece se o jogo estiver aberto em duas janelas ao mesmo tempo?", "specs/S-005/"),
    ("por que a tela usa um canvas com pixels grandes?", "decisions/0001"),
    ("qual linguagem e onde roda o produto?", "decisions/0002"),
    ("o pet envelhece e muda de fase: bebê, criança, adulto", "specs/S-036/"),
    (
        "quanto tempo passou com o app fechado e como o estado é atualizado ao reabrir",
        "specs/S-007/",
    ),
    ("valores inválidos no estado salvo, como fome acima de 100", "specs/S-008/"),
    ("a tela parou de atualizar porque a comparação de estados achava tudo igual", "specs/S-011/"),
    ("página principal que junta a simulação, a tela e o salvamento", "specs/S-015/"),
    ("tirar o código antigo em Python depois de portar para JavaScript", "specs/S-018/"),
    ("dar comida, colocar para dormir e brincar com o bichinho", "specs/S-022/"),
    ("um pet recém-nascido deve começar com tudo cheio", "specs/S-023/"),
    ("o que acontece quando uma necessidade zera", "specs/S-025/"),
    ("botão para começar uma partida nova apagando o progresso", "specs/S-026/"),
    ("o desenho do bichinho ficou pequeno demais, aumentar", "specs/S-048/"),
    ("carcaça em formato de ovo em volta da tela", "specs/S-054/"),
    ("botões com cara de botão de plástico do brinquedo", "specs/S-055/"),
    ("layout num celular estreito sem rolagem para o lado", "specs/S-056/"),
    ("feedback visual rápido quando o jogador aperta um botão", "specs/S-057/"),
    ("the face of the baby stage had a stray L-shaped stroke", "specs/S-053/"),
]


def recall(root: Path, embedder, questions=QUESTIONS, k: int = 5) -> tuple[int, list[str]]:
    loompa = root / ".loompa"
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryStore(Path(tmp) / "m.db", embedder)
        mem.index_file(loompa / "constitution.md", kind="constitution", doc_id="constitution.md")
        mem.index_file(loompa / "learnings.md", kind="learning", doc_id="learnings.md")
        mem.index_directory(loompa / "decisions", kind="adr", relative_to=loompa)
        mem.index_directory(loompa / "specs", kind="spec", relative_to=loompa)
        mem.index_file(root / "README.md", kind="doc", doc_id="README.md")
        missed = []
        found = 0
        for q, want in questions:
            hits = [h.chunk.doc_id for h in mem.search(q, top_k=k)]
            if any(h.startswith(want) for h in hits):
                found += 1
            else:
                missed.append(f"{q} -> {want} (got {hits[:3]})")
        mem.close()
    return found, missed


def main() -> None:
    root = Path(sys.argv[1])
    for name in sys.argv[2:] or ["hash"]:
        t = time.monotonic()
        emb = HashEmbedder() if name == "hash" else FastEmbedEmbedder(name)
        found, missed = recall(root, emb)
        print(f"{name}: {found}/{len(QUESTIONS)} in the top 5 ({time.monotonic() - t:.0f} s)")
        for m in missed:
            print("   miss:", m)


if __name__ == "__main__":
    main()
