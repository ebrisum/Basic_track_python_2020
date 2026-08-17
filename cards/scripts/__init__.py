"""Registry of card scripts.

`registry()` maps card_id -> CardScript. A card with no entry has no scripted
behaviour; its printed text is inert and the UI says so.
"""

from __future__ import annotations

import functools

from cards.dsl import CardScript
from cards.scripts.origins import SCRIPTS as ORIGINS_SCRIPTS

ALL_SCRIPTS: tuple[CardScript, ...] = ORIGINS_SCRIPTS


@functools.lru_cache(maxsize=1)
def registry() -> dict[str, CardScript]:
    out: dict[str, CardScript] = {}
    for script in ALL_SCRIPTS:
        if script.card_id in out:
            raise ValueError(f"duplicate script for {script.card_id}")
        out[script.card_id] = script
    return out


def script_for(card_id: str) -> CardScript | None:
    return registry().get(card_id)
