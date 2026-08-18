"""The standard tokens (179-187).

Tokens are not cards (185), so they are not in `cards.json` -- there is no
printed card to normalize. 187 defines them by their characteristics instead,
and this module is that list, one entry per printed definition.

The rules that make tokens different from cards, and which the engine has to
honour:

* **186.1** -- a token put into any non-board zone besides the chain "ceases
  to exist immediately after moving to its new zone". A token does not go to
  the trash when it dies; it is gone. Card-conservation arithmetic has to know
  that, or a token's death looks like a card vanishing.
* **185.1.a/b** -- a token can never become a card and a card can never become
  a token, so the distinction is a fixed property of the instance.
* **182 / 183** -- a token's controller and owner are both the controller of
  the effect that created it, unless that effect says otherwise.
* **184.1 / 184.2** -- the creating effect may say the token enters ready or
  exhausted, and may restrict where it enters.
"""

from __future__ import annotations

from cards.database import CardData
from cards.keywords import parse

# Token ids are namespaced so they can never collide with a printed card id,
# and so a token is recognisable in a log or a replay at a glance.
PREFIX = "TOKEN-"


def _token(slug: str, name: str, type_: str, might: int, keywords: tuple[str, ...],
           rules_text: str = "") -> CardData:
    return CardData(
        card_id=f"{PREFIX}{slug}",
        name=name,
        type=type_,
        energy=0,
        power=0,
        might=might,
        domains=(),                 # 187 -- every standard token is domainless
        keywords=keywords,
        rules_text=rules_text,
        is_champion=False,
        set="TOKEN",
        parsed_keywords=parse(rules_text) if rules_text else (),
    )


# 187.1-187.5, in the order the rules list them.
TOKENS: dict[str, CardData] = {
    card.card_id: card
    for card in (
        _token("RECRUIT", "Recruit", "unit", 1, ("Recruit",)),
        _token("SPRITE", "Sprite", "unit", 3, ("Fae",),
               "TEMPORARY (If this is unattached, kill it at the start of your "
               "ending phase.)"),
        _token("SAND_SOLDIER", "Sand Soldier", "unit", 2, ("Shurima",)),
        _token("MECH", "Mech", "unit", 3, ("Mech",)),
        _token("GOLD", "Gold", "gear", 0, (),
               "[REACTION] Kill this, [E]: Add 1."),
    )
}

RECRUIT = f"{PREFIX}RECRUIT"
SPRITE = f"{PREFIX}SPRITE"
SAND_SOLDIER = f"{PREFIX}SAND_SOLDIER"
MECH = f"{PREFIX}MECH"
GOLD = f"{PREFIX}GOLD"


def is_token_id(card_id: str) -> bool:
    return card_id.startswith(PREFIX)
