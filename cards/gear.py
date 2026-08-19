"""Equipment: what gear can be equipped, at what cost, for what Might Bonus.

Gear splits in two (150). Gear carrying the **Equipment** tag are Equipment
(150.1) and can be attached to a unit; the rest -- most of the pool -- are
base gear that sit in the Base and do their own thing, and can never be
attached. `equipment_profile` returns None for those rather than an empty
profile, so "cannot be equipped" is a distinguishable answer.

Equip is not a special case in the engine. 818.1.c.2 says "Equip [Cost]" is
functionally short for "[Cost]: Attach this gear to a unit you control", so
this module *derives* an ordinary activated `Ability` from the printed text.
That keeps the no-per-card-Python rule: 36 Equipment cards become playable
from their own card face, with no scripts.

## Why the reminder text and not the keyword

The `[EQUIP ...]` marker is printed at least six different ways across sets --
`[EQUIP Fury]`, `[EQUIP 1, Fury]`, `[EQUIP1, Calm]`, `[EQUIP 1 Body]`,
`[Equip] 1 calm rune`, and bare `Equip 1 body rune`. The reminder text that
follows it is not: every Equipment prints
`(<cost>: Attach this to a unit you control.)`, so the cost is parsed from
there. Reminder text is normally stripped rather than interpreted (DSL.md);
this is the documented exception, and it is safe because 818.1.c.2 defines
Equip *by* that expansion -- the reminder is the rule, quoted.

Costs that are not pure resources print `(Pay the cost: ...)` instead, which
carries no cost at all. Those are reported as unparsed with a note rather
than guessed at; `equip_ability` returns None and the card stays inert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cards.dsl import (
    Ability,
    Attach,
    EquipToMe,
    PayEnergy,
    PayPower,
    Selector,
    TriggerKind,
)
from engine.zones import DOMAINS

EQUIPMENT_TAG = "equipment"

# Sentinel so a cached None is distinguishable from a cache miss.
_MISSING = object()

# `(<cost>: Attach this to a unit you control.)` -- 818.1.c.2 quoted on the
# card. The cost is everything before the colon.
_REMINDER = re.compile(
    r"\(\s*(?P<cost>[^):]*?)\s*:\s*Attach\s+this\s+to\s+a\s+unit\s+you\s+control",
    re.IGNORECASE,
)
# The reminder printed when the cost is not purely resources (SFD-150,
# SFD-178, UNL-158). It states no cost, so nothing can be derived from it.
_OPAQUE_COST = re.compile(r"^\s*pay\s+the\s+cost\s*$", re.IGNORECASE)

_ENERGY = re.compile(r"(\d+)\s*energy", re.IGNORECASE)
_LEADING_NUMBER = re.compile(r"^\s*(\d+)\b")
_DOMAIN = re.compile(r"\b(" + "|".join(DOMAINS) + r")\b", re.IGNORECASE)
_ANY_RUNE = re.compile(r"rune\s+of\s+any\s+type", re.IGNORECASE)

# 137.1 -- the Might Bonus prints in the lower right corner, so it is the last
# one on the card. Reminder text quotes other +N Might values (ASSAULT 2 reads
# "+2 Might while I'm attacking"), so reminders are removed before matching.
_MIGHT_BONUS = re.compile(r"(?:Might\s*([+-]\d+)|([+-]\d+)\s*Might)", re.IGNORECASE)
_REMINDER_SPAN = re.compile(r"\([^)]*\)")

_QUICK_DRAW = re.compile(r"\bquick[-\s]?draw\b", re.IGNORECASE)


@dataclass(frozen=True)
class EquipmentProfile:
    """What the engine needs to know about one Equipment card."""

    # (energy, (domain, ...)) or None when the printed cost is not resources.
    equip_cost: tuple[int, tuple[str, ...]] | None
    # Why `equip_cost` is None, or why it is approximate. Empty when exact.
    cost_note: str = ""
    # 137 -- modulates the host's Might while attached. None means the card
    # prints no bonus at all, which is not the same as printing +0.
    might_bonus: int | None = None
    quick_draw: bool = False  # 819


def is_equipment(card) -> bool:
    """150.1 -- Equipment are exactly the gear carrying the Equipment tag."""
    if card.type != "gear":
        return False
    return any(str(k).lower() == EQUIPMENT_TAG for k in (card.keywords or []))


def _parse_cost(raw: str) -> tuple[tuple[int, tuple[str, ...]] | None, str]:
    if _OPAQUE_COST.match(raw):
        return None, ("Equip cost is not stated in the reminder text "
                      "(printed as \"Pay the cost\"); read it off the keyword")

    energy = 0
    match = _ENERGY.search(raw)
    if match:
        energy = int(match.group(1))
    else:
        # `1, Fury` / `1 Body` / `1 calm rune`: a leading bare number is
        # energy only when a domain follows it as a separate term. `1 calm
        # rune` is one *power*, not energy plus power.
        lead = _LEADING_NUMBER.match(raw)
        if lead and not re.search(r"^\s*\d+\s*,?\s*\w+\s+rune", raw, re.IGNORECASE):
            energy = int(lead.group(1))

    domains = tuple(d.capitalize() for d in _DOMAIN.findall(raw))
    note = ""
    if not domains and _ANY_RUNE.search(raw):
        # 163.2.b -- a rune of any type is Universal power. The engine spends
        # universal power for an unmatched domain, so an unknown domain is the
        # honest encoding; recorded rather than silently picked.
        return None, "Equip cost needs a rune of any type; not yet modelled"
    if not domains and energy == 0:
        return None, f"unrecognised Equip cost {raw!r}"
    return (energy, domains), note


def _parse_might_bonus(text: str) -> int | None:
    outside = _REMINDER_SPAN.sub(" ", text)
    matches = _MIGHT_BONUS.findall(outside)
    if not matches:
        return None
    signed, trailing = matches[-1]  # 137.1 -- the last one printed
    return int(signed or trailing)


# Parsing a card face is a pure function of immutable card data, but it was
# being redone on every call: profiling ISMCTS found `equip_ability` running
# 34,718 regex parses across six searches, because legal-action generation
# asks every card on the board for its activated abilities every ply.
#
# Keyed on the card *face*, not just the id. Keying on the id alone assumes
# id -> face is a bijection, which holds for the loaded database but silently
# returns the wrong answer for any constructed card that reuses an id -- as
# the parser's own tests do.
def _key(card) -> tuple:
    return (card.card_id, card.rules_text, tuple(card.keywords or ()))


_PROFILE_CACHE: dict[tuple, "EquipmentProfile | None"] = {}


def equipment_profile(card) -> EquipmentProfile | None:
    """The Equipment facts for `card`, or None if it is not Equipment."""
    key = _key(card)
    cached = _PROFILE_CACHE.get(key, _MISSING)
    if cached is not _MISSING:
        return cached
    profile = _parse_equipment(card)
    _PROFILE_CACHE[key] = profile
    return profile


def _parse_equipment(card) -> EquipmentProfile | None:
    if not is_equipment(card):
        return None
    text = card.rules_text or ""
    reminder = _REMINDER.search(text)
    if reminder:
        cost, note = _parse_cost(reminder.group("cost"))
    else:
        cost, note = None, "no Equip reminder text printed"
    return EquipmentProfile(
        equip_cost=cost,
        cost_note=note,
        might_bonus=_parse_might_bonus(text),
        quick_draw=bool(_QUICK_DRAW.search(text)),
    )


_ABILITY_CACHE: dict[tuple, "Ability | None"] = {}


def equip_ability(card) -> Ability | None:
    """818.1.c.2 -- "[Cost]: Attach this gear to a unit you control."

    None when the card is not Equipment, or when its cost could not be read
    off the card face. A card whose cost is unparsed stays inert rather than
    being handed a guessed cost.
    """
    key = _key(card)
    cached = _ABILITY_CACHE.get(key, _MISSING)
    if cached is not _MISSING:
        return cached
    ability = _build_equip_ability(card)
    _ABILITY_CACHE[key] = ability
    return ability


def _build_equip_ability(card) -> Ability | None:
    profile = equipment_profile(card)
    if profile is None or profile.equip_cost is None:
        return None
    energy, domains = profile.equip_cost
    costs: list = []
    if energy:
        costs.append(PayEnergy(energy))
    for domain in domains:
        costs.append(PayPower(domain, 1))
    bits = ([str(energy)] if energy else []) + list(domains)
    return Ability(
        kind=TriggerKind.ACTIVATED,
        costs=tuple(costs),
        effects=(_attach_to_friendly_unit(),),
        text=f"Equip {', '.join(bits)}",
    )


def _attach_to_friendly_unit() -> Attach:
    return Attach(selector=Selector(scope="choose", type="unit", controller="friendly"))


def discounted_equip_cost(
    cost: tuple[int, tuple[str, ...]] | None,
) -> tuple[int, tuple[str, ...]] | None:
    """821.1.c -- an Equip cost "reduced by [A]": one Power of any domain off.

    821.1.c.3: a cost with no Power in it "can still be paid, but will not be
    reduced". 821.1.c.4: a card with no Equip cost at all cannot be paid for,
    which is `None` here -- and RQ-13's four Equipment with unreadable costs
    land in that case rather than being handed a guess.
    """
    if cost is None:
        return None                                   # 821.1.c.4
    energy, domains = cost
    if not domains:
        return energy, ()                             # 821.1.c.3
    return energy, tuple(domains[1:])


def weaponmaster_ability(card) -> Ability | None:
    """821.1.c -- "When you play me, you may choose a Card you control with the
    Equipment tag ... Pay the cost of its Equip ability, reduced by [A], to
    attach it to this unit."

    Derived from the printed keyword, like Quick-Draw (819.1.d), so the 16
    cards carrying Weaponmaster need no per-card script.

    821.1.c and 725.3 make this an explicit exception to 718.2: an Equipment
    already attached elsewhere has Inactive Rules Text and cannot normally
    have its Equip ability activated, and Weaponmaster reaches it anyway.
    821.1.c.6 keeps it from being an activation -- no Equip trigger fires, and
    the Weaponmaster unit is not *chosen*, so no Deflect is paid for it.
    """
    import cards.keywords as kw

    if not kw.has(card.parsed_keywords, "Weaponmaster"):
        return None
    return Ability(
        kind=TriggerKind.ON_PLAY,
        effects=(EquipToMe(
            selector=Selector(scope="choose", type="gear", controller="friendly"),
        ),),
        text="Weaponmaster — attach an Equipment you control to me, its Equip "
             "cost reduced by one Power of any domain",
    )


def quick_draw_ability(card) -> Ability | None:
    """819.1.d -- Quick-Draw is short for "[Reaction]" and "When you play
    this, attach it to a Unit you control."

    This is the *only* way a gear attaches for free as it is played. Every
    other Equipment has to pay its Equip cost as a separate activated ability
    (818.1), which is the difference between "most gear you have to equip"
    and the handful that arrive already attached.
    """
    profile = equipment_profile(card)
    if profile is None or not profile.quick_draw:
        return None
    return Ability(
        kind=TriggerKind.ON_PLAY,
        effects=(_attach_to_friendly_unit(),),
        text="Quick-Draw — attach this to a unit you control",
    )
