"""A toy two-player game implementing `engine.interface.GameState`.

THIS IS NOT RIFTBOUND. Not one Riftbound rule is encoded here, and nothing in
this module should ever be imported by `engine/`, `cards/`, or `analysis/`.

It exists so the rules-agnostic infrastructure -- the replay harness, the
random agent, the batch runner, the determinism guarantee -- is real, tested
code before the Riftbound engine exists, and so the frozen agent-facing
interface is proven sufficient before the engine is written against it.

The game: each player holds a hidden hand drawn from their own seeded deck.
On your turn you either play a card (scoring its value and drawing a
replacement) or pass. Two consecutive passes end the game, as does either
player reaching the target score. Highest score wins; equal scores draw.

It is chosen to exercise the awkward parts of the contract: hidden
information on both sides, a seeded shuffle, unequal action counts per turn,
and a terminal condition reachable two different ways.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import total_ordering

TARGET_SCORE = 15
HAND_SIZE = 3
DECK_VALUES = tuple(range(1, 10))
DECK_COPIES = 2


@total_ordering
@dataclass(frozen=True)
class ToyAction:
    """`play:<value>` or `pass`. Ordered so `legal_actions()` is canonical."""

    kind: str  # "play" | "pass"
    value: int = 0

    def _sort_key(self) -> tuple[str, int]:
        return (self.kind, self.value)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ToyAction):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __repr__(self) -> str:
        return "pass" if self.kind == "pass" else f"play:{self.value}"


@dataclass(frozen=True)
class ToyObservation:
    """One player's view. The opponent's hand and both decks are hidden."""

    player_id: int
    own_hand: tuple[int, ...]
    own_score: int
    opponent_score: int
    opponent_hand_size: int
    own_deck_size: int
    opponent_deck_size: int
    consecutive_passes: int
    current_player: int

    def to_canonical_bytes(self) -> bytes:
        return (
            f"p{self.player_id}"
            f"|h{','.join(map(str, self.own_hand))}"
            f"|s{self.own_score}"
            f"|os{self.opponent_score}"
            f"|oh{self.opponent_hand_size}"
            f"|d{self.own_deck_size}"
            f"|od{self.opponent_deck_size}"
            f"|cp{self.consecutive_passes}"
            f"|t{self.current_player}"
        ).encode()


@dataclass
class ToyState:
    """Mutable game state. Cheaply deep-copyable, as MCTS will require."""

    seed: int
    hands: list[list[int]] = field(default_factory=list)
    decks: list[list[int]] = field(default_factory=list)
    scores: list[int] = field(default_factory=lambda: [0, 0])
    _current_player: int = 0
    consecutive_passes: int = 0
    turn_count: int = 0

    def __post_init__(self) -> None:
        if self.hands:
            return
        # Each player gets their own generator, so one player's draws never
        # shift the other's -- the same property Riftbound's two decks need.
        for player in (0, 1):
            rng = random.Random((self.seed << 1) | player)
            deck = [v for v in DECK_VALUES for _ in range(DECK_COPIES)]
            rng.shuffle(deck)
            self.hands.append(sorted(deck[:HAND_SIZE]))
            self.decks.append(deck[HAND_SIZE:])

    @property
    def current_player(self) -> int:
        return self._current_player

    def legal_actions(self) -> list[ToyAction]:
        if self.is_terminal():
            return []
        hand = self.hands[self._current_player]
        # sorted(set(...)) -- duplicate card values must not produce duplicate
        # actions, or replay action reprs become ambiguous.
        actions = [ToyAction("play", v) for v in sorted(set(hand))]
        actions.append(ToyAction("pass"))
        return sorted(actions)

    def apply(self, action: ToyAction) -> None:
        if self.is_terminal():
            raise ValueError("cannot apply an action to a terminal state")
        player = self._current_player

        if action.kind == "pass":
            self.consecutive_passes += 1
        elif action.kind == "play":
            hand = self.hands[player]
            if action.value not in hand:
                raise ValueError(f"player {player} has no {action.value} in hand")
            hand.remove(action.value)
            self.scores[player] += action.value
            if self.decks[player]:
                hand.append(self.decks[player].pop())
                hand.sort()
            self.consecutive_passes = 0
        else:
            raise ValueError(f"unknown action kind {action.kind!r}")

        self._current_player = 1 - player
        self.turn_count += 1

    def observation(self, player_id: int) -> ToyObservation:
        opponent = 1 - player_id
        return ToyObservation(
            player_id=player_id,
            own_hand=tuple(self.hands[player_id]),
            own_score=self.scores[player_id],
            opponent_score=self.scores[opponent],
            opponent_hand_size=len(self.hands[opponent]),
            own_deck_size=len(self.decks[player_id]),
            opponent_deck_size=len(self.decks[opponent]),
            consecutive_passes=self.consecutive_passes,
            current_player=self._current_player,
        )

    def is_terminal(self) -> bool:
        return self.consecutive_passes >= 2 or max(self.scores) >= TARGET_SCORE

    def returns(self) -> tuple[float, float]:
        if not self.is_terminal():
            raise ValueError("returns() is undefined for a non-terminal state")
        if self.scores[0] > self.scores[1]:
            return (1.0, 0.0)
        if self.scores[1] > self.scores[0]:
            return (0.0, 1.0)
        return (0.5, 0.5)


def build_toy_state(seed: int) -> ToyState:
    return ToyState(seed=seed)
