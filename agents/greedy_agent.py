"""One-ply greedy agent: pick the action with the best evaluated result.

The simplest possible consumer of `analysis.evaluation`. It exists as a
measuring stick: if the heuristic is worth anything, this beats the random
agent; if it does not, the heuristic is wrong and no amount of search on top
will save it.

Like every agent, it sees only the frozen interface plus the evaluator. It
does not know what a card is.
"""

from __future__ import annotations

import copy
import random

from analysis.evaluation import Model, evaluate, load_model
from engine.interface import Action, GameState


class GreedyAgent:
    """Applies each legal action to a clone and keeps the best-evaluated one.

    Ties are broken with a seeded RNG rather than by list order, so the agent
    is not silently biased toward whatever `legal_actions()` happens to sort
    first -- that bias would show up as strength the heuristic has not earned.
    """

    def __init__(self, seed: int, model: Model | None = None, epsilon: float = 0.0) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        self.model = model or load_model()
        # Optional exploration, useful when generating varied training data.
        self.epsilon = epsilon

    def act(self, state: GameState) -> Action:
        legal = state.legal_actions()
        if not legal:
            raise ValueError("no legal actions -- state should have been terminal")
        if len(legal) == 1:
            return legal[0]
        if self.epsilon and self._rng.random() < self.epsilon:
            return legal[self._rng.randrange(len(legal))]

        me = state.current_player
        best_score, best = None, []
        for action in legal:
            clone = copy.deepcopy(state)
            try:
                clone.apply(action)
            except Exception:
                continue  # never let a search crash lose the game
            score = evaluate(clone, me, self.model)
            if best_score is None or score > best_score:
                best_score, best = score, [action]
            elif score == best_score:
                best.append(action)
        if not best:
            return legal[self._rng.randrange(len(legal))]
        return best[self._rng.randrange(len(best))]

    def __call__(self, state: GameState) -> Action:
        return self.act(state)

    def reset(self) -> None:
        self._rng = random.Random(self.seed)
