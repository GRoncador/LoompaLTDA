# ADR-0045: One Analyst for specs and research, two tasks underneath

Date: 2026-10-12 · Status: accepted

## Context

On 12/10 Gustavo saw three figures in the office for two jobs: "Product", "Spec" and "Analyst".
"Product" and "Spec" were the same agent. The dashboard seeded a fixed "Product Loompa" row
(`dashboard/app.py`, `DEFAULT_AGENTS`), while the spec writer (`agents/product.py`, role `product`)
recorded its state as "Spec Loompa". Each factory's `state.db` therefore held two names for the
same role. Other screens used a third and fourth label: "Spec" in costs and "Spec Loompa (BDD)" in
settings.

He also proposed folding the spec writer into the Analyst. In an agile team the business analyst
researches, gathers requirements and writes acceptance criteria, and the Product Owner reviews and
decides. The factory already splits the work that way: the spec writer writes and the Product
Owner reviews it with the "No Invention" gate (ADR-0006).

## Decision

1. **The founder sees one Loompa, the Analyst Loompa.** It writes specs and does research. The
   spec writer's `display` is "Analyst Loompa", the dashboard seeds only that name, and the
   office, cards, costs and settings label both tasks as the Analyst. Costs and settings name the
   task: "Analyst (spec)" and "Analyst (pesquisa)".
2. **Underneath there are still two roles, `product` (spec) and `analyst` (research).** Each keeps
   its own tool profile (ADR-0009), model tier (ADR-0024), prompt and bench (ADR-0034/0035). Writing
   a spec gets no web on purpose. A spec comes from the request, the constitution and earlier
   specs, and web search during writing invites invented scope. Research gets the web through MCP.
   The `product` role id stays because factory configs, model matrices and A/B arms are keyed by
   it.
3. **The spec prompt keeps "You are the Spec Loompa".** It is the measured text (14.3), and the
   model needs to know the task, not the persona.
4. **Older databases are migrated on open** (`Store.RENAMED_AGENTS`). The "Spec Loompa" and
   "Product Loompa" rows leave `agents`, and their `usage` rows move to "Analyst Loompa". The
   `role` column keeps spec and research spending apart.

## Consequences

- The `agents` table keeps one row per name, so the Analyst's row shows whichever task wrote last.
  If a spec and a research story run at once, the office shows one of them. The Worker already
  has the same limit (one row for parallel workers), and cards find their holder from events.
- Older trace files and event logs still say "Spec Loompa". They are history and stay as written.
