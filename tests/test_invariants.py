"""Structural truths, asserted after every action of whole games.

A rules engine can be wrong in ways no single test notices: a card in two
zones at once, a designation outliving its combat, a deck that quietly gains a
card. Those do not announce themselves -- they show up as a strange win rate
ten thousand games later, by which point the position that caused it is gone.

So the invariants are checked *continuously*, over games played by real
agents, and a violation names the action that produced it.

This found a live bug on its first run: `ReturnToHand` took a unit off the
board without detaching its Equipment, leaving a gear attached to a card in a
hand (719.5).
"""

from __future__ import annotations

import pytest

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from analysis.evaluation import load_model
from cards.database import load as load_db
from engine.invariants import assert_ok, check
from engine.setup import available_decks, build_state, load_deck

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return [load_deck(slug) for slug in available_decks()]


def test_a_fresh_state_is_consistent(decks):
    assert check(build_state(decks[0], decks[1], seed=1, db=DB)) == []


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5])
def test_random_games_never_violate_an_invariant(decks, seed):
    """Random play reaches positions no hand-written test would think to
    build -- which is the point of fuzzing the rules rather than sampling
    them."""
    d0 = decks[seed % len(decks)]
    d1 = decks[(seed + 1) % len(decks)]
    state = build_state(d0, d1, seed=seed, db=DB, validate_decks=False)
    agent = RandomAgent(seed)
    for step in range(400):
        if state.is_terminal():
            break
        action = agent.act(state)
        state.apply(action)
        assert_ok(state, f"seed {seed} step {step}: {action!r}")


@pytest.mark.parametrize("seed", [11, 12])
def test_greedy_games_never_violate_an_invariant(decks, seed):
    """Greedy play reaches *different* positions from random -- it actually
    contests battlefields and fights, so it exercises combat and attachment
    where random play mostly passes."""
    model = load_model()
    d0 = decks[seed % len(decks)]
    d1 = decks[(seed + 1) % len(decks)]
    state = build_state(d0, d1, seed=seed, db=DB, validate_decks=False)
    agents = (GreedyAgent(seed, model), GreedyAgent(seed + 1, model))
    for step in range(400):
        if state.is_terminal():
            break
        action = agents[state.current_player].act(state)
        state.apply(action)
        assert_ok(state, f"seed {seed} step {step}: {action!r}")


def test_the_checker_actually_catches_a_broken_state(decks):
    """A checker that never fires is indistinguishable from no checker."""
    state = build_state(decks[0], decks[1], seed=2, db=DB)
    assert check(state) == []

    # Put one card in two zones at once.
    stray = state.players[0].main_deck[0]
    state.players[0].hand.append(stray)
    assert any("both" in problem for problem in check(state))


def test_the_checker_catches_an_attachment_to_a_card_off_the_board(decks):
    """The exact shape of the bug it found: a gear attached to a card that
    has left play (719.5)."""
    state = build_state(decks[0], decks[1], seed=2, db=DB)
    gear, host = list(state.cards)[:2]
    state.cards[gear].location = "base"
    state.players[state.cards[gear].controller].base.append(gear)
    state.cards[gear].attached_to = host
    state.cards[host].location = None
    assert any("not on the board" in problem for problem in check(state))
