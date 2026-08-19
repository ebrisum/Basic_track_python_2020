"""Buff counters (426, 701-705).

A Buff is not "+N Might". It is a **counter**, capped at one per unit, worth
exactly +1 Might, removed when the unit leaves play, spendable as a cost, and
queryable as a status. 53 cards in the pool reference it -- "While I'm
buffed", "for each buffed friendly unit", "spend a buff to...", "when you buff
me" -- and the set even prints a rules-reminder card, OGN-357, whose entire
text is "A unit may have no more than one buff at a time."

The DSL modelled it as an unbounded Might modifier, which is a different
mechanic wearing the same name.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    return state


def put_unit(state, player, card_id="OGN-142", location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return state.cards[instance_id]


# --- 426.1.b / 702.3: one at a time -----------------------------------------


def test_buffing_places_a_counter(decks):
    """426.1.b -- "place a Buff Counter on it if it does not have one already"."""
    state = arena(decks)
    unit = put_unit(state, 0)
    assert unit.buffs == 0
    assert state.buff(unit) is True
    assert unit.buffs == 1


def test_a_unit_cannot_hold_two_buffs(decks):
    """702.3 / 426.1.b.1, and OGN-357, whose whole text is this rule."""
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    assert state.buff(unit) is False, "the second buff is not placed"
    assert unit.buffs == 1


def test_buff_returns_whether_it_actually_landed(decks):
    """426.1.c -- "Units with Buff Counters can still be chosen for actions
    that Buff units, but will not be Buffed as part of the execution."

    A spell reading "Buff a unit. Then, if it was buffed this way, draw a
    card" must not draw when the chosen unit was already buffed, and "when you
    buff me" must not trigger. Both need the *return value*, which is why
    `buff()` reports rather than just acting.
    """
    state = arena(decks)
    unit = put_unit(state, 0)
    assert state.buff(unit) is True
    assert state.buff(unit) is False


def test_permission_can_lift_the_cap(decks):
    """426.1.b.2 -- "Some effects may grant a Game Object permission to be
    Buffed multiple times. Such an effect ignores this restriction." Modelled
    as a count rather than a flag so that stays expressible."""
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    assert state.buff(unit, ignore_cap=True) is True
    assert unit.buffs == 2


# --- 703: exactly +1 Might each ---------------------------------------------


def test_a_buff_is_worth_exactly_one_might(decks):
    """703 -- "Each Buff individually contributes +1 Might to a Unit"."""
    state = arena(decks)
    unit = put_unit(state, 0)
    before = state.might_of(unit)
    state.buff(unit)
    assert state.might_of(unit) == before + 1


def test_a_buff_is_not_a_this_turn_effect(decks):
    """A buff is a counter, not a duration-scoped modifier: it survives the
    end of the turn, where "+1 Might this turn" does not."""
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    unit.might_this_turn += 3

    state._phase_ending()

    assert unit.buffs == 1, "the counter survives"
    assert unit.might_this_turn == 0, "the this-turn modifier does not"


# --- 702.2.b: spending ------------------------------------------------------


def test_spending_removes_one_buff(decks):
    """702.2.b -- "Spending a Buff removes a single Buff counter"."""
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    assert state.spend_buff(unit) is True
    assert unit.buffs == 0


def test_a_buff_cannot_be_spent_from_an_unbuffed_unit(decks):
    """702.2.b.1."""
    state = arena(decks)
    unit = put_unit(state, 0)
    assert state.spend_buff(unit) is False


def test_a_player_can_only_spend_buffs_on_units_they_control(decks):
    """702.2.b.2."""
    state = arena(decks)
    theirs = put_unit(state, 1)
    state.buff(theirs)
    assert state.spend_buff(theirs, spender=0) is False
    assert theirs.buffs == 1
    assert state.spend_buff(theirs, spender=1) is True


# --- 705: buffs do not survive leaving play ---------------------------------


def test_buffs_are_removed_when_a_unit_leaves_play(decks):
    """705 -- "If a Unit leaves play, remove all Buffs from it." 124.1 says
    the same for every temporary modification."""
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    state.leave_board(unit)
    assert unit.buffs == 0


def test_a_killed_unit_does_not_carry_a_buff_to_the_trash(decks):
    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    state._kill(unit)
    assert unit.buffs == 0


# --- the invariant ----------------------------------------------------------


def test_the_checker_catches_an_over_buffed_unit(decks):
    """A unit holding two buffs with no permission granted is a state 702.3
    forbids, so the structural checker should say so."""
    from engine.invariants import check

    state = arena(decks)
    unit = put_unit(state, 0)
    unit.buffs = 2
    assert any("buff" in problem.lower() for problem in check(state))


def test_a_single_buff_is_not_flagged(decks):
    from engine.invariants import check

    state = arena(decks)
    unit = put_unit(state, 0)
    state.buff(unit)
    assert not any("buff" in problem.lower() for problem in check(state))
