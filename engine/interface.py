"""The frozen agent-facing contract.

This mirrors OpenSpiel's state interface so its ISMCTS implementation can be
dropped in later without a rewrite. Per the project brief, changing anything
in this file requires asking the repo owner first.

Nothing here knows any Riftbound rule. `engine/` implements this; `agents/`
consumes it and may use nothing else.

Note on hashing: `GameState` deliberately does NOT carry a `state_hash()`
method. The replay harness derives its hash from the five methods below (see
`engine/replay.py`), so the contract agents see stays exactly the five calls
the brief specifies.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Players are always 0 and 1. Riftbound Milestone 1 is strictly two-player.
PLAYERS: tuple[int, int] = (0, 1)


@runtime_checkable
class Action(Protocol):
    """An action must be hashable, ordered, and stably renderable.

    * hashable + equatable -- agents put actions in sets and dicts
    * ordered -- `legal_actions()` must be sortable into a canonical order, or
      determinism is lost the moment anything iterates a set
    * `__repr__` stable -- it lands in replay logs, which are diffed by hand
    """

    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...
    def __lt__(self, other: Any) -> bool: ...
    def __repr__(self) -> str: ...


@runtime_checkable
class Observation(Protocol):
    """One player's view of the state, with hidden information stripped.

    Must serialize deterministically: the same underlying state produces
    byte-identical output every time, in-process or across runs. This is what
    the replay harness hashes and what an ISMCTS determinizer will resample
    against, so instability here is silently corrupting.
    """

    def to_canonical_bytes(self) -> bytes: ...


@runtime_checkable
class GameState(Protocol):
    """The five calls agents get. Nothing more is exposed to them."""

    def legal_actions(self) -> list[Action]:
        """Actions legal for the player to move, in a canonical (sorted) order.

        Empty if and only if the state is terminal. Ordering must be a pure
        function of the state -- never of insertion order, set iteration, or
        object identity -- or seeded replays diverge between runs.
        """
        ...

    def apply(self, action: Action) -> None:
        """Advance the state in place. Must be a legal action.

        Any randomness consumed here must come from the state's own seeded
        generator, never the `random` module's global instance.
        """
        ...

    def observation(self, player_id: int) -> Observation:
        """This player's view, with all hidden information removed.

        Must not leak the opponent's hand, either deck's order, or anything
        else the player could not legally know.
        """
        ...

    def is_terminal(self) -> bool: ...

    def returns(self) -> tuple[float, float]:
        """Final payoff per player. Defined only when terminal.

        Convention for Riftbound: win = 1.0, loss = 0.0, draw = 0.5.
        """
        ...

    @property
    def current_player(self) -> int:
        """Which player is to move. Undefined when terminal."""
        ...
