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


def test_a_finished_game_may_leave_a_facedown_card_stranded(decks):
    """323.1 vs 323.7 -- removing a stranded Hidden card is cleanup *step 5*,
    and winning is *step 1*. The cleanup that ends the game stops at step 1, so
    the later steps never run and a finished position can legitimately show a
    facedown card at a battlefield its controller has lost.

    Found by the checker firing on the last action of a 299-step game. The
    engine was right; the invariant was over-broad.
    """
    from engine.actions import HideCard
    from engine.state import Phase
    from engine.zones import CardRef

    state = build_state(decks[0], decks[1], seed=3, db=DB)
    agent = RandomAgent(3)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))

    player = state.turn_player
    card_id = next(c.card_id for c in DB.cards.values()
                   if c.type in ("unit", "spell", "gear") and c.has_hidden)
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    state.players[player].pool.universal_power += 1
    state.battlefields[0].controller = player
    state.apply(HideCard(instance_id, 0))

    state.battlefields[0].controller = state.opponent(player)
    assert any("does not control" in p for p in check(state)), (
        "mid-game, a stranded facedown card is a violation"
    )

    state.phase = Phase.GAME_OVER
    assert not any("does not control" in p for p in check(state)), (
        "once the game is over, step 5 never runs (323.1)"
    )
