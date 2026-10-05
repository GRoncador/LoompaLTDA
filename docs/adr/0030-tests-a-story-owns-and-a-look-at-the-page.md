# ADR-0030: Tests a story owns, in proportion, and a look at the page

Date: 2026-10-05 · Status: accepted

## Context

Tamagotchi SP-008 produced about 430 lines of product and 3,130 lines of tests (7.3x):

| Story | Lines of product | Lines of test | Note |
|---|---|---|---|
| S-053 | 20 | 689 | |
| S-055 | 39 of CSS and HTML | 378 | |
| S-056 | 53 of CSS | 409 | took 65 minutes; its replies hit the token ceiling twice, growing it 16k → 32k → 65k |

Three costs came out of that:

1. **Time.** The Worker is the bottleneck, and most of its output was tests.
2. **Merge conflicts.** Each story appended a `describe` block to the end of the module's test
   file. `tests/casca.test.js` and `tests/sprites.test.js` (2,100 lines) took one from four
   stories, so any two stories running in parallel conflicted at merge (ADR-0029). The Worker
   also re-reads such a file whole on every task.
3. **Missed visual defects.** The tests that covered how the page looks parsed `estilo.css` with
   regular expressions. The founder still caught every visual defect:
   - S-054's screen crossing the egg-shaped shell;
   - S-057's feedback outside the LCD;
   - S-048 clipped, off-centre, then glued to the bottom.

   The smoke opened the page in Chrome but only checked that it drew something.

## Decisions

### 1. A story's new tests go in a test file of its own

Both plan prompts (`SYSTEM`, `SIMPLE_SYSTEM`) say so. New tests go in a new file next to the
existing ones, named after what it tests and so the runner finds it. An existing file changes only
where its own tests change.

`plan_check.tests_appended_to_long_files` makes this a plan problem when a task adds tests to a
test file longer than 400 lines. Only a clause that adds without also saying it changes counts, so
"ajustar o teste do critério 3" there is fine. The plan is then rewritten once, at no model cost,
like the other plan problems (ADR-0026). The check now runs against the story's worktree rather
than the main checkout: a story built on an unmerged delivery plans against that delivery's files
(ADR-0027).

### 2. Tests in proportion to the change: behaviour by risk, never the implementation

- **Prompts.** The plan prompts and the Worker's prompt ask for tests of behaviour, chosen by
  risk:
  - **In:** each acceptance criterion, plus the limits and error paths of the code the story
    changes (empty, zero, the maximum and one past it, invalid input). Edge cases are where bugs
    live: `contas` lost rounds to an OFX code read wrongly and to "12.50" with a point.
  - **Out:** the same path repeated with other values; an implementation detail pinned (an exact
    pixel, the stylesheet's text), which Google's testing guidance calls a change-detector test;
    and a copy of the current code's data as the expected value (S-053 embedded the whole "before"
    sprite grids). These break on the next legitimate change and catch no bug: the tests pinning
    the pet's pixels broke when S-048 resized it, and left an obsolete-comment card (S-051) behind.

  How the page looks is left to the browser smoke (decision 3).

  *Revised the same day.* The first wording said "an edge case only where the criterion names
  it". That threw away the tests that find bugs. The SP-008 bloat was never edge cases; it was the
  three kinds above.
- **Finding.** `hygiene.test_ratio` adds a low-severity finding when the tests added pass 150 lines
  and 5 times the product lines. Its text points at the same distinction: behaviour and its limits,
  not the same path again or implementation details. Product counts CSS, HTML, Vue and Svelte too, since a web story's
  whole change can be style. Low severity makes the verdict CONCERNS but files no Kaizen card: it
  is a signal for the sprint, not a reason to fail a delivery.

### 3. The smoke looks at the page at two widths and the founder gets the screenshots

**Viewports.** After the existing checks, `smoke.py` lays the page out at 1280 px and at 390 px
(phone emulation) and runs `LAYOUT_PROBE`. It finds two kinds of problem:

- **Sideways scroll.** The content is wider than the screen. The probe compares against
  `documentElement.clientWidth`, because under phone emulation `innerWidth` grows to fit the
  overflow: 857 px for a 390 px screen.
- **Sticking out.** A canvas, image, video, svg or form control sticks out of the nearest ancestor
  that draws a box (a background, a border, or clipped overflow). It counts when its rectangle
  passes the box's, or when one of its corners falls outside the box's rounded shape (radii read
  from CSS, percentages resolved, scaled as CSS scales them). The second test is what catches
  S-054: the rectangles fit, only the egg's curve was crossed.

**Validated on the real branches before wiring it in:**

| Version | Desktop | 390 px |
|---|---|---|
| S-054, first delivery (sent back by the founder) | screen crosses the shell | scrolls sideways, screen crosses the shell |
| S-054, approved version | clean | scrolls sideways (as main already does) |
| S-056 | clean | clean |
| main | clean | scrolls sideways |

**Baseline.** A layout problem the base already has is not the story's.
`InspectorAgent._layout_baseline` runs the smoke once on a checkout of the base and keeps the
problem keys in the story's baseline. A key leaves the pixels out (`mobile:#tela-lcd>#carca-ovo`),
so the same problem is recognised on both sides. Like the tests' baseline, it is measured again
when the base moves. A base that cannot be rendered counts as having no problems.

New layout problems fail the smoke, like the canvas-edge rule. They are the defects the founder
was finding by eye.

**Screenshots.** Each width is saved as a PNG (cut at 2,400 px tall) under
`~/.loompa/shots/<factory>/<story>/`, which is the hub, never the product repository.
`FounderMessage.images` lists them on the delivery, and the context says what they are. The
dashboard serves only those two names (`/api/factories/{slug}/stories/{id}/shots/{name}`) and the
inbox shows them as thumbnails. The Deployer's summary prompt now says screenshots go with the
message, while still forbidding claims about appearance.

## Consequences

- Each smoke run costs about 1.5 s more for the two layouts and screenshots, plus one base smoke
  per baseline.
- The sticking-out rule can flag a deliberate overlap, such as a badge placed over a box's edge. It
  only looks at canvases, media and controls, and only at their nearest box. If it misfires, the
  answer is a narrower selector, not removing the rule.
- Not done: a vision model reading the screenshot against the criteria. Geometry covers the defects
  seen so far, and those models misjudge fine positions. The founder sees the images either way.
