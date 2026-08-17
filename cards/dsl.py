"""The effect DSL: primitives, selectors, triggers, costs.

Card effects are *data*, not code -- one `CardScript` per card, built from the
primitives below. There is deliberately no escape hatch that runs arbitrary
Python per card; if a card cannot be expressed here, the DSL is wrong.

The primitives are the game's own Game Actions (rules 413-444), adopted
verbatim rather than designed. Rule 411.4 keys triggers to game actions, so a
vocabulary that is not one-to-one with them cannot express "when you move an
enemy unit" correctly. See DSL.md for the derivation.

Nothing here executes; `cards/primitives.py` interprets these against a state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Duration(str, Enum):
    """How long a modifier lasts (701 Buffs, 426 Buff)."""

    THIS_TURN = "this_turn"
    PERMANENT = "permanent"


class Who(str, Enum):
    """Which player an effect acts on."""

    YOU = "you"  # the controller of the effect
    OPPONENT = "opponent"
    EACH = "each"  # 411.1 -- each player is responsible for their own action


# --------------------------------------------------------------------------
# Selectors -- Riftbound has no "target" keyword (0 uses in 888 card texts);
# selection is "choose a unit", "an enemy unit", "all units at battlefields".
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Selector:
    """Which game objects an effect applies to.

    `scope`:
      * `self`   -- the source card (053: cards refer to themselves in the first person)
      * `choose` -- the controller picks one; raises a choice request
      * `all`    -- every match, no choice
    """

    scope: str = "choose"  # self | choose | all
    type: str = "unit"  # unit | gear | spell | any
    controller: str = "any"  # any | friendly | enemy
    location: str = "any"  # any | battlefield | base
    max_might: int | None = None  # e.g. Gust: "3 Might or less"
    # `each_player` makes the selection happen once per player, by that player
    # (411.1 -- "Each player kills one of their gear").
    each_player: bool = False

    def describe(self) -> str:
        bits = [self.controller] if self.controller != "any" else []
        bits.append(self.type)
        if self.location != "any":
            bits.append(f"at {self.location}")
        if self.max_might is not None:
            bits.append(f"<= {self.max_might} might")
        return " ".join(bits)


SELF = Selector(scope="self")


# --------------------------------------------------------------------------
# Effects -- one class per Game Action used by a scripted card.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Effect:
    """Base class. Subclasses carry the rule they implement in their docstring."""


@dataclass(frozen=True)
class Draw(Effect):
    """413 Draw."""

    count: int = 1
    who: Who = Who.YOU


@dataclass(frozen=True)
class Discard(Effect):
    """422 Discard. The discarding player chooses which cards."""

    count: int = 1
    who: Who = Who.YOU


@dataclass(frozen=True)
class Deal(Effect):
    """417 Deal -- mark damage on units (142)."""

    amount: int
    selector: Selector = field(default_factory=Selector)


@dataclass(frozen=True)
class Kill(Effect):
    """428 Kill."""

    selector: Selector = field(default_factory=Selector)


@dataclass(frozen=True)
class Buff(Effect):
    """426 Buff -- a Might modifier (701)."""

    might: int
    duration: Duration = Duration.THIS_TURN
    selector: Selector = field(default_factory=lambda: SELF)


@dataclass(frozen=True)
class GrantKeyword(Effect):
    """Grant a keyword, optionally parameterised (804-829)."""

    keyword: str
    value: int | None = None
    duration: Duration = Duration.THIS_TURN
    selector: Selector = field(default_factory=Selector)


@dataclass(frozen=True)
class AddEnergy(Effect):
    """429 Add -- energy has no domain (163.1.a)."""

    count: int = 1


@dataclass(frozen=True)
class AddPower(Effect):
    """429 Add -- power carries a domain (163.2.a)."""

    domain: str
    count: int = 1


@dataclass(frozen=True)
class ReturnToHand(Effect):
    """A zone change to the owner's hand (446.2: not a Move)."""

    selector: Selector = field(default_factory=Selector)


@dataclass(frozen=True)
class LookAtTop(Effect):
    """Look at the top N, keep `take` in hand, Recycle the rest (416)."""

    count: int
    take: int = 1
    who: Who = Who.YOU


@dataclass(frozen=True)
class Attach(Effect):
    """434 Attach -- equip this gear to a unit (716 Attachment)."""

    selector: Selector = field(
        default_factory=lambda: Selector(controller="friendly", type="unit")
    )


@dataclass(frozen=True)
class Exhaust(Effect):
    """414 Exhaust."""

    selector: Selector = field(default_factory=lambda: SELF)


@dataclass(frozen=True)
class Ready(Effect):
    """415 Ready."""

    selector: Selector = field(default_factory=lambda: SELF)


@dataclass(frozen=True)
class GainPoints(Effect):
    """194.1.c -- gain points from a card effect."""

    count: int = 1
    who: Who = Who.YOU


@dataclass(frozen=True)
class UnitsEnterReady(Effect):
    """A turn-scoped permission: units you play this turn enter ready."""


# --------------------------------------------------------------------------
# Costs -- 201-204. A cost must be payable in full or the action is illegal.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Cost:
    """Base class for additional/activation costs."""


@dataclass(frozen=True)
class ExhaustSelf(Cost):
    """`[E]:` -- exhausting the source is the cost (164.2.a)."""


@dataclass(frozen=True)
class DiscardCost(Cost):
    """"Discard 1, Exhaust:" -- 422 as a cost."""

    count: int = 1


@dataclass(frozen=True)
class RecycleFromTrash(Cost):
    """416.3 -- must be completable for the cost to be paid."""

    count: int = 1


@dataclass(frozen=True)
class PayPower(Cost):
    """A domain-associated power cost (163.2)."""

    domain: str
    count: int = 1


@dataclass(frozen=True)
class PayEnergy(Cost):
    """A numeric energy cost (163.1)."""

    count: int = 1


# --------------------------------------------------------------------------
# Triggers and abilities
# --------------------------------------------------------------------------


class TriggerKind(str, Enum):
    ON_PLAY = "on_play"  # 382, "When you play me"
    ON_CONQUER = "on_conquer"  # 471.2.a
    ON_DEATH = "on_death"  # 808 Deathknell
    ACTIVATED = "activated"  # 376, "Cost: effect"
    ON_RESOLVE = "on_resolve"  # a spell's own effect (351.2)


@dataclass(frozen=True)
class Ability:
    """One ability: a trigger, its costs, and the effects it produces."""

    kind: TriggerKind
    effects: tuple[Effect, ...] = ()
    costs: tuple[Cost, ...] = ()
    # Human-readable, for the UI and for test failure messages.
    text: str = ""


@dataclass(frozen=True)
class CardScript:
    """Everything the engine executes for one card."""

    card_id: str
    abilities: tuple[Ability, ...] = ()
    # Set when the script covers the card's whole printed text. False means
    # part of the text is still inert, and the UI keeps saying so.
    complete: bool = True
    note: str = ""

    def of_kind(self, kind: TriggerKind) -> tuple[Ability, ...]:
        return tuple(a for a in self.abilities if a.kind is kind)


@dataclass
class ChoiceRequest:
    """Raised by an effect that needs the player to pick something.

    The engine turns this into `legal_actions()` and resumes the effect once
    the choice arrives.
    """

    player: int
    options: tuple[int, ...]  # instance ids
    prompt: str
    # Set when the player may decline (e.g. "you may"). 355.1.a.
    optional: bool = False
