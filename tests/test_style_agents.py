"""`TCG_AI_BUILD.md` section 12 -- Aggro, Conservative and Objective baselines.

Section 23 wants these in the opponent pool, and the reason is positional
rather than competitive: three policies that all maximise the same thing visit
the same positions, and a model trained against them learns one game. These
three maximise *different* things, so they walk into different board states.

Each test here asks the only question worth asking of a baseline: does it
actually behave differently, on real positions, in the direction its name
claims? A "style" agent that plays like Greedy is a name, not a baseline.
"""

from __future__ import annotations

import random

import pytest

from agents.styles import AggroAgent, ConservativeAgent, ObjectiveAgent, STYLES
from engine.actions import ChannelRune, PassPhase, StandardMove
from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from analysis.evaluation import FEATURE_NAMES
from analysis.validate import build_state, load_db, load_deck
from engine.replay import state_hash


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


def _state(seed: int):
    db = load_db()
    return build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)


def _play_out(agents, seed: int, cap: int = 3000):
    state = _state(seed)
    steps = 0
    while not state.is_terminal() and steps < cap:
        legal = state.legal_actions()
        if not legal:
            break
        action = agents[state.current_player].act(state)
        assert action in legal, f"{agents[state.current_player]} played illegally"
        state.apply(action)
        steps += 1
    return state, steps


@pytest.mark.parametrize("cls", [AggroAgent, ConservativeAgent, ObjectiveAgent])
def test_a_style_agent_finishes_a_game_legally(cls):
    state, steps = _play_out([cls(1), RandomAgent(2)], seed=5)
    assert steps > 0
    assert state.is_terminal() or steps >= 3000


@pytest.mark.parametrize("cls", [AggroAgent, ConservativeAgent, ObjectiveAgent])
def test_a_style_agent_is_deterministic_under_a_seed(cls):
    """Determinism is the project's floor, and a tie-break RNG is easy to leak."""
    first, first_steps = _play_out([cls(7), RandomAgent(8)], seed=11)
    second, second_steps = _play_out([cls(7), RandomAgent(8)], seed=11)
    assert first_steps == second_steps
    # The project's own determinism idiom: both players' observations, hashed.
    assert state_hash(first) == state_hash(second)


def test_the_styles_weight_different_features():
    """Named differently is not enough: the weight vectors must differ."""
    vectors = {name: agent_cls(0).model.weights for name, agent_cls in STYLES.items()}
    assert len(set(vectors.values())) == len(vectors)
    for name, weights in vectors.items():
        assert len(weights) == len(FEATURE_NAMES), name


def test_each_style_leans_where_its_name_says():
    """The direction is asserted against the shared baseline, not in isolation.

    A vector can differ from Greedy's in a hundred ways; what makes it *aggro*
    is that the board-pressure features outrank the attrition ones by more than
    they do in the baseline.
    """
    base = dict(zip(FEATURE_NAMES, GreedyAgent(0).model.weights))
    aggro = dict(zip(FEATURE_NAMES, AggroAgent(0).model.weights))
    careful = dict(zip(FEATURE_NAMES, ConservativeAgent(0).model.weights))
    objective = dict(zip(FEATURE_NAMES, ObjectiveAgent(0).model.weights))

    def pressure(w):
        return w["might_diff"] + w["unit_count_diff"] + w["bf_presence_diff"]

    def attrition(w):
        return w["hand_diff"] + w["deck_diff"] + w["answer_risk"]

    def scoring(w):
        return w["battlefield_diff"] + w["point_progress"] + w["takeover_edge"]

    assert pressure(aggro) - attrition(aggro) > pressure(base) - attrition(base)
    assert attrition(careful) - pressure(careful) > attrition(base) - pressure(base)
    assert scoring(objective) - pressure(objective) > scoring(base) - pressure(base)


def test_the_action_ranks_disagree_about_what_to_do():
    """The tie-break is where the style lives, so the tables must differ.

    Asserted on the orderings rather than the raw numbers: what matters is
    that aggro puts committing a unit above declining, and conservative puts
    declining above committing.
    """
    aggro, careful, objective = (
        AggroAgent(0), ConservativeAgent(0), ObjectiveAgent(0)
    )

    class _Obs:
        hand = ()

    obs = _Obs()
    commit = StandardMove(instance_id=1, destination="bf:0")
    decline = PassPhase()
    develop = ChannelRune()

    assert aggro._rank(commit, obs) > aggro._rank(decline, obs)
    assert careful._rank(decline, obs) > careful._rank(commit, obs)
    assert careful._rank(develop, obs) > careful._rank(commit, obs)
    assert objective._rank(commit, obs) > objective._rank(develop, obs)


def test_a_feature_tie_break_could_not_have_worked():
    """The measurement that forced this design, kept as a regression.

    `Model.score` is linear in the 13 features, so two actions that tie
    exactly on score got there by producing identical feature vectors. A
    style ranking the tied set by any function of those features would
    discriminate on none of them. If a future evaluator breaks this -- a
    nonlinear head, or features fine enough to separate tied actions -- this
    test fails, and a feature tie-break becomes worth reconsidering.
    """
    import copy

    from analysis.evaluation import evaluate, features, load_model

    model = load_model()
    state = _state(0)
    rng = random.Random(0)
    plateaus = identical = 0
    for _ in range(120):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        if len(legal) > 1:
            me = state.current_player
            best, rows = None, []
            for action in legal:
                clone = copy.deepcopy(state)
                try:
                    clone.apply(action)
                except Exception:
                    continue
                score = evaluate(clone, me, model)
                row = (
                    None if clone.is_terminal()
                    else tuple(features(clone, me)[n] for n in FEATURE_NAMES)
                )
                if best is None or score > best:
                    best, rows = score, [row]
                elif score == best:
                    rows.append(row)
            if len(rows) > 1:
                plateaus += 1
                if len(set(rows)) == 1:
                    identical += 1
        state.apply(rng.choice(legal))
    assert plateaus >= 20, "not enough plateaus sampled to say anything"
    assert identical == plateaus, (
        f"{plateaus - identical} of {plateaus} plateaus held actions the "
        f"features can tell apart -- a feature tie-break is viable again"
    )


def test_the_styles_actually_diverge_on_real_positions():
    """The point of the pool: different agents pick different actions.

    Measured rather than asserted -- if two styles agree on almost every
    decision, the pool has one member wearing three hats and section 23 gets
    nothing from it.
    """
    state = _state(4)
    agents = {name: cls(3) for name, cls in STYLES.items()}
    rng = random.Random(4)
    disagreements = {name: 0 for name in agents}
    decisions = 0
    for _ in range(400):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        if len(legal) > 1:
            decisions += 1
            baseline = GreedyAgent(3).act(state)
            for name, agent in agents.items():
                if agent.act(state) != baseline:
                    disagreements[name] += 1
        state.apply(rng.choice(legal))
    assert decisions >= 50, "not enough real decisions to measure divergence"
    # A floor, not "any difference at all". The first implementation of these
    # agents -- weight leans with no tie-break -- diverged on 4.8%, 0.9% and
    # 0.3% of decisions and would have passed a "> 0" assertion while giving
    # section 23's opponent pool essentially nothing. Measured divergence on
    # the two starter decks is 55.2% / 54.7% / 54.0% averaged over three
    # seeds, and 32.5% on seed 4 alone -- the spread between seeds is why the
    # floor is 25% rather than something close to the mean.
    for name, count in disagreements.items():
        share = count / decisions
        assert share >= 0.25, (
            f"{name} differed from Greedy on only {share:.1%} of {decisions} "
            f"decisions -- it is the baseline wearing a hat"
        )


def test_the_styles_disagree_with_each_other_too():
    """Diverging from Greedy is not enough if all three diverge identically."""
    state = _state(0)
    agents = {name: cls(3) for name, cls in STYLES.items()}
    names = list(agents)
    rng = random.Random(0)
    pairs = {(a, b): 0 for i, a in enumerate(names) for b in names[i + 1:]}
    decisions = 0
    for _ in range(250):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        if len(legal) > 1:
            decisions += 1
            picks = {name: agent.act(state) for name, agent in agents.items()}
            for (a, b) in pairs:
                if picks[a] != picks[b]:
                    pairs[(a, b)] += 1
        state.apply(rng.choice(legal))
    assert decisions >= 50
    for (a, b), count in pairs.items():
        assert count / decisions >= 0.15, (
            f"{a} and {b} agree on {1 - count / decisions:.1%} of decisions"
        )
