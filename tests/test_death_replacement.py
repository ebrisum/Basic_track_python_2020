"""Death replacement effects (367-373).

370.1.a.1: "A unit's death being replaced by Zhonya's Hourglass is the same as
the kill action that caused that death **not occurring**." So a replaced death
is not a death: nothing that triggers on dying triggers, and the unit does not
reach the trash.

14 cards in the pool use "instead", and the death family is the one the Core
Rules keep reaching for as their worked example (370.1.a.1, 373.1.a, 373.2,
383, 808.1.d.1 all cite Zhonya's Hourglass or Guardian Angel). One of them --
OGN-023 Unlicensed Armory -- is in `volibear_body_fury`, so it is the last gap
in a deck the engine actually measures.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.dsl import ReplaceDeath, Selector
from cards.primitives import EffectContext, execute
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
UNIT = "OGN-142"


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    for player in state.players:
        player.pool.energy = 30
        player.pool.universal_power = 30
    return state


def put_unit(state, player, card_id=UNIT, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def arm(state, unit, **kwargs):
    execute(state, ReplaceDeath(selector=Selector(scope="choose", type="unit"),
                                **kwargs),
            EffectContext(controller=state.cards[unit].controller,
                          chosen=(unit,)))


# --- 370.1.a.1: the kill does not happen ------------------------------------


def test_a_replaced_death_recalls_instead_of_killing(decks):
    """OGN-023's shape: "the next time it dies this turn ... recall it
    exhausted instead"."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    arm(state, unit, recall=True, exhaust=True)

    state._kill(state.cards[unit])

    assert unit not in state.players[0].trash, "the kill was not replaced"
    assert state.cards[unit].location == BASE_LOCATION
    assert state.cards[unit].exhausted is True


def test_a_replaced_death_is_not_a_death(decks):
    """370.1.a.1 -- the replacement is "the same as the kill action that
    caused that death not occurring". Damage is cleared by the recall, and the
    unit is still on the board."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    state.cards[unit].damage = 99
    arm(state, unit, recall=True, exhaust=True, heal=True)

    state._kill(state.cards[unit])

    assert state.cards[unit].location is not None
    assert state.cards[unit].damage == 0


def test_the_replacement_is_spent_after_one_use(decks):
    """"The **next** time it dies" -- once, not a standing ward."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    arm(state, unit, recall=True, exhaust=True)

    state._kill(state.cards[unit])
    assert unit not in state.players[0].trash

    state._kill(state.cards[unit])
    assert unit in state.players[0].trash, "the ward survived its one use"


def test_an_unarmed_unit_dies_normally(decks):
    """The counterweight."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    state._kill(state.cards[unit])
    assert unit in state.players[0].trash


# --- the cost ---------------------------------------------------------------


def test_a_replacement_with_an_unpayable_cost_does_not_apply(decks):
    """OGN-023 charges 1 Fury. A replacement whose cost cannot be paid simply
    does not apply, and the unit dies."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    arm(state, unit, recall=True, exhaust=True, cost_power="Fury")
    state.players[0].pool.universal_power = 0
    state.players[0].pool.power = {}

    state._kill(state.cards[unit])

    assert unit in state.players[0].trash


def test_a_payable_cost_is_actually_spent(decks):
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    arm(state, unit, recall=True, exhaust=True, cost_power="Fury")
    before = state.players[0].pool.total_power()

    state._kill(state.cards[unit])

    assert unit not in state.players[0].trash
    assert state.players[0].pool.total_power() == before - 1


# --- it expires with the turn ------------------------------------------------


def test_the_replacement_expires_at_end_of_turn(decks):
    """OGN-023 says "this turn", so an unused ward goes at step 3d with every
    other this-turn effect (317.2.c)."""
    state = arena(decks)
    unit = put_unit(state, 0, location=bf_location(0))
    arm(state, unit, recall=True, exhaust=True)

    state._phase_ending()
    state._kill(state.cards[unit])

    assert unit in state.players[0].trash


# --- a token cannot be saved -------------------------------------------------


def test_a_token_still_ceases_to_exist(decks):
    """186.1 -- a token put into a non-board zone ceases to exist. Recalling
    it instead keeps it on the board, which is the replacement working; but
    the engine must not leave a token in the trash either way."""
    from cards.tokens import RECRUIT

    state = arena(decks)
    instance = state.create_token(RECRUIT, 0, bf_location(0))
    arm(state, instance, recall=True, exhaust=True)

    state._kill(state.cards[instance])

    assert instance not in state.players[0].trash


# --- the actual card --------------------------------------------------------


def test_unlicensed_armory_wards_a_friendly_unit(decks):
    """OGN-023 -- the last card in `volibear_body_fury` that was not fully
    implemented. "Discard 1, Exhaust: Choose a friendly unit. The next time it
    dies this turn, you may pay 1 Fury to recall it exhausted instead."
    """
    from cards.scripts import activated_abilities

    state = arena(decks)
    player = state.turn_player
    armory = put_unit(state, player, "OGN-023", BASE_LOCATION)
    unit = put_unit(state, player, UNIT, bf_location(0))

    # Only one friendly unit is a legal choice, so the effect takes it without
    # asking; the Armory itself is gear, not a unit.
    ability = activated_abilities(DB["OGN-023"])[0]
    state._queue(ability, player, armory)
    state._resolve_effects()

    assert state.cards[unit].death_replacement is not None
    assert state.cards[armory].death_replacement is None

    # And it does what the card says: 1 Fury, recalled exhausted, not dead.
    before = state.players[player].pool.total_power()
    state._kill(state.cards[unit])

    assert unit not in state.players[player].trash
    assert state.cards[unit].location == BASE_LOCATION
    assert state.cards[unit].exhausted is True
    assert state.players[player].pool.total_power() == before - 1


def test_the_whole_deck_is_implemented(decks):
    """The point of the exercise: `volibear_body_fury` now has no card whose
    printed text the engine leaves inert, so a win rate measured on it
    reflects those cards."""
    from engine.setup import load_deck

    for name in ("jinx_chaos_fury", "volibear_body_fury"):
        deck = load_deck(name)
        inert = [cid for cid in sorted(set(list(deck.main) + [deck.champion]))
                 if not DB[cid].text_implemented]
        assert not inert, f"{name} still has inert cards: {inert}"
