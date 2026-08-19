"""Deflect (809), and the order of the play steps (354-359).

809.1.c: "Spells and abilities an opponent controls that target [me] cost an
amount of Power equal to [Deflect Value] more to play as an additional cost
for each time they choose [me]."

It is a **mandatory additional cost priced off the declared target**, so it
only became implementable once 355.8 made targets known at play time (RQ-19),
and only works if the play steps run in the printed order:

    354 move to chain -> 355 make choices -> 356 total cost -> 357 pay

The engine used to pay first and target second, which is why Deflect had
nowhere to attach.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import ChooseTarget, PlayCard
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
RAY = "OGN-009"           # Hextech Ray -- "Deal 3 to a unit at a battlefield"
DEFLECT_2 = "OGN-041"     # Volibear, Furious -- Deflect 2
PLAIN = "OGN-142"         # Mountain Drake -- no Deflect


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    return state


def give(state, player, card_id):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    return instance_id


def put_unit(state, player, card_id, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def fund(state, player, energy=30, power=30):
    state.players[player].pool.energy = energy
    state.players[player].pool.universal_power = power


# --- 809.1.c: the tax ------------------------------------------------------


def test_targeting_a_deflect_unit_costs_extra_power(decks):
    """809.1.c -- an opponent's spell that targets it costs Deflect Value more
    Power. 809.1.b.2 calls that value the Deflect Value; Volibear's is 2."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    fund(state, caster)
    guarded = put_unit(state, foe, DEFLECT_2, bf_location(0))

    before = state.players[caster].pool.total_power()
    state.apply(PlayCard(give(state, caster, RAY)))
    spent = before - state.players[caster].pool.total_power()

    plain_cost = DB[RAY].power
    assert spent == plain_cost + 2, f"expected {plain_cost}+2 power, spent {spent}"


def test_a_unit_without_deflect_costs_nothing_extra(decks):
    """The counterweight."""
    state = arena(decks)
    caster = state.turn_player
    fund(state, caster)
    put_unit(state, state.opponent(caster), PLAIN, bf_location(0))

    before = state.players[caster].pool.total_power()
    state.apply(PlayCard(give(state, caster, RAY)))
    spent = before - state.players[caster].pool.total_power()

    assert spent == DB[RAY].power


def test_deflect_does_not_tax_its_own_controller(decks):
    """809.1.c -- "Spells and abilities **an opponent** controls". Your own
    Deflect unit does not tax you."""
    state = arena(decks)
    caster = state.turn_player
    fund(state, caster)
    put_unit(state, caster, DEFLECT_2, bf_location(0))

    before = state.players[caster].pool.total_power()
    state.apply(PlayCard(give(state, caster, RAY)))
    spent = before - state.players[caster].pool.total_power()

    assert spent == DB[RAY].power


def test_the_deflect_power_may_be_of_any_domain(decks):
    """809.1.c.1 -- "The Power used to pay this cost may always be of any
    Domain." Paid here from universal power with no domained power at all."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    state.players[caster].pool.energy = 30
    state.players[caster].pool.universal_power = 30
    state.players[caster].pool.power = {}
    put_unit(state, foe, DEFLECT_2, bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))
    assert state.chain, "the spell was played despite holding no domained power"


def test_a_spell_that_cannot_afford_the_deflect_is_not_playable(decks):
    """358 Check legality -- a cost that cannot be paid bars the play. With
    only the base cost available and a Deflect 2 unit as the sole target,
    there is no affordable line."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    state.players[caster].pool.energy = 30
    state.players[caster].pool.universal_power = DB[RAY].power   # exact base
    put_unit(state, foe, DEFLECT_2, bf_location(0))

    ray = give(state, caster, RAY)
    assert PlayCard(ray) not in state.legal_actions()


def test_an_affordable_target_keeps_the_spell_playable(decks):
    """The other side: with a cheaper target available the spell is legal, and
    the taxed one must not be offered when it cannot be paid for."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    state.players[caster].pool.energy = 30
    state.players[caster].pool.universal_power = DB[RAY].power
    guarded = put_unit(state, foe, DEFLECT_2, bf_location(0))
    plain = put_unit(state, foe, PLAIN, bf_location(0))

    ray = give(state, caster, RAY)
    assert PlayCard(ray) in state.legal_actions()

    state.apply(PlayCard(ray))
    if state.phase is Phase.CHOOSING:
        offered = {a.instance_id for a in state.legal_actions()}
        assert offered == {plain}, "an unaffordable target was offered"


# --- 354-357: the printed order --------------------------------------------


def test_the_card_reaches_the_chain_before_costs_are_paid(decks):
    """354 is step 1 and 357 is step 4. The card is on the chain, Pending,
    while its targets are chosen -- which is what gives Deflect something to
    price against."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    fund(state, caster)
    put_unit(state, foe, PLAIN, bf_location(0))
    put_unit(state, foe, PLAIN, bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))

    assert state.phase is Phase.CHOOSING
    assert state.chain and state.chain[-1].pending, "on the chain and Pending"
