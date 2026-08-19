"""The Chain, priority, focus, and showdowns (307-348).

The chain is the game's resolution engine. Everything played goes onto it,
players get windows to respond, and items resolve newest-first. Getting this
right is what makes "response windows and priority passing during showdowns"
-- the trouble spot flagged at the start of the project -- actually testable.

This module holds the data structures and the pure timing predicates. The
state machine that drives them lives in `engine/state.py`, which owns the
mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass
class ChainItem:
    """One card or activated ability on the Chain (329).

    Items are Pending until the "Check Legality" step of playing, then
    Finalized (329.2-329.3). Finalized items resolve newest-first (340.1).
    """

    kind: str  # "card" | "ability"
    instance_id: int
    controller: int
    ability_index: int = 0
    pending: bool = True
    # True when the item was put on the chain by a triggered ability or an
    # ability that Adds resources. Focus does not pass for these (346.1).
    from_trigger: bool = False
    # 811.1.d -- the battlefield this card was played from Hidden at, or None
    # for an ordinary play. It belongs to the item and not to the state: the
    # chain can hold several cards at once, and a card played in response must
    # not disturb where an earlier hidden card makes its choices.
    from_hidden: int | None = None
    # 355.8 -- "In order to put a spell or ability on the chain, valid choices
    # must be made for all targets." Declared as the item is played and
    # carried here, keyed by (ability index, effect index) so a choice can be
    # matched back to the effect it belongs to on resolution.
    targets: dict = field(default_factory=dict)

    def copy(self) -> "ChainItem":
        # `targets` is the one mutable field; everything else is a scalar.
        return replace(self, targets={k: v for k, v in self.targets.items()})

    def clone_key(self) -> tuple:
        return (
            self.kind,
            self.instance_id,
            self.controller,
            self.ability_index,
            self.pending,
            self.from_trigger,
            self.from_hidden,
            tuple(sorted(self.targets.items())),
        )


@dataclass
class Showdown:
    """A Showdown in progress (341-348).

    A Combat Showdown (464) is the same window with `is_combat` set; when it
    closes, combat proceeds to the Damage Step rather than simply establishing
    control.
    """

    battlefield: int
    attacker: int  # the player who applied Contested status (464.2.c.1)
    defender: int
    is_combat: bool = False
    # Consecutive passes with nothing added; all players passing ends it
    # (347.2.a).
    passes: int = 0

    def copy(self) -> "Showdown":
        return replace(self)   # every field is a scalar

    def clone_key(self) -> tuple:
        return (
            self.battlefield,
            self.attacker,
            self.defender,
            self.is_combat,
            self.passes,
        )


# --------------------------------------------------------------------------
# Timing -- the four-state machine at 307-310.
# --------------------------------------------------------------------------


def is_closed(chain: list[ChainItem]) -> bool:
    """309.1 -- a Chain exists, so the turn is in a Closed State."""
    return bool(chain)


def timing_label(chain: list[ChainItem], showdown: Showdown | None) -> str:
    """The turn's state as one of the four names in 310."""
    axis1 = "Showdown" if showdown is not None else "Neutral"
    axis2 = "Closed" if is_closed(chain) else "Open"
    return f"{axis1} {axis2}"


class _WithReaction:
    """A card view that has Reaction, for 811.1.b's grant."""

    def __init__(self, card):
        self._card = card

    def __getattr__(self, name):
        return getattr(self._card, name)

    @property
    def has_reaction(self) -> bool:
        return True


def can_play(
    card,
    player: int,
    turn_player: int,
    chain: list[ChainItem],
    showdown: Showdown | None,
    reaction_override: bool = False,
) -> bool:
    """`reaction_override` grants Reaction timing regardless of the printed
    card, which is what 811.1.b does to a card played from Hidden."""
    if reaction_override:
        card = _WithReaction(card)
    """Whether `card` may be played right now, by timing alone (308-310).

    * Closed (a chain exists): only Reaction (309.1.a).
    * Showdown Open: only Action or Reaction (308.1.a).
    * Neutral Open: anything, but only the turn player (310.1.a).

    Cost and zone legality are checked separately by the caller.
    """
    if is_closed(chain):
        return card.has_reaction
    if showdown is not None:
        return card.has_action or card.has_reaction
    return player == turn_player


def ability_can_activate(
    card,
    ability,
    player: int,
    turn_player: int,
    chain: list[ChainItem],
    showdown: Showdown | None,
) -> bool:
    """Same timing test for an activated ability (145.2, 398).

    An ability's own printed timing wins: the rune-seal cycle reads
    "Exhaust: REACTION - ADD ..." so it is legal in a Closed state even though
    the gear itself is not.
    """
    text = (ability.text or "").lower()
    is_reaction = "reaction" in text or card.has_reaction
    is_action = "action" in text or card.has_action

    if is_closed(chain):
        return is_reaction
    if showdown is not None:
        return is_action or is_reaction
    return player == turn_player


def resolves_immediately(card, ability=None) -> bool:
    """337.2 -- a finalized Unit, Gear, or resource-Adding ability resolves at
    once rather than waiting for priority to pass."""
    if ability is not None:
        from cards.dsl import AddEnergy, AddPower

        return any(isinstance(e, (AddEnergy, AddPower)) for e in ability.effects)
    return card.type in ("unit", "gear")
