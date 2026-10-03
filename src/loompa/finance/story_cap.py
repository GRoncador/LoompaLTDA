"""The spending cap of one story.

Cost review of 2026-10-03: one cancelled story (Tamagotchi S-006) cost 17% of the week. A story
that spends this much without finishing usually has the wrong approach, so it pauses and the
founder decides whether it deserves more. Continuing grants another cap from where it stopped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext
    from loompa.engine.state import StoryState

# The story's cost when the founder last let it go on (or 0): the cap counts from there.
CAP_BASE_KEY = "cost_cap_base"


def story_spent(ctx: EngineContext, story_id: str) -> float:
    row = ctx.store.get_story(story_id) or {}
    return float(row.get("cost_usd") or 0.0)


def over_cap(ctx: EngineContext, state: StoryState) -> float | None:
    """What the story has spent, when that is past its cap; None while it is under (or the cap
    is off)."""
    cap = ctx.config.budget.story_cap_usd
    if cap <= 0:
        return None
    spent = story_spent(ctx, state.story_id)
    base = float(state.extra.get(CAP_BASE_KEY) or 0.0)
    return spent if spent - base >= cap else None
