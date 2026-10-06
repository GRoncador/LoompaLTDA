# ADR-0032: A decision model for narrow typed questions, in shadow first

Date: 2026-10-05 · Status: accepted (shadow); the move to `decide` is the founder's

## Context

TypeSafe's Jev 1.13 does not write text. It takes a state and typed questions, and answers each
with a calibrated confidence:

- `noul`: true/false;
- `choice`: one of named options;
- `score`: one of ordered levels.

It costs US$ 0.042 per million input tokens, the output is free, and a call takes about 0.1 s.
OpenRouter serves it at `POST /api/alpha/decisions`, with the key and credits the factory already
uses. `chat/completions` refuses it.

Its own documentation lists its weak points:

- it reads literally;
- it gets arithmetic, counts and dates wrong;
- it is lost with several steps of reasoning;
- it gets worse with a large, noisy state.

The Master and the PO together are 4-6% of a factory's cost, and almost every one of their calls
also writes text, so replacing them saves cents and is out of scope.

## Decisions

1. **One small interface** (`loompa/decider.py`). `Decider.decide(state, questions)` returns
   typed answers, or None.
   - `JevDecider` calls the endpoint and meters the call (role `decider`, the cost OpenRouter
     reports).
   - The dry-run gets `FixedDecider`, whose answers carry no confidence.
   - The tests script their own decider.
   - A failure (no key, a timeout, a changed shape of the `alpha` endpoint) answers None, and
     the caller keeps its current path, never a guess.
   - The dated model behind `typesafe/jev-1.13` is kept in the kv store. When it changes, the
     founder hears it once, the same way `AliasWatch` reports a moved alias.
2. **Three modes**, set in `decider.mode`:
   - `off` is the default: the decider is never called.
   - `shadow`: it answers beside the current path, and only the trace sees it
     (`decider.shadow`).
   - `decide`: answers at or above `threshold` (0.9) stand; the rest stay with the LLM.

   Only the founder moves a factory between modes.
3. **Where it is used.** Each use is a short state and a plain question:
   - **Research.** For each sentence and source the Analyst cites, it asks whether the source's
     text supports the sentence (`noul`). The text is what the web tool returned around that
     URL. In `decide` mode, a source confidently judged not to support its sentence is dropped,
     like a URL the run never saw.
   - **Memory.** It gives a usefulness `score` to each passage pushed into a prompt. This is
     shadow only for now: dropping a passage needs the answer before the prompt is built, which
     waits for the founder's verdict on the shadow sprint.
   - **Later (Fase 15).** "Does this story touch the interface?" will decide the design gate.
4. **Where it is not used.**
   - **Duplicate findings.** Plan 12.2 found the repeated finding was code (ffce921).
   - **Inspector, Worker, Architect, Spec.** They write or need long reasoning.
   - **Screenshots.** It reads text only.
   - **`typesafe/jev-router`.** It would choose models and effort over the house score and the
     lists the founder approved (ADR-0011, 0024).

## Measured offline

`scripts/decider_offline_eval.py` asked "is this finding the same problem as that card?" of 176
finding/card pairs from both factories, costing about US$ 0.004. The labels came from history:
"duplicate" when the finding was linked to an older card, otherwise the new card against its two
most similar older cards.

| Confidence | Pairs | Agreement with the label |
|---|---|---|
| 0.5-0.7 | 19 | 63% |
| 0.7-0.9 | 40 | 78% |
| 0.9-1.0 | 117 | 81% |

The labels turned out to be the weak side. Every high-confidence disagreement checked by hand was
a wrong link in the history, not a Jev error. Examples:

- `uv.lock` was linked to `debug.txt`;
- five different findings were folded into S-010;
- S-060's duplicated loop was folded into S-059's import cycle.

The cause was `Backlog.find_duplicate`'s same-file rule. It overrode the PO after the PO had read
a finding and called it new. That rule now applies only to findings nobody read (83ca2ca). The
same check limited to the PO's triage era (61 pairs) gave 82% at ≥ 0.9. The pt-BR sample is
now large enough to say Jev reads Portuguese well at this task. Its weak recall on the history's
"duplicates" is the history's fault.

## Consequences

- Turning it on is one line per factory (`decider.mode: shadow`), and it costs fractions of a
  cent per sprint.
- At the end of a shadow sprint, the `decider.shadow` events are what the founder decides on:
  how often a dropped citation or passage would have been right.
- The supplier was 17 days old when this was written, its limits "change without notice", and
  the endpoint is `alpha`. Every path falls back to the LLM, and none depends on it.

## Measured against recorded decisions (2026-10-05, later)

The founder asked to replay recorded decisions rather than wait for shadow data.

### Memory filter (first shape — superseded, see "Revised to TypeSafe's documented shapes")

`scripts/decider_memory_eval.py` used the 20 questions with a known right document from ADR-0031.
The top six passages of each question were scored for usefulness: 51 passages from the right
document and 69 from others.

- **As a filter, it does not work.** It gave "useful" to 49 of 51 right passages and to 50 of 69
  wrong ones. At confidence ≥ 0.7 it would drop nothing.
- **As a re-ranker, it does.** Within each question, P(useful) puts a right passage above a wrong
  one in 69% of pairs. The search's own order inside those six does it in 49% of pairs.
- **Proposal:** replace "drop" with "re-order and keep the best", and measure it in shadow.

These numbers came from a shape TypeSafe's documentation advises against: all six passages in one
state, and a score per "passage [n]". The revision below measured the same 20 questions again.

### Research claims and process-only findings

- **Research claims:** no usable labels. The past reports cite repository files, not web pages, and
  the PO's review kept only counts.
- **Process-only findings:** only two positive verdicts on record, too few to measure.

### In shadow from 2026-10-05 (evening), decided at plan 14.5

- **Precedents:** filter and re-order, with the order P(useful) would give in `reranked`.
- **Research claims:** sentence against source.
- **Kaizen triage:** process only, and duplicate of which open card. The state is the finding and
  the eight nearest open card titles by the memory's embedder; the PO's verdict is recorded beside.

None of these changes a prompt, so the providers' prompt cache is unaffected.

In `decide` mode, a re-ordered precedents block would change only that block. The block sits at the
end of the first user message, after the stable part (role and constitution), and already varies
with each request. It is decided once per call, so a tool loop re-sends it unchanged.

## Revised to TypeSafe's documented shapes (2026-10-05, night)

The founder asked whether the shadow followed the supplier's documentation. It did not in two of the
three uses. Checked against docs.typesafe.ai (Jev 1.13 jaggedness, State, Confidence, and the
re-ranking, RAG-passage and citation-check cookbooks):

- **Memory** put every passage in one state and asked about "passage [3]". The jaggedness page
  names both: indirection, and a large state full of irrelevant detail. The cookbooks send one
  request per query–passage pair.
- **Kaizen duplicates** asked one choice among the eight nearest cards, nearest first. The
  jaggedness page says Jev 1.13 leans toward the first option, which here was the nearest card. It
  was also not the shape measured offline (one yes/no per pair).
- **Research claims** sent up to 3,000 raw characters of the page and asked yes/no. The citation
  cookbook locates the section in code, then asks a choice: supports, contradicts, says nothing.
- The states were text with headings. The docs recommend a JSON object with named fields, named in
  the questions in backticks.
- A noul's confidence was `max(p, 1 - p)`. TypeSafe's formula is `|2p - 1|`, on the same scale as a
  choice's. A ranking reads the probability, never a threshold.
- "Process only" hid two judgments and a negation in one question. It is now two literal yes/no
  questions, combined in code.

All questions and thresholds now live in one block of `loompa/decider.py`, as the docs advise.

### Measured again

**Memory** (`scripts/decider_memory_eval.py`): the same 20 pt-BR questions, top 6. That makes 120
passages, 43 of them from the right document. The mix differs from the first run because of 13.8
(two chunks per document at most). Each passage got its own request with two yes/no questions.

| Order by | Right passage above a wrong one |
|---|---|
| The search's own order | 60% |
| P(applies) | 73% |
| P(relevant) | **95%** |

- **Keep only the best one:** the right document survives in 15 of 20 questions by P(relevant),
  against 8 by the search.
- **As a filter, it now works.** Dropping passages with P(relevant) < 0.3 loses none of the 43
  right ones, and drops 24 of the 77 wrong ones.
- So the shadow ranks by P(relevant), and `RELEVANT_MIN` is 0.3. That threshold is tuned on only
  20 questions, so the shadow sprint is what confirms it.
- The first shape's result ("does not work as a filter") was the shape's fault, not Jev's.

**Duplicates** (`scripts/decider_offline_eval.py`, now with a JSON state): 178 pairs, 78%
agreement, 82% at confidence ≥ 0.8. That is the same as before, as expected: that check already
used one pair per request. The shadow now asks the same shape, so it measures what was validated.

**Research claims:** still no labels on record. The new shape waits for a research story in shadow.
