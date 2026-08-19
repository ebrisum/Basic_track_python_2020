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


# Same reasoning as cards/gear.py: this is a pure function of the card face
# and legal-action generation calls it for every card on the board, every ply.
_ACTIVATED_CACHE: dict[tuple, tuple] = {}


def is_equipment(card) -> bool:
    """Whether this card carries an Equip ability, i.e. is an Equipment."""
    return equip_ability(card) is not None


def _split_text(card) -> tuple[tuple[Ability, ...], tuple[Ability, ...]]:
    """(rules_text_abilities, effect_text_abilities) for one card.

    136.1/136.2 divide a card's printed text in two, and 718.2 / 724 make
    exactly one half live at a time depending on whether the card is
    Attached. The scraped `rules_text` merges both halves into one string, so
    the engine cannot read the division off the card.

    **Approximation, and a real one** (RQ-18): for an Equipment, the derived
    Equip and Quick-Draw abilities are treated as Rules Text and every
    scripted ability as Effect Text. That matches how Equipment are laid out
    -- the Equip line above, the worn effect in the box below with the Might
    Bonus -- but it would misfile a printed activated ability meant to work
    while the gear is loose. No card in the scripted pool has one.

    Every non-Equipment card is all Rules Text: 723 says Rules Text is never
    Inactive by default, and 724 makes Effect Text moot for a card that
    cannot be attached.
    """
    script = script_for(card.card_id)
    scripted = tuple(script.abilities) if script else ()
    derived = tuple(a for a in (equip_ability(card), quick_draw_ability(card))
                    if a is not None)
    if not derived:
        return scripted, ()
    return derived, scripted


def abilities_for(card, attached: bool) -> tuple[Ability, ...]:
    """The abilities that are *live* on this card right now.

    718.2 -- attached: printed Rules Text is Inactive.
    718.3 / 724 -- attached: Effect Text is live and appended to the host.
    723 -- unattached: Rules Text is live, Effect Text is not.

    722 is the reason this filters abilities and nothing else: Inactive text
    is still *present*, so keywords, types and the Might Bonus are untouched.
    """
    rules, effect = _split_text(card)
    return effect if attached else rules


def activated_abilities(card) -> tuple[Ability, ...]:
    """Every activated ability on `card` (376), in a stable order.

    Scripted abilities come first, then the Equip ability derived from the
    card's printed keyword (818.1.c.2). Deriving it here rather than writing
    it per card is what makes 32 Equipment playable with no scripts at all.

    Equip is appended rather than prepended so a scripted ability keeps the
    index it already had -- recorded replays address abilities by index, and
    renumbering them would silently invalidate every one.
    """
    key = (card.card_id, card.rules_text, tuple(card.keywords or ()))
    cached = _ACTIVATED_CACHE.get(key)
    if cached is not None:
        return cached
    script = script_for(card.card_id)
    abilities = list(script.of_kind(TriggerKind.ACTIVATED)) if script else []
    equip = equip_ability(card)
    if equip is not None:
        abilities.append(equip)
    result = tuple(abilities)
    _ACTIVATED_CACHE[key] = result
    return result


def abilities_of_kind(card, kind: TriggerKind,
                      attached: bool = False) -> tuple[Ability, ...]:
    """Every *live* ability of `kind` on `card`.

    `attached` selects which half of the card's text is active: Rules Text
    when loose (723), Effect Text when worn (718.2 / 718.3 / 724). See
    `abilities_for`.
    """
    if kind is TriggerKind.ACTIVATED:
        live = set(activatable_indices(card, attached))
        return tuple(a for i, a in enumerate(activated_abilities(card))
                     if i in live)
    return tuple(a for a in abilities_for(card, attached) if a.kind is kind)


def activatable_indices(card, attached: bool) -> tuple[int, ...]:
    """Indices into `activated_abilities(card)` that can be activated now.

    The *list* stays whole and stably ordered whatever the card's state --
    recorded replays address abilities by index, so filtering the list itself
    would silently renumber every one. What changes is which indices are live.
    """
    live = {id(a) for a in abilities_for(card, attached)}
    return tuple(i for i, a in enumerate(activated_abilities(card))
                 if id(a) in live)
