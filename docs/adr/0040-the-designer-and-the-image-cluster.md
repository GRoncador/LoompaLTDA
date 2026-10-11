# ADR-0040: The Designer Loompa and the Image cluster

Date: 2026-10-11 · Status: accepted

## Context

On 05/10 the founder asked for a graphic designer:

- logo, icons and illustrations for the products;
- sprites for the games;
- later, marketing pieces.

The plan (Phase 15, "Designer gráfico") placed it as the second role of a Design area beside the
UX: the UX decides flows and screens, and the Designer makes the pictures. The plan also set
three conditions:

- **a flat logo first**: drafts as flat images, and only the chosen one is vectorised (the
  founder's correction);
- **its own model cluster**: image models are billed per picture and rated in their own arena;
- **pixel art held in code**: models imitate the style without keeping the pixel grid.

What was checked on 11/10 against the supplier's own documentation and catalog:

- **The image API.** OpenRouter's image API is `POST /api/v1/images` with `model`, `prompt`, the
  model's own parameters and optional `input_references` (a list of
  `{"type": "image_url", "image_url": {"url": <data URL>}}`). It answers with
  `data[].b64_json`, `data[].media_type` and `usage.cost`.
- **The catalog.** `GET /api/v1/images/models` has 59 models, each with the parameters it
  accepts. Each model's `/endpoints` gives its price lines, either per picture or per output
  token.
- **The ratings.** Artificial Analysis has `GET /api/v2/data/media/text-to-image` and
  `/image-editing`. Both give the ELO per model and use the same key as the text ratings.

## Decision

### 1. The role (`agents/designer.py`, role `designer`, cluster `engineering`)

- **Tool profile.** It reads the repository and memory, and its writes go only to
  `.loompa/design/`. It is the only profile with `generate_image`, which
  `DesignerAgent.picture` checks on every call.
- **Prompts.** Three, product-agnostic, written for the bench:
  - logo prompts;
  - piece prompts;
  - review of pictures, with vision.
- **What it does itself.** It writes the image prompts and looks at the results. The pictures
  come from the Image cluster.

### 2. Pictures, metered (`llm/images.py`)

`OpenRouterImages` calls the API above. Every picture is recorded like a model call:

- the tier is `image:<slot>`;
- `usage.cost` is the cost;
- it counts in the story's cap and the period's budget.

There are two more limits:

- a story makes at most `design.images.per_story` pictures (24);
- generation stops when the budget is spent and set to pause.

Without an OpenRouter key, in the dry run and in tests, `PlaceholderImages` draws a flat local
picture, so the whole flow runs offline.

### 3. The Image cluster (`llm/image_catalog.py`, `design.images`)

| Slot | Default | Suggested by |
|---|---|---|
| draft | Seedream 5.0 Flash (US$ 0.018) | generation ELO per dollar |
| final | FLUX 3 (from US$ 0.041) | best generation ELO under `final_ceiling_usd` (0.15) |

- **Price.** The price per picture is the cheapest `output_image` line. A per-token line is
  multiplied by 1,290 tokens, the size of a 1k picture.
- **Ratings.** The arena rating is matched on the model's slug or name, letters and digits only.
- **What is left out.** A model with no price or no rating is left out of the suggestion. Vector
  models are left out of the ranking: vectorising is a step in code, and the vector model
  (`vector_model`, Recraft v4.1 Vector) is that step's reserve.
- **Where it shows.** (11/10, founder's correction) The Image cluster is a card of its own in Modelos e
  papéis, beside the general cluster when the split is off and beside the three clusters when it is
  on: image models never join the general text list. Its 💡 per slot shows the suggestion, and the
  Benchmark tab lists it after the text profiles (which follow the same switch). Opening a tab
  reads the saved list only; the refresh button goes to the network. The logo option stays in Fábrica.
- **Applying it.** The founder applies it
  (ADR-0011/0025), as with the text models.
- **Editing.** No editing slot yet: the logo's adjustment round uses the draft slot with the
  chosen draft as reference. The editing ELO is shown for when it is needed.

### 4. The logo, inside the visual base

It applies under `design.images.logo`:

- `auto`: commercial products only;
- `on` and `off` force it either way.

After the base is chosen, the story that owns the base runs the logo, and other stories stay
parked on the same key:

1. Two directions, four flat drafts each (solid colours, no gradients or shadows, plain
   background), from the draft slot.
2. The founder picks one, asks for an adjustment of one ("3, letras mais grossas": four edits with
   it as the reference), asks for other drafts, or goes without a logo. Prior-art search and the
   INPI registration stay with the founder; the message says so.
3. **Vector, in code.** vtracer (MIT, a core dependency) traces the chosen draft. The trace is
   accepted only when all three hold:
   - it is a valid SVG;
   - it has at most 300 paths;
   - the SVG, drawn back by the headless browser, differs from the draft in at most 6% of the
     shrunk picture.

   Otherwise the vector model redraws it, and the brand notes that the drawing was reinterpreted.
4. `design/brand/` gets `logo.svg`, PNGs at 16-512 px, `favicon.ico`, and `logo.json` with the
   prompt, model and seed. The company's `brand.md` gets a "Logo" section.

### 5. Pieces at the design gate

The UX lists the pictures its screens need (`pieces`):

- size;
- format;
- background;
- pixel art and grid;
- the `target` path in the product.

It shows each one in the HTML as `pieces/<name>.png`. For each piece, the Designer:

1. makes two drafts (with the brand as reference);
2. picks the best with vision, or takes the first;
3. finishes it in the final slot, with that draft and the brand as references.

Code then fits it to its size and format. A pixel-art piece is shrunk to its grid (nearest
neighbour), held to the base's palette and grown back by whole pixels. Code also checks:

- the size;
- the transparency, where the background must be clear;
- the weight (600 kB);
- the SVG, when the piece is one.

The pieces go to the founder in the same approval as the screens. They are made once per founder
round: internal retries reuse them, and an adjustment makes them again.

When the story reaches `dev`, the approved pieces are copied to their targets in the worktree and
committed before the Worker runs. The Worker never generates a picture. A target outside the
repository, or under `.loompa/`, is dropped.

## Consequences

- **Cost.** A commercial product's first screen story spends about US$ 0.15-0.30 on the logo
  (8 drafts, any adjustment, a rare vector redraw). Each piece costs about US$ 0.08: two drafts and
  one final.
- **Without a vision model.** The Designer cannot pick or review. The first draft is finished,
  and the founder's approval is the review.
- **Pictures as references.** They reach image models only. A text model never receives them
  unless `models.vision` is set (ADR-0039).

## Not done, on purpose

- **An editing slot ranked by the editing ELO.** It waits until an adjustment round asks for it.
- **The marketing pieces.** They are Phase 17, which reuses `DesignerAgent.picture` with the brand
  as reference.
- **Video.** It stays on the plan's "Depois" list.
