"""Determinism: same seed + same policies = byte-identical game.

The brief calls this non-negotiable -- it is how everything else gets
debugged. These tests fail the moment a global RNG, a set iteration, or a
dict-ordering assumption creeps into the engine.
"""

from __future__ import annotations

import copy
import random

import pytest

from agents.random_agent import RandomAgent
from analysis.batch import play_game, run_batch
from engine.replay import state_hash
from tests.fixtures.toy_game import build_toy_state


def trace(seed: int, agent_seed: int | None = None) -> list[str]:
    """Full action + state-hash trace of one game."""
    agent = RandomAgent(seed if agent_seed is None else agent_seed)
    state = build_toy_state(seed)
    out = [state_hash(state)]
    while not state.is_terminal():
        action = agent.act(state)
        out.append(repr(action))
        state.apply(action)
        out.append(state_hash(state))
    return out


@pytest.mark.parametrize("seed", range(10))
def test_same_seed_reproduces_identical_game(seed):
    assert trace(seed) == trace(seed)


def test_different_seeds_produce_different_games():
    """Guards against a seed that is silently ignored."""
    traces = {tuple(trace(s)) for s in range(12)}
    assert len(traces) > 1


def test_initial_deal_is_seed_determined():
    a, b = build_toy_state(42), build_toy_state(42)
    assert a.hands == b.hands
    assert a.decks == b.decks
    assert build_toy_state(42).hands != build_toy_state(43).hands


def test_global_rng_state_does_not_affect_games():
    """The engine and agents must never touch random's global generator."""
    random.seed(1)
    first = trace(5)
    random.seed(999)
    [random.random() for _ in range(50)]
    assert trace(5) == first


def test_agent_reset_restores_the_sequence():
    agent = RandomAgent(17)
    state = build_toy_state(17)
    first = [repr(agent.act(copy.deepcopy(state))) for _ in range(5)]
    agent.reset()
    assert [repr(agent.act(copy.deepcopy(state))) for _ in range(5)] == first


def test_batch_is_reproducible():
    factories = (lambda s: RandomAgent(s), lambda s: RandomAgent(s + 1000))
    a = run_batch(build_toy_state, factories, games=25, base_seed=100)
    b = run_batch(build_toy_state, factories, games=25, base_seed=100)
    assert [(r.seed, r.returns, r.turns) for r in a.results] == [
        (r.seed, r.returns, r.turns) for r in b.results
    ]


def test_single_game_reproduces_standalone_from_its_batch_seed():
    """An outlier must be investigable without replaying the whole batch."""
    factories = (lambda s: RandomAgent(s), lambda s: RandomAgent(s + 1000))
    batch = run_batch(build_toy_state, factories, games=10, base_seed=500)
    target = batch.results[7]
    standalone = play_game(
        build_toy_state,
        (factories[0](target.seed), factories[1](target.seed)),
        target.seed,
    )
    assert standalone == target
