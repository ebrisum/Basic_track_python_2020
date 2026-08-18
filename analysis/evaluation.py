"""Heuristic position evaluation -- search guidance, NOT the reward.

The only thing that defines winning is `state.returns()`: 1.0 for a win, 0.0
for a loss, 0.5 for a draw. That never changes, and nothing in this file is
allowed to change it.

This module estimates *how likely a player is to reach that win* from a
non-terminal position. It exists because search needs a value for positions it
cannot roll out to the end, not because points or board presence are worth
anything in themselves.

## Why the separation matters

Folding these features into the reward would be reward shaping, and reward
shaping optimises the proxy. An agent paid for "units on battlefields" learns
to park units on battlefields; an agent paid for points learns to take points
that lose the game -- which is a real line in Riftbound, since the Final Point
rule (471.1.b) turns a greedy conquer into a wasted draw.

So: the terminal signal is winning. This is a *prior* over winning, and it is
only trusted as far as it has been measured. `analysis/calibrate.py` measures
it; `analysis/fit_weights.py` fits it to self-play outcomes rather than to
anyone's intuition.

## Reading the output

`evaluate()` returns an estimated win probability in [0, 1] for the given
player. 0.5 means "no idea"; a terminal position returns the true result.

Every feature is a *difference* between the two players, which makes the whole
vector antisymmetric and therefore guarantees
`evaluate(s, 0) + evaluate(s, 1) == 1`. Search relies on that: it lets a value
be negated for the opponent instead of recomputed, and it is the invariant that
catches a feature accidentally written from one player's point of view.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

WEIGHTS_PATH = Path(__file__).resolve().parent / "weights.json"
# `fit_weights.py` writes here. Nothing loads it automatically: a candidate is
# only installed at WEIGHTS_PATH after it wins a head-to-head benchmark, since
# a better Brier score does not imply a better player. See SCORING.md.
CANDIDATE_PATH = Path(__file__).resolve().parent / "weights.candidate.json"

# Feature order is fixed: weights.json is a plain list and must line up.
FEATURE_NAMES: tuple[str, ...] = (
    "point_diff",           # points ahead
    "point_progress",       # nonlinear closeness to the Victory Score
    "battlefield_diff",     # battlefields controlled, minus theirs
    "unit_count_diff",      # units on the board
    "might_diff",           # total current Might on the board
    "bf_presence_diff",     # units standing at battlefields specifically
    "hand_diff",            # cards in hand
    "rune_diff",            # runes channeled (resource development)
    "deck_diff",            # main deck remaining (burn-out risk)
    "tempo",                # ready (unexhausted) units
    "victory_pressure",     # how close to winning, given the current scoring rate
)

# Hand-set starting point, replaced by `fit_weights.py` once self-play data
# exists. `point_diff` dominates deliberately: it is the win condition, and
# everything else is a guess at future points.
DEFAULT_WEIGHTS: tuple[float, ...] = (
    1.20,   # point_diff
    0.35,   # point_progress
    0.55,   # battlefield_diff
    0.12,   # unit_count_diff
    0.05,   # might_diff
    0.20,   # bf_presence_diff
    0.08,   # hand_diff
    0.06,   # rune_diff
    0.02,   # deck_diff
    0.04,   # tempo
    0.90,   # victory_pressure
)
DEFAULT_BIAS: float = 0.0

# Scales that turn raw counts into roughly unit-sized inputs, so the weights
# are comparable to each other and the logistic does not saturate.
SCALES: dict[str, float] = {
    "point_diff": 4.0,
    "point_progress": 1.0,
    "battlefield_diff": 2.0,
    "unit_count_diff": 4.0,
    "might_diff": 12.0,
    "bf_presence_diff": 3.0,
    "hand_diff": 5.0,
    "rune_diff": 6.0,
    "deck_diff": 20.0,
    "tempo": 4.0,
    "victory_pressure": 1.0,
}


@dataclass(frozen=True)
class Model:
    weights: tuple[float, ...] = DEFAULT_WEIGHTS
    bias: float = DEFAULT_BIAS
    # Set when the weights came from a fit, so callers can tell a measured
    # model from the hand-set prior.
    fitted: bool = False
    trained_on_games: int = 0

    def score(self, features: dict[str, float]) -> float:
        total = self.bias
        for weight, name in zip(self.weights, FEATURE_NAMES):
            total += weight * features[name]
        return total


def load_model(path: Path | None = None) -> Model:
    """Load fitted weights if they exist, else the hand-set prior."""
    source = path or WEIGHTS_PATH
    if not source.exists():
        return Model()
    raw = json.loads(source.read_text())
    return Model(
        weights=tuple(raw["weights"]),
        bias=float(raw.get("bias", 0.0)),
        fitted=True,
        trained_on_games=int(raw.get("trained_on_games", 0)),
    )


def save_model(model: Model, path: Path | None = None) -> None:
    dest = path or WEIGHTS_PATH
    dest.write_text(
        json.dumps(
            {
                "weights": list(model.weights),
                "bias": model.bias,
                "feature_names": list(FEATURE_NAMES),
                "trained_on_games": model.trained_on_games,
            },
            indent=2,
        )
        + "\n"
    )


def _pressure(points: int, battlefields: int) -> float:
    """Urgency in [0, 1]: how near a player is to winning at their current rate.

    Estimates turns-to-victory as (points still needed) / (points per turn),
    where the rate is the battlefields under control, since Hold scores one per
    controlled battlefield per turn (469.2). A player with no board has no
    clock at all and reads as no threat, however many points they hold.

    This replaced a hard threshold at "one point from winning", which said the
    same thing about a player at 7 with an empty board as one at 7 holding
    everything.
    """
    from engine.state import VICTORY_SCORE

    needed = max(0, VICTORY_SCORE - points)
    if needed == 0:
        return 1.0
    if battlefields <= 0:
        # No scoring rate: only off-board effects can win, so treat the clock
        # as long rather than infinite.
        return 1.0 / (1.0 + needed * 2.0)
    return 1.0 / (1.0 + needed / battlefields)


def features(state, player: int) -> dict[str, float]:
    """Extract the feature vector for `player`, scaled to roughly [-1, 1].

    Reads the state directly rather than an observation: evaluation runs
    inside search, which already has full state, and going through the
    observation would hide the opponent's hand from a determinized rollout
    that is entitled to see it.
    """
    from engine.state import VICTORY_SCORE

    opponent = 1 - player
    me, them = state.players[player], state.players[opponent]

    def units_of(pid: int):
        return [
            ref
            for ref in state.cards.values()
            if ref.location is not None
            and state.db[ref.card_id].type == "unit"
            and ref.controller == pid
        ]

    my_units, their_units = units_of(player), units_of(opponent)
    at_bf = lambda refs: [r for r in refs if (r.location or "").startswith("bf:")]

    my_bfs = sum(1 for bf in state.battlefields if bf.controller == player)
    their_bfs = sum(1 for bf in state.battlefields if bf.controller == opponent)

    raw = {
        "point_diff": me.points - them.points,
        # Nonlinear: 7 points is worth far more than 3, even at equal diff,
        # because the Victory Score is an absolute threshold. Written as a
        # difference so the whole vector stays antisymmetric -- see
        # `test_evaluation_is_zero_sum`.
        "point_progress": (
            (min(me.points, VICTORY_SCORE) / VICTORY_SCORE) ** 2
            - (min(them.points, VICTORY_SCORE) / VICTORY_SCORE) ** 2
        ),
        "battlefield_diff": my_bfs - their_bfs,
        "unit_count_diff": len(my_units) - len(their_units),
        "might_diff": (
            sum(state.might_of(r) for r in my_units)
            - sum(state.might_of(r) for r in their_units)
        ),
        "bf_presence_diff": len(at_bf(my_units)) - len(at_bf(their_units)),
        "hand_diff": len(me.hand) - len(them.hand),
        "rune_diff": len(me.channeled_runes) - len(them.channeled_runes),
        "deck_diff": len(me.main_deck) - len(them.main_deck),
        "tempo": (
            sum(1 for r in my_units if not r.exhausted)
            - sum(1 for r in their_units if not r.exhausted)
        ),
        # Urgency, measured in turns rather than points.
        #
        # A threshold at "7 points" is the wrong shape: 7 points while
        # controlling nothing is not urgent, and 5 points while holding both
        # battlefields is a two-turn clock. What matters is how fast the score
        # is actually moving, and control is the rate -- a held battlefield
        # scores once per turn in the Beginning Phase (469.2).
        "victory_pressure": _pressure(me.points, my_bfs) - _pressure(them.points, their_bfs),
    }
    return {name: raw[name] / SCALES[name] for name in FEATURE_NAMES}


def evaluate(state, player: int, model: Model | None = None) -> float:
    """Estimated win probability for `player`, in [0, 1].

    A terminal state returns the true result -- the heuristic never overrides
    the actual outcome.
    """
    if state.is_terminal():
        return state.returns()[player]
    active = model or load_model()
    return 1.0 / (1.0 + math.exp(-active.score(features(state, player))))


def explain(state, player: int, model: Model | None = None) -> list[tuple[str, float, float]]:
    """(feature, scaled value, contribution) rows, largest contribution first.

    For looking at why the heuristic likes a position -- the thing that makes
    a shaped score debuggable instead of a black box.
    """
    active = model or load_model()
    values = features(state, player)
    rows = [
        (name, values[name], weight * values[name])
        for weight, name in zip(active.weights, FEATURE_NAMES)
    ]
    return sorted(rows, key=lambda row: -abs(row[2]))
