"""Repeat (820): reading the optional additional cost off the card face.

820.1.c prints it as "Repeat [Cost]", and the pool spells that at least four
ways:

    [REPEAT 1]          1 energy
    [REPEAT 2, Fury]    2 energy and one Fury power
    [REPEAT 4 Mind]     4 energy and one Mind power
    [REPEAT]            no cost printed at all

The last case is not a free Repeat. SFD-040 prints a bare marker, and SFD-078
grants "[REPEAT] equal to its cost" -- a value that only exists once the spell
is being played. Neither can be read off the face, so `repeat_cost` returns
None and the card simply is not offered the option, the same treatment RQ-13's
unreadable Equip costs get.

This is the same shape as `cards/gear.py`: derive the mechanic from the
printed keyword so the 17 cards carrying Repeat need no per-card script.
"""

from __future__ import annotations

import re

from engine.zones import DOMAINS

# The keyword and everything up to the closing bracket or parenthesis. Written
# to tolerate `[REPEAT 2, Fury]`, `[REPEAT 4 Mind]` and a bare `REPEAT`.
_REPEAT = re.compile(r"\[?\bREPEAT\b(?P<cost>[^\]\)\n]*)", re.IGNORECASE)
_NUMBER = re.compile(r"(\d+)")
_DOMAIN = re.compile(r"\b(" + "|".join(DOMAINS) + r")\b", re.IGNORECASE)

_MISSING = object()
_CACHE: dict[tuple, "tuple[int, tuple[str, ...]] | None"] = {}


def _key(card) -> tuple:
    # Keyed on the face rather than the id, for the reason cards/gear.py
    # documents: a constructed card can reuse an id.
    return (card.card_id, card.rules_text, tuple(card.keywords or ()))


def repeat_cost(card) -> "tuple[int, tuple[str, ...]] | None":
    """The Repeat cost as (energy, domains), or None if the card has none.

    None covers both "this card does not have Repeat" and "it has Repeat but
    the cost is not printed", which are the same thing for the engine: no
    offer is made either way.
    """
    key = _key(card)
    cached = _CACHE.get(key, _MISSING)
    if cached is not _MISSING:
        return cached
    cost = _parse(card)
    _CACHE[key] = cost
    return cost


def _parse(card) -> "tuple[int, tuple[str, ...]] | None":
    text = card.rules_text or ""
    match = _REPEAT.search(text)
    if not match:
        return None
    raw = match.group("cost")
    number = _NUMBER.search(raw)
    domains = tuple(d.capitalize() for d in _DOMAIN.findall(raw))
    if number is None and not domains:
        # 820.1.c with no cost printed: SFD-040's bare marker, or SFD-078's
        # granted "equal to its cost". Not readable here.
        return None
    return (int(number.group(1)) if number else 0), domains


def has_repeat(card) -> bool:
    """820.4 -- whether the card carries a Repeat the engine can offer."""
    return repeat_cost(card) is not None
