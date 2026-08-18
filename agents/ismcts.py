"""Information Set Monte Carlo Tree Search.

The answer to "how does the AI figure the hard judgments out itself".

A linear evaluation cannot express the tactics that actually decide Riftbound
games, and no amount of weight-fitting will fix that, because the judgments are
*conditional*:

- Sweeping the board is right when behind and wrong when ahead. That is an
  interaction between two features, which a weighted sum cannot represent.
- Denying the opponent's point is worth a card at 7 points and worth nothing at
  1. Same action, opposite valuation, depending on the score.
- Holding a Reaction is only correct if something worth answering is coming,
  which is a fact about the *future*, not the present position.

Search represents all three for free, because it plays the consequences out.
The evaluation's job shrinks to scoring leaves; the tactics are discovered.

## How it handles hidden information

Riftbound hides the opponent's hand and both deck orders (128). Plain MCTS
would cheat by reading them. ISMCTS instead *determinizes*: each iteration
samples one concrete world consistent with what the searching player can see,
searches that, and shares statistics across iterations keyed by action
sequence. Averaged over many determinizations, the agent plans against the
distribution of possible hands rather than the one it happens to be holding.

Leaves are scored with `analysis.evaluation` rather than played to the end.
Full rollouts from a mid-game Riftbound position take hundreds of steps; a
value function makes the search affordable, and it is the same function the
benchmark already showed beats random.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field

from analysis.evaluation import Model, evaluate, load_model
from engine.interface import Action, GameState

DEFAULT_ITERATIONS = 120
DEFAULT_EXPLORATION = 1.2
DEFAULT_DEPTH = 12


@dataclass
class Node:
    """Statistics for one information set, keyed by the path that reached it."""

    visits: int = 0
    total_value: float = 0.0
    children: dict[str, "Node"] = field(default_factory=dict)
    # Times each action was *available*, which is what ISMCTS divides by --
    # an action only legal in some determinizations must not be punished for
    # the iterations where it never appeared.
    availability: dict[str, int] = field(default_factory=dict)

    def value(self) -> float:
        return self.total_value / self.visits if self.visits else 0.0


def determinize(state: GameState, player: int, rng: random.Random) -> GameState:
    """Sample a world consistent with `player`'s knowledge (128 Privacy).

    The searching player knows their own hand and everything on the board, but
    not the opponent's hand or either deck order. Both decks are shuffled, and
    the opponent's hand is redealt from their shuffled deck -- keeping its
    size, which *is* public.
    """
    clone = copy.deepcopy(state)
    opponent = 1 - player

    them = clone.players[opponent]
    pool = list(them.hand) + list(them.main_deck)
    rng.shuffle(pool)
    them.hand = pool[: len(them.hand)]
    them.main_deck = pool[len(them.hand):]

    # Our own deck order is unknown to us too.
    rng.shuffle(clone.players[player].main_deck)
    rng.shuffle(clone.players[player].rune_deck)
    rng.shuffle(them.rune_deck)
    return clone


class ISMCTSAgent:
    """ISMCTS with a value-function leaf evaluation.

    `iterations` is the search budget per decision. `depth` caps how far a
    single iteration walks before the leaf is scored -- Riftbound turns are
    long, and an uncapped descent spends the whole budget on one line.
    """

    def __init__(
        self,
        seed: int,
        iterations: int = DEFAULT_ITERATIONS,
        model: Model | None = None,
        exploration: float = DEFAULT_EXPLORATION,
        depth: int = DEFAULT_DEPTH,
    ) -> None:
        self.seed = seed
        self.iterations = iterations
        self.exploration = exploration
        self.depth = depth
        self.model = model or load_model()
        self._rng = random.Random(seed)

    # -- selection ----------------------------------------------------------

    def _select(self, node: Node, legal: list[Action]) -> Action:
        """UCB1, over the actions legal in *this* determinization."""
        keys = [repr(a) for a in legal]
        unseen = [a for a, k in zip(legal, keys) if k not in node.children]
        if unseen:
            return unseen[self._rng.randrange(len(unseen))]

        log_avail = {k: math.log(max(1, node.availability.get(k, 1))) for k in keys}
        best, best_score = None, -float("inf")
        for action, key in zip(legal, keys):
            child = node.children[key]
            if child.visits == 0:
                return action
            score = child.value() + self.exploration * math.sqrt(
                log_avail[key] / child.visits
            )
            if score > best_score:
                best, best_score = action, score
        return best if best is not None else legal[0]

    # -- one iteration ------------------------------------------------------

    def _iterate(self, root: Node, state: GameState, me: int) -> None:
        node = root
        path: list[tuple[Node, str]] = []

        for _ in range(self.depth):
            if state.is_terminal():
                break
            legal = state.legal_actions()
            if not legal:
                break

            action = self._select(node, legal)
            key = repr(action)
            for other in legal:                       # availability bookkeeping
                other_key = repr(other)
                node.availability[other_key] = node.availability.get(other_key, 0) + 1
            child = node.children.setdefault(key, Node())
            path.append((child, key))

            try:
                state.apply(action)
            except Exception:
                break
            node = child

        value = (
            state.returns()[me] if state.is_terminal()
            else evaluate(state, me, self.model)
        )
        root.visits += 1
        root.total_value += value
        for child, _ in path:
            child.visits += 1
            child.total_value += value

    # -- public API ---------------------------------------------------------

    def act(self, state: GameState) -> Action:
        legal = state.legal_actions()
        if not legal:
            raise ValueError("no legal actions -- state should have been terminal")
        if len(legal) == 1:
            return legal[0]

        me = state.current_player
        root = Node()
        for _ in range(self.iterations):
            self._iterate(root, determinize(state, me, self._rng), me)

        # Most-visited action, not highest value: visit count is the robust
        # choice, since a high mean over two visits is mostly noise.
        best, best_visits = None, -1
        for action in legal:
            child = root.children.get(repr(action))
            visits = child.visits if child else 0
            if visits > best_visits:
                best, best_visits = action, visits
        return best if best is not None else legal[0]

    def __call__(self, state: GameState) -> Action:
        return self.act(state)

    def reset(self) -> None:
        self._rng = random.Random(self.seed)
