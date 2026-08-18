"""Registry of card scripts.

`registry()` maps card_id -> CardScript. A card with no entry has no scripted
behaviour; its printed text is inert and the UI says so.
"""

from __future__ import annotations

import functools

from cards.dsl import Ability, CardScript, TriggerKind
from cards.gear import equip_ability, quick_draw_ability
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


def activated_abilities(card) -> tuple[Ability, ...]:
    """Every activated ability on `card` (376), in a stable order.

    Scripted abilities come first, then the Equip ability derived from the
    card's printed keyword (818.1.c.2). Deriving it here rather than writing
    it per card is what makes 32 Equipment playable with no scripts at all.

    Equip is appended rather than prepended so a scripted ability keeps the
    index it already had -- recorded replays address abilities by index, and
    renumbering them would silently invalidate every one.
    """
    script = script_for(card.card_id)
    abilities = list(script.of_kind(TriggerKind.ACTIVATED)) if script else []
    equip = equip_ability(card)
    if equip is not None:
        abilities.append(equip)
    return tuple(abilities)


def abilities_of_kind(card, kind: TriggerKind) -> tuple[Ability, ...]:
    """Every ability of `kind` on `card`: scripted, plus any derived from a
    printed keyword. Quick-Draw (819.1.d) contributes an on-play Attach."""
    if kind is TriggerKind.ACTIVATED:
        return activated_abilities(card)
    script = script_for(card.card_id)
    abilities = list(script.of_kind(kind)) if script else []
    if kind is TriggerKind.ON_PLAY:
        derived = quick_draw_ability(card)
        if derived is not None:
            abilities.append(derived)
    return tuple(abilities)
