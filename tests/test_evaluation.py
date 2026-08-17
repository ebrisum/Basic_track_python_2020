"""Tests for the evaluation heuristic and the agents that use it.

The heuristic is a *prior over winning*, never the reward. These tests pin the
properties that make it safe to search with, and the boundary that keeps it
from leaking into the definition of winning.
"""

from __future__ import annotations

import copy
import math

import pytest

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from analysis.evaluation import (
    FEATURE_NAMES,
    DEFAULT_WEIGHTS,
    Model,
    evaluate,
    explain,
    features,
    load_model,
    save_model,
)
from cards.database import load as load_db
from engine.setup import build_state, load_deck
from engine.state import VICTORY_SCORE, Phase

DB = load_db()
PRIOR = Model()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def midgame(decks, seed=1, steps=90):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    return state


# --- the boundary: reward vs heuristic --------------------------------------


def test_returns_is_untouched_by_the_heuristic(decks):
    """The only definition of winning stays win/loss/draw."""
    state = build_state(decks[0], decks[1], seed=2, db=DB)
    agent = RandomAgent(2)
    while not state.is_terminal():
        state.apply(agent.act(state))
    assert set(state.returns()) <= {0.0, 0.5, 1.0}
    assert sum(state.returns()) == pytest.approx(1.0)


def test_evaluate_defers_to_the_real_result_when_terminal(decks):
    state = build_state(decks[0], decks[1], seed=3, db=DB)
    agent = RandomAgent(3)
    while not state.is_terminal():
        state.apply(agent.act(state))
    for player in (0, 1):
        assert evaluate(state, player) == state.returns()[player]


# --- invariants search relies on --------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_evaluation_is_zero_sum(decks, seed):
    """Every feature is a difference, so the two views must sum to 1.

    Search negates a value for the opponent instead of recomputing it; a
    feature written from one player's point of view breaks that silently.
    """
    state = midgame(decks, seed)
    assert evaluate(state, 0) + evaluate(state, 1) == pytest.approx(1.0)


def test_features_are_antisymmetric(decks):
    state = midgame(decks, 4)
    mine, theirs = features(state, 0), features(state, 1)
    for name in FEATURE_NAMES:
        assert mine[name] == pytest.approx(-theirs[name]), name


def test_evaluate_is_bounded(decks):
    for seed in range(4):
        state = midgame(decks, seed)
        for player in (0, 1):
            assert 0.0 <= evaluate(state, player) <= 1.0


def test_an_even_position_scores_near_a_half(decks):
    """Setup is symmetric apart from the die roll, so it should read ~0.5."""
    state = build_state(decks[0], decks[1], seed=5, db=DB)
    assert evaluate(state, 0, PRIOR) == pytest.approx(0.5, abs=0.05)


# --- direction: more points must not read as worse --------------------------


def test_more_points_scores_higher(decks):
    state = midgame(decks, 6)
    before = evaluate(state, 0, PRIOR)
    state.players[0].points += 2
    assert evaluate(state, 0, PRIOR) > before


def test_the_opponents_points_score_lower(decks):
    state = midgame(decks, 7)
    before = evaluate(state, 0, PRIOR)
    state.players[1].points += 2
    assert evaluate(state, 0, PRIOR) < before


def test_controlling_a_battlefield_scores_higher(decks):
    state = midgame(decks, 8)
    for bf in state.battlefields:
        bf.controller = None
    before = evaluate(state, 0, PRIOR)
    state.battlefields[0].controller = 0
    assert evaluate(state, 0, PRIOR) > before


def test_point_progress_is_nonlinear_near_the_victory_score(decks):
    """471 -- the Victory Score is an absolute threshold, so the last points
    are worth more than the first."""
    state = midgame(decks, 9)
    state.players[0].points = state.players[1].points = 0
    low = features(state, 0)["point_progress"]
    state.players[0].points = 3
    mid = features(state, 0)["point_progress"]
    state.players[0].points = VICTORY_SCORE - 1
    high = features(state, 0)["point_progress"]
    assert (high - mid) > (mid - low)


# --- model plumbing ---------------------------------------------------------


def test_model_roundtrips_through_disk(tmp_path):
    model = Model(weights=tuple(range(len(FEATURE_NAMES))), bias=-0.5,
                  fitted=True, trained_on_games=42)
    path = tmp_path / "w.json"
    save_model(model, path)
    loaded = load_model(path)
    assert loaded.weights == model.weights
    assert loaded.bias == pytest.approx(-0.5)
    assert loaded.fitted and loaded.trained_on_games == 42


def test_missing_weights_file_falls_back_to_the_prior(tmp_path):
    loaded = load_model(tmp_path / "absent.json")
    assert loaded.weights == DEFAULT_WEIGHTS
    assert not loaded.fitted


def test_explain_accounts_for_the_whole_score(decks):
    state = midgame(decks, 10)
    rows = explain(state, 0, PRIOR)
    total = PRIOR.bias + sum(contribution for _, _, contribution in rows)
    assert 1 / (1 + math.exp(-total)) == pytest.approx(evaluate(state, 0, PRIOR))


# --- the greedy agent -------------------------------------------------------


def test_greedy_agent_only_returns_legal_actions(decks):
    state = build_state(decks[0], decks[1], seed=11, db=DB)
    agent = GreedyAgent(11, PRIOR)
    for _ in range(60):
        if state.is_terminal():
            break
        action = agent.act(state)
        assert action in state.legal_actions()
        state.apply(action)


def test_greedy_agent_is_deterministic(decks):
    def trace(seed):
        state = build_state(decks[0], decks[1], seed=seed, db=DB)
        agent = GreedyAgent(seed, PRIOR)
        out = []
        for _ in range(40):
            if state.is_terminal():
                break
            action = agent.act(state)
            out.append(repr(action))
            state.apply(action)
        return out

    assert trace(12) == trace(12)


def test_greedy_agent_does_not_mutate_the_state_it_is_given(decks):
    """It searches on clones; leaking a mutation would corrupt the real game."""
    state = midgame(decks, 13)
    before = state.observation(0).to_canonical_bytes()
    GreedyAgent(13, PRIOR).act(state)
    assert state.observation(0).to_canonical_bytes() == before


def test_greedy_agent_takes_a_winning_action_when_one_exists(decks):
    """The point of the whole exercise: given a move that wins, take it."""
    state = midgame(decks, 14)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(RandomAgent(14).act(state))
    if state.is_terminal():
        pytest.skip("game ended during setup")
    player = state.turn_player
    state._current_player = player
    # One point from victory, holding a battlefield: passing into the next
    # Beginning Phase scores by Hold and wins.
    state.players[player].points = VICTORY_SCORE - 1
    state.players[1 - player].points = 0
    for bf in state.battlefields:
        bf.controller = player
        bf.scored_by.clear()

    agent = GreedyAgent(14, PRIOR)
    for _ in range(80):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    assert state.is_terminal(), "greedy failed to close out a won position"
    assert state.winner == player


@pytest.mark.perf
def test_greedy_agent_finishes_a_game_in_reasonable_time(decks):
    """It deep-copies per candidate action, so it is far slower than random.
    This pins that it is usable at all, not that it is fast."""
    import time

    state = build_state(decks[0], decks[1], seed=15, db=DB)
    agent0, agent1 = GreedyAgent(15, PRIOR), RandomAgent(16)
    started = time.perf_counter()
    steps = 0
    while not state.is_terminal() and steps < 4000:
        state.apply((agent0 if state.current_player == 0 else agent1).act(state))
        steps += 1
    assert time.perf_counter() - started < 120.0
