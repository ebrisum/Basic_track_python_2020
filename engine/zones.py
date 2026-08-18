"""Zones, locations, and the mutable board objects.

Every zone here traces to Core Rules v1.4 sections 105-108. Nothing in this
module knows about any specific card -- it deals in `CardRef` (a card id plus
per-instance state), never in card behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

# The six domains (164.1). Order is fixed so serialization is deterministic.
DOMAINS: tuple[str, ...] = ("Fury", "Calm", "Mind", "Body", "Chaos", "Order")
# 135.2.e.5 -- "[A]", power of any Domain, as a cost symbol.
ANY_DOMAIN = "*"


class Zone(str, Enum):
    """Zones a card can occupy (107 The Board, 108 Non-Board Zones)."""

    BASE = "base"  # 107.1
    BATTLEFIELD = "battlefield"  # 107.2
    LEGEND = "legend"  # 107.4
    CHAIN = "chain"  # 108.1
    TRASH = "trash"  # 108.2
    CHAMPION = "champion"  # 108.3
    MAIN_DECK = "main_deck"  # 108.4
    RUNE_DECK = "rune_deck"  # 108.5
    BANISHMENT = "banishment"  # 108.6
    HAND = "hand"


# A unit's Location is its Base or a Battlefield (146.1). Bases are per-player;
# battlefields are indexed. `None` means "not on the board".
BASE_LOCATION = "base"


@dataclass
class CardRef:
    """One physical card instance.

    `instance_id` is stable for the life of the game so the UI can address a
    specific copy when a player holds three of the same card.
    """

    instance_id: int
    card_id: str  # riftbound_id into cards.json
    owner: int
    controller: int
    # Board state, meaningful only while on the board.
    location: str | None = None  # BASE_LOCATION or "bf:<index>"
    exhausted: bool = False  # 414 / 415
    damage: int = 0  # 142, marked damage
    is_attacker: bool = False  # 464.2.c.3
    is_defender: bool = False
    # 423 -- a binary status. A Stunned unit contributes no Might to combat
    # damage (423.1.b) and loses the status at end of turn (423.1.a.2).
    stunned: bool = False
    # 185 -- a token can never become a card and a card can never become a
    # token (185.1.a/b), so this is fixed for the life of the instance.
    is_token: bool = False
    # 421 / 811 -- facedown at a battlefield. `hidden_at` is the battlefield
    # index; `hidden_on_turn` is when it was hidden, because 811.1.b only
    # allows playing it "beginning on the next turn".
    hidden_at: int | None = None
    hidden_on_turn: int = -1
    # Damage marked this combat is cleared in the Resolution Step (466).
    # Might modifiers (426 Buff, 701). Turn-scoped ones clear in the Ending
    # Phase; permanent ones persist while the object stays on the board.
    might_this_turn: int = 0
    might_permanent: int = 0
    # Keywords granted by effects: (name, value, duration).
    granted_keywords: tuple[tuple[str, int, str], ...] = ()
    # 434 Attach / 716 -- the unit this gear is attached to.
    attached_to: int | None = None


    def copy(self) -> "CardRef":
        """A copy sharing nothing mutable.

        Every field is a scalar, a string, or a tuple (`granted_keywords`), so
        copying the instance dict wholesale is a complete copy. This is the
        single hottest call in search -- roughly 200,000 per three games --
        and `dataclasses.replace` costs a `getattr` and a full `__init__` per
        field, which measured as the largest remaining cost after cloning was
        made targeted.

        `test_cloning.py` asserts every field's type is immutable, so adding a
        mutable one fails loudly rather than silently aliasing it.
        """
        new = CardRef.__new__(CardRef)
        new.__dict__.update(self.__dict__)
        return new

    def clone_key(self) -> tuple:
        """Canonical tuple for hashing/serialization."""
        return (
            self.instance_id,
            self.card_id,
            self.owner,
            self.controller,
            self.location,
            self.exhausted,
            self.damage,
            self.is_attacker,
            self.is_defender,
            self.might_this_turn,
            self.might_permanent,
            self.granted_keywords,
            self.attached_to,
        )


@dataclass
class Battlefield:
    """A battlefield in the Battlefield Zone (107.2, 190 Control)."""

    index: int
    card_id: str
    provider: int  # which player contributed it
    controller: int | None = None  # 190.2.b -- None = controlled by no one
    contested: bool = False  # 190.3.a
    contested_by: int | None = None
    # Which players have already Scored this battlefield this turn (470).
    scored_by: set[int] = field(default_factory=set)

    def copy(self) -> "Battlefield":
        # `scored_by` is the only mutable field; everything else is a scalar.
        return replace(self, scored_by=set(self.scored_by))

    def clone_key(self) -> tuple:
        return (
            self.index,
            self.card_id,
            self.provider,
            self.controller,
            self.contested,
            self.contested_by,
            tuple(sorted(self.scored_by)),
        )


@dataclass
class RunePool:
    """A player's rune pool (165). Emptied at Main Phase start (316.3).

    Energy has no domain (163.1.a). Power is domain-associated (163.2.a);
    Universal power pays any domain (163.2.b) and is tracked separately.
    """

    energy: int = 0
    power: dict[str, int] = field(default_factory=dict)  # domain -> count
    universal_power: int = 0

    def total_power(self) -> int:
        return sum(self.power.values()) + self.universal_power

    def clear(self) -> None:
        """316.3 -- unspent Energy and Power are lost."""
        self.energy = 0
        self.power = {}
        self.universal_power = 0

    def add_power(self, domain: str, amount: int = 1) -> None:
        self.power[domain] = self.power.get(domain, 0) + amount

    def can_pay(self, energy: int, power_domains: list[str]) -> bool:
        """Can this pool cover an (energy, power) cost? (163)

        Domain-specific power is spent before universal, so a cost is payable
        whenever any assignment works. Costs in Milestone 1 are small, so the
        greedy check below is exact: each required domain consumes a matching
        rune first, and anything unmatched falls back to universal.
        """
        if self.energy < energy:
            return False
        remaining = dict(self.power)
        universal = self.universal_power
        for domain in power_domains:
            # 135.2.e.5.a -- "[A]" is power of *any* domain, so it is paid by
            # whatever is available rather than by a matching rune.
            if domain == ANY_DOMAIN:
                if universal > 0:
                    universal -= 1
                elif any(count > 0 for count in remaining.values()):
                    pick = next(d for d, c in remaining.items() if c > 0)
                    remaining[pick] -= 1
                else:
                    return False
            elif remaining.get(domain, 0) > 0:
                remaining[domain] -= 1
            elif universal > 0:
                universal -= 1
            else:
                return False
        return True

    def pay(self, energy: int, power_domains: list[str]) -> None:
        if not self.can_pay(energy, power_domains):
            raise ValueError("cannot pay cost from this pool")
        self.energy -= energy
        for domain in power_domains:
            if domain == ANY_DOMAIN:
                if self.universal_power > 0:
                    self.universal_power -= 1
                else:
                    pick = next(d for d, c in self.power.items() if c > 0)
                    self.power[pick] -= 1
                    if self.power[pick] == 0:
                        del self.power[pick]
            elif self.power.get(domain, 0) > 0:
                self.power[domain] -= 1
                if self.power[domain] == 0:
                    del self.power[domain]
            else:
                self.universal_power -= 1

    def copy(self) -> "RunePool":
        return replace(self, power=dict(self.power))

    def clone_key(self) -> tuple:
        return (
            self.energy,
            tuple(sorted(self.power.items())),
            self.universal_power,
        )


@dataclass
class PlayerState:
    """One player's zones and resources."""

    player_id: int
    points: int = 0  # 194
    hand: list[int] = field(default_factory=list)  # instance ids
    main_deck: list[int] = field(default_factory=list)  # index 0 == top
    rune_deck: list[int] = field(default_factory=list)
    trash: list[int] = field(default_factory=list)
    banishment: list[int] = field(default_factory=list)
    champion_zone: list[int] = field(default_factory=list)  # 108.3
    legend: int | None = None  # 107.4
    # Permanents and runes in this player's Base (107.1.c).
    base: list[int] = field(default_factory=list)
    channeled_runes: list[int] = field(default_factory=list)  # runes on board
    pool: RunePool = field(default_factory=RunePool)
    # Set once the player has taken their first Channel Phase, for the 1v1
    # going-second extra rune (485.7).
    has_channeled: bool = False

    def copy(self) -> "PlayerState":
        """A copy sharing nothing mutable.

        Every zone is a list of instance ids -- ints are immutable, so a
        shallow list copy is a complete one. Only `pool` holds a nested
        mutable field.
        """
        return replace(
            self,
            hand=list(self.hand),
            main_deck=list(self.main_deck),
            rune_deck=list(self.rune_deck),
            trash=list(self.trash),
            banishment=list(self.banishment),
            champion_zone=list(self.champion_zone),
            base=list(self.base),
            channeled_runes=list(self.channeled_runes),
            pool=self.pool.copy(),
        )

    def clone_key(self) -> tuple:
        return (
            self.player_id,
            self.points,
            tuple(self.hand),
            tuple(self.main_deck),
            tuple(self.rune_deck),
            tuple(sorted(self.trash)),
            tuple(sorted(self.banishment)),
            tuple(self.champion_zone),
            self.legend,
            tuple(sorted(self.base)),
            tuple(sorted(self.channeled_runes)),
            self.pool.clone_key(),
            self.has_channeled,
        )
