"""The frozen agent-facing contract, verified against a reference implementation.

These tests pin the invariants `engine/state.py` will have to satisfy. They run
against the toy fixture today; when the Riftbound state exists it gets
parametrized in here alongside it, and any contract violation fails loudly
instead of surfacing as a mysterious ISMCTS bug months later.
"""

from __future__ import annotations

import copy

import pytest

from agents.random_agent import RandomAgent
from engine.interface import PLAYERS, Action, GameState, Observation
from tests.fixtures.toy_game import ToyAction, build_toy_state


def play_out(state, seed=0, max_steps=10_000):
    agent = RandomAgent(seed)
    steps = 0
    while not state.is_terminal():
        assert steps < max_steps, "game failed to terminate"
        state.apply(agent.act(state))
        steps += 1
    return state, steps


def test_toy_state_satisfies_the_protocols():
    state = build_toy_state(1)
    assert isinstance(state, GameState)
    assert isinstance(state.legal_actions()[0], Action)
    assert isinstance(state.observation(0), Observation)


def test_legal_actions_empty_iff_terminal():
    state = build_toy_state(7)
    while not state.is_terminal():
        assert state.legal_actions(), "non-terminal state must offer actions"
        state.apply(state.legal_actions()[0])
    assert state.legal_actions() == []


def test_legal_actions_are_canonically_ordered():
    """Unstable ordering silently destroys seeded reproducibility."""
    state = build_toy_state(3)
    actions = state.legal_actions()
    assert actions == sorted(actions)


def test_legal_actions_are_a_pure_function_of_state():
    state = build_toy_state(3)
    assert state.legal_actions() == state.legal_actions()


def test_legal_actions_contain_no_duplicates():
    """Duplicate actions make replay action reprs ambiguous."""
    state = build_toy_state(5)
    while not state.is_terminal():
        actions = state.legal_actions()
        reprs = [repr(a) for a in actions]
        assert len(set(reprs)) == len(reprs), f"duplicate action reprs: {reprs}"
        state.apply(actions[0])


def test_observation_hides_opponent_information():
    state = build_toy_state(11)
    obs = state.observation(0)
    assert obs.own_hand == tuple(state.hands[0])
    # The opponent's actual card values must not be recoverable.
    assert not hasattr(obs, "opponent_hand")
    assert obs.opponent_hand_size == len(state.hands[1])


def test_observation_serialization_is_stable_and_distinguishing():
    state = build_toy_state(11)
    assert state.observation(0).to_canonical_bytes() == state.observation(0).to_canonical_bytes()
    assert state.observation(0).to_canonical_bytes() != state.observation(1).to_canonical_bytes()


def test_observation_changes_when_state_changes():
    state = build_toy_state(11)
    before = state.observation(0).to_canonical_bytes()
    state.apply(state.legal_actions()[0])
    assert state.observation(0).to_canonical_bytes() != before


def test_returns_undefined_before_terminal():
    state = build_toy_state(2)
    with pytest.raises(ValueError):
        state.returns()


@pytest.mark.parametrize("seed", range(12))
def test_returns_are_zero_sum_and_well_formed(seed):
    state, _ = play_out(build_toy_state(seed), seed)
    returns = state.returns()
    assert len(returns) == len(PLAYERS)
    assert sum(returns) == pytest.approx(1.0)
    assert set(returns) <= {0.0, 0.5, 1.0}


def test_apply_rejects_action_on_terminal_state():
    state, _ = play_out(build_toy_state(4), 4)
    with pytest.raises(ValueError):
        state.apply(ToyAction("pass"))


def test_apply_rejects_illegal_action():
    state = build_toy_state(6)
    absent = next(v for v in range(1, 10) if v not in state.hands[0])
    with pytest.raises(ValueError):
        state.apply(ToyAction("play", absent))


def test_state_is_deep_copyable_and_copies_are_independent():
    """MCTS determinization clones states thousands of times per decision."""
    state = build_toy_state(9)
    clone = copy.deepcopy(state)
    clone.apply(clone.legal_actions()[0])
    assert state.observation(0).to_canonical_bytes() != clone.observation(0).to_canonical_bytes()
    assert state.scores == [0, 0]


def test_every_game_terminates_across_many_seeds():
    for seed in range(60):
        state, steps = play_out(build_toy_state(seed), seed)
        assert state.is_terminal()
        assert steps > 0
