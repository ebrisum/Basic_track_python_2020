"""Uniform-random legal-move agent.

Knows nothing about card identities, or about Riftbound at all -- it sees only
`legal_actions()`. That is the point: it is the baseline the engine's
performance target is measured against, and it exercises every legal action
the engine can generate.

Determinism: the agent owns a seeded `random.Random`, never the `random`
module's global generator. Same seed + same state sequence = same choices.
"""

from __future__ import annotations

import random

from engine.interface import Action, GameState


class RandomAgent:
    """Picks uniformly at random from `state.legal_actions()`."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def act(self, state: GameState) -> Action:
        legal = state.legal_actions()
        if not legal:
            raise ValueError("no legal actions -- state should have been terminal")
        # Index into the (canonically ordered) list rather than random.choice
        # so behaviour depends only on the ordering the engine guarantees.
        return legal[self._rng.randrange(len(legal))]

    def __call__(self, state: GameState) -> Action:
        return self.act(state)

    def reset(self) -> None:
        """Restore the agent to its initial seeded state."""
        self._rng = random.Random(self.seed)
