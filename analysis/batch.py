"""Batch simulation and aggregation.

Produces the Milestone 1 output: win rate, mean game length in turns, and the
point-differential distribution. Rules-agnostic -- it drives anything
implementing `engine.interface.GameState` and reads only the frozen interface.

Determinism: game `i` of a batch uses seed `base_seed + i`, and each agent is
re-seeded per game from that seed. Re-running a batch, or any single game from
it, reproduces it exactly. That is what makes a surprising result
investigable.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from engine.interface import Action, GameState

StateBuilder = Callable[[int], GameState]
PolicyFactory = Callable[[int], Callable[[GameState], Action]]

MAX_STEPS = 10_000


@dataclass
class GameResult:
    seed: int
    returns: tuple[float, float]
    turns: int
    # Player 0's score minus player 1's, where the game exposes scores.
    point_differential: int | None = None


@dataclass
class BatchResult:
    results: list[GameResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def games(self) -> int:
        return len(self.results)

    def win_rate(self, player: int = 0) -> float:
        """Fraction of the payoff won by `player`. Draws count as half."""
        if not self.results:
            return 0.0
        return sum(r.returns[player] for r in self.results) / len(self.results)

    def record(self, player: int = 0) -> tuple[int, int, int]:
        """(wins, losses, draws) from `player`'s perspective."""
        wins = losses = draws = 0
        for r in self.results:
            mine, theirs = r.returns[player], r.returns[1 - player]
            if mine > theirs:
                wins += 1
            elif theirs > mine:
                losses += 1
            else:
                draws += 1
        return wins, losses, draws

    @property
    def mean_turns(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.turns for r in self.results) / len(self.results)

    def point_differential_distribution(self) -> dict[int, int]:
        """Histogram of player 0's point differential, ascending."""
        counts = Counter(
            r.point_differential
            for r in self.results
            if r.point_differential is not None
        )
        return dict(sorted(counts.items()))

    @property
    def games_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return float("inf")
        return self.games / self.elapsed_seconds

    def summary(self) -> str:
        wins, losses, draws = self.record(0)
        lines = [
            f"games:            {self.games}",
            f"win rate (p0):    {self.win_rate(0):.4f}  ({wins}W-{losses}L-{draws}D)",
            f"mean game length: {self.mean_turns:.2f} turns",
            f"elapsed:          {self.elapsed_seconds:.2f}s "
            f"({self.games_per_second:.1f} games/s)",
        ]
        dist = self.point_differential_distribution()
        if dist:
            lines.append("point differential (p0 - p1):")
            widest = max(dist.values())
            for diff, count in dist.items():
                bar = "#" * max(1, round(40 * count / widest))
                lines.append(f"  {diff:+4d} | {count:5d} {bar}")
        return "\n".join(lines)


def play_game(
    build_state: StateBuilder,
    policies: tuple[Callable[[GameState], Action], Callable[[GameState], Action]],
    seed: int,
    max_steps: int = MAX_STEPS,
) -> GameResult:
    """Play one game to completion and return its result."""
    state = build_state(seed)
    steps = 0
    while not state.is_terminal():
        if steps >= max_steps:
            raise RuntimeError(
                f"game seed={seed} exceeded {max_steps} steps without "
                f"terminating -- likely a rules loop."
            )
        state.apply(policies[state.current_player](state))
        steps += 1

    scores = getattr(state, "scores", None)
    differential = (scores[0] - scores[1]) if scores is not None else None
    return GameResult(
        seed=seed,
        returns=tuple(state.returns()),
        turns=steps,
        point_differential=differential,
    )


def run_batch(
    build_state: StateBuilder,
    policy_factories: tuple[PolicyFactory, PolicyFactory],
    games: int,
    base_seed: int = 0,
    max_steps: int = MAX_STEPS,
) -> BatchResult:
    """Play `games` games, seeding game `i` with `base_seed + i`.

    Agents are rebuilt per game from that same seed, so any single game is
    reproducible standalone -- no need to replay the whole batch to
    investigate one outlier.
    """
    batch = BatchResult()
    started = time.perf_counter()
    for i in range(games):
        seed = base_seed + i
        policies = (policy_factories[0](seed), policy_factories[1](seed))
        batch.results.append(play_game(build_state, policies, seed, max_steps))
    batch.elapsed_seconds = time.perf_counter() - started
    return batch
