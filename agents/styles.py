"""Aggro, Conservative and Objective baselines (`TCG_AI_BUILD.md` section 12).

Section 23 wants a *pool* of opponents, and the reason is positional rather
than competitive: three policies that maximise the same quantity visit the
same positions, so a model trained against them learns one game well and
nothing about the games it never sees.

Two designs were tried and measured before this one
---------------------------------------------------

**Re-weighted evaluators.** The obvious implementation is `GreedyAgent` under
a different weight vector. It barely worked: against the fitted baseline an
aggro lean changed 4.8% of decisions, a conservative lean 0.9%, an objective
lean 0.3%. Pushing the weights hard enough to diverge also cost real strength
-- an objective agent with the board features zeroed diverged on 78% of
decisions and fell from 0.93 to 0.62 against Random. A pool member that is
only different because it is worse teaches a model to beat a worse player.

**Feature tie-breaks on the plateau.** Measuring *why* explained it. Over
1,047 real decisions, 77.4% are exact ties at the top of the evaluator, mean
tie size 4.2 -- `GreedyAgent` is playing randomly on three quarters of its
decisions. That looked like free room for a style, so the second design ranked
the tied set by a style-specific function of the resulting position's
features.

It discriminated on **0 of 816 plateaus**, and the reason is structural rather
than unlucky: `Model.score` is a *linear* function of the 13 features, so two
actions scoring exactly equal almost always got there by producing exactly
equal features. Checked directly: on 398 plateaus, **100% consisted of actions
whose resulting positions had identical feature vectors**. No re-weighting and
no feature-based tie-break can ever separate them. The evaluator is blind
between 4.2 actions, three quarters of the time, and that blindness is a
property of the feature set, not of the weights.

What these agents actually do
-----------------------------

A style is a preference over **action kinds**, applied among the actions the
evaluator scored equally. That is what aggro, conservative and objective mean
at the level a player would describe them: commit bodies and attack; hold
cards, develop runes and decline; go where the points are. It needs no feature
the evaluator lacks, and it costs no strength, because every action it chooses
among is one the evaluator called equal.

The mild weight leans are kept as well, so the styles differ a little in what
they consider best and a lot in what they do when the evaluator cannot tell.
`Model.fitted` stays False on all three: these are priors, and nothing
downstream should mistake a guess for a measurement.

Each agent's tie-break RNG is seeded from its style as well as the caller's
seed. Without that, two styles whose preferences happen not to separate a
plateau fall through to identical RNG streams and make identical picks -- an
artefact that made a first measurement of pairwise disagreement read 1.1% when
the agents were in fact both choosing at random.
"""

from __future__ import annotations

import copy
import random

from analysis.evaluation import FEATURE_NAMES, Model, evaluate, load_model
from engine.interface import Action, GameState

# Multipliers on the baseline weight vector; an absent feature stays at 1.0.
# Kept mild on purpose -- see the module docstring. The heavy lifting is done
# by the tie-breaks below.
_LEANS: dict[str, dict[str, float]] = {
    "aggro": {
        "might_diff": 3.0,
        "unit_count_diff": 2.5,
        "bf_presence_diff": 2.0,
        "hand_diff": 0.3,
        "deck_diff": 0.3,
        "answer_risk": 0.3,
    },
    "conservative": {
        "hand_diff": 4.0,
        "deck_diff": 3.0,
        "answer_risk": 3.0,
        "rune_diff": 2.5,
        "might_diff": 0.5,
        "unit_count_diff": 0.5,
    },
    "objective": {
        "battlefield_diff": 3.0,
        "takeover_edge": 3.0,
        "point_progress": 2.0,
        "might_diff": 0.5,
        "unit_count_diff": 0.5,
    },
}

# The secondary key, applied only among actions the evaluator scored equally.
# Each maps an action to a rank, higher first. The ranks are a preference over
# what a player *does*, not over what the position looks like afterwards --
# see the docstring for why the latter is provably impossible here.
#
# `kind` is the action's class name; `subject` is "unit", "spell", "gear",
# "rune" or "" for the card it names, and `toward` is "battlefield", "base" or
# "" for where it sends something.
_AGGRO_RANKS = {
    ("PlayCard", "unit"): 10,
    ("StandardMove", "battlefield"): 9,
    ("AssignDamageTo", ""): 8,
    ("PlayCard", "spell"): 7,
    ("PlayCard", "gear"): 6,
    ("ActivateAbility", ""): 5,
    ("ChannelRune", ""): 3,
    ("ExhaustRuneForEnergy", ""): 2,
    ("RecycleRuneForPower", ""): 2,
    ("StandardMove", "base"): 1,
    ("HideCard", ""): 1,
    ("PassPhase", ""): 0,
}
_CONSERVATIVE_RANKS = {
    ("ChannelRune", ""): 10,
    ("PassPhase", ""): 8,
    ("HideCard", ""): 7,
    ("ExhaustRuneForEnergy", ""): 6,
    ("StandardMove", "base"): 6,
    ("ActivateAbility", ""): 5,
    ("AssignDamageTo", ""): 5,
    ("PlayCard", "gear"): 4,
    ("PlayCard", "unit"): 4,
    # 164.2.b spends the rune permanently, which is the least conservative
    # way to make power.
    ("RecycleRuneForPower", ""): 2,
    ("PlayCard", "spell"): 2,
    ("StandardMove", "battlefield"): 1,
}
_OBJECTIVE_RANKS = {
    ("StandardMove", "battlefield"): 10,
    ("PlayCard", "unit"): 7,
    ("AssignDamageTo", ""): 6,
    ("ActivateAbility", ""): 5,
    ("PlayCard", "spell"): 5,
    ("PlayCard", "gear"): 4,
    ("ChannelRune", ""): 4,
    ("ExhaustRuneForEnergy", ""): 3,
    ("RecycleRuneForPower", ""): 3,
    ("PassPhase", ""): 2,
    ("StandardMove", "base"): 1,
    ("HideCard", ""): 1,
}

_TIE_BREAKS = {
    "aggro": _AGGRO_RANKS,
    "conservative": _CONSERVATIVE_RANKS,
    "objective": _OBJECTIVE_RANKS,
}

# The default for an action kind no table mentions -- mid-range, so an action
# type added to the engine later is neither chased nor avoided until someone
# decides where it belongs.
_UNRANKED = 4


def _describe(action, obs) -> tuple[str, str]:
    """(kind, qualifier) for an action, read from the observation only."""
    kind = type(action).__name__
    if kind == "StandardMove":
        return kind, "base" if action.destination == "base" else "battlefield"
    instance_id = getattr(action, "instance_id", None)
    if instance_id is None:
        return kind, ""
    if kind == "PlayCard":
        for card in obs.hand:
            if card.instance_id == instance_id:
                return kind, card.type
        return kind, ""
    return kind, ""


def _leaned_model(style: str) -> Model:
    """The baseline vector, scaled feature by feature.

    Scaling rather than replacing means a later refit of the baseline carries
    through, instead of leaving these three frozen against a model that moved.
    """
    base = load_model()
    lean = _LEANS[style]
    return Model(
        weights=tuple(
            weight * lean.get(name, 1.0)
            for weight, name in zip(base.weights, FEATURE_NAMES)
        ),
        bias=base.bias,
        fitted=False,
    )


class _StyleAgent:
    """One-ply search on the leaned evaluator, with a style tie-break.

    Sees only the frozen interface plus the evaluator, exactly like
    `GreedyAgent`. It does not know what a card is beyond the type the
    observation already tells it.
    """

    style = ""

    def __init__(self, seed: int, epsilon: float = 0.0) -> None:
        self.seed = seed
        # Seeded from the style as well, so two styles that fail to separate a
        # plateau still explore it differently instead of making identical
        # "random" picks. Deterministic per (style, seed), which is the
        # project's floor.
        self._rng = random.Random(f"{self.style}:{seed}")
        self.model = _leaned_model(self.style)
        self._ranks = _TIE_BREAKS[self.style]
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
        best_score: float | None = None
        best: list[Action] = []
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
        if len(best) == 1:
            return best[0]

        # The plateau: the evaluator cannot tell these apart. This is where
        # the style lives.
        obs = state.observation(me)
        ranked = [(self._rank(action, obs), action) for action in best]
        top = max(rank for rank, _ in ranked)
        tied = [action for rank, action in ranked if rank == top]
        return tied[self._rng.randrange(len(tied))]

    def _rank(self, action: Action, obs) -> int:
        kind, qualifier = _describe(action, obs)
        if (kind, qualifier) in self._ranks:
            return self._ranks[(kind, qualifier)]
        return self._ranks.get((kind, ""), _UNRANKED)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(seed={self.seed})"


class AggroAgent(_StyleAgent):
    """Prefers board pressure whenever the evaluator is indifferent."""

    style = "aggro"


class ConservativeAgent(_StyleAgent):
    """Prefers keeping cards, deck and resources; avoids overextending."""

    style = "conservative"


class ObjectiveAgent(_StyleAgent):
    """Prefers battlefield control and progress toward the Victory Score."""

    style = "objective"


STYLES: dict[str, type[_StyleAgent]] = {
    "aggro": AggroAgent,
    "conservative": ConservativeAgent,
    "objective": ObjectiveAgent,
}
