"""The action vocabulary agents and the UI speak.

Actions are frozen, ordered, hashable dataclasses so `legal_actions()` returns
a canonical list (required by `engine.interface`) and `repr()` is stable enough
to key replays.

Every action maps to a Game Action or Discretionary Action in the rules; the
citation is on each class.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering
from typing import Any


@total_ordering
@dataclass(frozen=True)
class Action:
    """Base class. Subclasses define `kind` and their own fields."""

    def _sort_key(self) -> tuple:
        return (type(self).__name__,) + tuple(
            str(v) for v in self.__dict__.values()
        )

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Action):
            return NotImplemented
        return self._sort_key() < other._sort_key()


@dataclass(frozen=True, order=False)
class ChooseBattlefield(Action):
    """Setup: each player randomly selects 1 of their 3 battlefields (485.5).

    Presented as a choice so a human can drive setup; the random default is
    what an agent takes.
    """

    index: int

    def __repr__(self) -> str:
        return f"choose_battlefield:{self.index}"


@dataclass(frozen=True, order=False)
class Mulligan(Action):
    """117 -- set aside up to two cards, draw that many, recycle the set-aside."""

    instance_ids: tuple[int, ...]

    def __repr__(self) -> str:
        ids = ",".join(str(i) for i in self.instance_ids)
        return f"mulligan:[{ids}]"


@dataclass(frozen=True, order=False)
class PlayCard(Action):
    """419 Play. Units and Gear go to the controller's Base (148.1.a.1)."""

    instance_id: int

    def __repr__(self) -> str:
        return f"play:{self.instance_id}"


@dataclass(frozen=True, order=False)
class StandardMove(Action):
    """144 -- exhaust a unit to move Base<->Battlefield.

    `destination` is `"base"` or `"bf:<index>"`.
    """

    instance_id: int
    destination: str

    def __repr__(self) -> str:
        return f"move:{self.instance_id}->{self.destination}"


@dataclass(frozen=True, order=False)
class ChannelRune(Action):
    """430 Channel -- put a rune from the Rune Deck onto the board."""

    def __repr__(self) -> str:
        return "channel"


@dataclass(frozen=True, order=False)
class ExhaustRuneForEnergy(Action):
    """164.2.a -- `[E]: Add [1]` (one energy)."""

    instance_id: int

    def __repr__(self) -> str:
        return f"tap_energy:{self.instance_id}"


@dataclass(frozen=True, order=False)
class RecycleRuneForPower(Action):
    """164.2.b -- `Recycle this: Add [C]` (one power of the rune's domain)."""

    instance_id: int

    def __repr__(self) -> str:
        return f"recycle_power:{self.instance_id}"


@dataclass(frozen=True, order=False)
class PassPhase(Action):
    """305 -- decline further Discretionary Actions; the phase ends."""

    def __repr__(self) -> str:
        return "pass"


@dataclass(frozen=True, order=False)
class AssignDamageTo(Action):
    """465.2.c -- assign combat damage to one opposing unit.

    Assignment is done one unit at a time rather than as a whole distribution.
    Enumerating every legal distribution is combinatorially large and useless
    to a search agent; assigning lethal-then-next covers the strategically
    real choices. Logged as an approximation in RULES_QUESTIONS.md (RQ-7).
    """

    instance_id: int

    def __repr__(self) -> str:
        return f"assign_damage:{self.instance_id}"


@dataclass(frozen=True, order=False)
class Concede(Action):
    """649 -- leave the game; the opponent is the only player remaining (195)."""

    def __repr__(self) -> str:
        return "concede"
