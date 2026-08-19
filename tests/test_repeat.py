"""Repeat (820).

820.1.d: "You may pay [Cost] as an additional cost as you play this. If you
do, execute the instructions of this chain item one additional time during
resolution."

17 cards carry it. It was blocked until targets were declared at play time
(RQ-19), because 820.2 says the choices for the additional execution "must be
made at the usual time during the Make Relevant Choices step of Playing a
Card" -- and 820.2.a says they "do not have to be the same as the choices made
for the initial execution".

That last clause is what makes Repeat more than a loop: the chain item has to
carry **two independent sets of targets**.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import ChooseTarget, PassPhase, PlayCard
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
    for player in state.players:
        player.pool.energy = 30
        player.pool.universal_power = 30
    return state


def give(state, player, card_id):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    return instance_id


def put_unit(state, player, card_id="OGN-142", location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- 820.1.c: reading the cost off the card ---------------------------------


def test_repeat_costs_are_read_from_the_printed_keyword():
    """820.1.c -- "Repeat is formatted as 'Repeat [Cost]'". The pool prints it
    at least four ways: a bare number, a number and a comma-separated domain,
    a number and a bare domain, and no cost at all."""
    from cards.repeat import repeat_cost

    assert repeat_cost(DB["SFD-003"]) == (1, ())          # [REPEAT 1]
    assert repeat_cost(DB["SFD-023"]) == (2, ("Fury",))   # [REPEAT 2, Fury]
    assert repeat_cost(DB["SFD-077"]) == (4, ("Mind",))   # [REPEAT 4 Mind]


def test_a_card_without_repeat_has_no_repeat_cost():
    from cards.repeat import repeat_cost

    assert repeat_cost(DB["OGN-009"]) is None


def test_a_bare_repeat_is_reported_as_unreadable():
    """SFD-040 prints `[REPEAT]` with no cost, and SFD-078 grants "[REPEAT]
    equal to its cost" -- a value that only exists at play time. Neither can
    be read off the face, so neither is guessed at."""
    from cards.repeat import repeat_cost

    assert repeat_cost(DB["SFD-040"]) is None


# --- 820.1.d: the effect happens twice --------------------------------------


PIERCING_LIGHT = "SFD-023"      # [REPEAT 2, Fury] Deal 2 to a unit at a battlefield


@pytest.fixture
def piercing_light(monkeypatch):
    """Script SFD-023 as its printed text, so the real Repeat card is tested.

    None of the 17 Repeat cards is scripted yet, and a keyword tested only
    through a card that does not have it is not tested at all.
    """
    from cards import scripts
    from cards.dsl import Ability, CardScript, Deal, Selector, TriggerKind

    script = CardScript(
        card_id=PIERCING_LIGHT,
        abilities=(Ability(
            kind=TriggerKind.ON_RESOLVE,
            effects=(Deal(amount=2,
                          selector=Selector(scope="choose", type="unit",
                                            location="battlefield")),),
        ),),
    )
    scripts.registry.cache_clear()
    monkeypatch.setattr(scripts, "ALL_SCRIPTS", scripts.ALL_SCRIPTS + (script,))
    scripts.registry.cache_clear()
    yield PIERCING_LIGHT
    scripts.registry.cache_clear()


def test_paying_repeat_executes_the_effect_twice(decks, piercing_light):
    """820.1.d -- "execute the instructions of this chain item one additional
    time during resolution"."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    victim = put_unit(state, foe, location=bf_location(0))

    ray = give(state, caster, piercing_light)
    state._current_player = caster
    state.apply(PlayCard(ray, repeat=True))
    guard = 0
    while state.chain and guard < 30:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(ChooseTarget(victim))
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert state.cards[victim].damage == 4, "the spell resolved once, not twice"


def test_not_paying_repeat_executes_it_once(decks, piercing_light):
    """The counterweight -- Repeat is an *optional* additional cost (820.1)."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    victim = put_unit(state, foe, location=bf_location(0))

    ray = give(state, caster, piercing_light)
    state._current_player = caster
    state.apply(PlayCard(ray))
    guard = 0
    while state.chain and guard < 30:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(ChooseTarget(victim))
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert state.cards[victim].damage == 2


def test_the_two_executions_can_choose_different_targets(decks, piercing_light):
    """820.2.a -- "Choices made for the additional execution do not have to be
    the same as the choices made for the initial execution."

    This is what makes Repeat more than a loop: the chain item carries two
    independent target sets.
    """
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    first = put_unit(state, foe, location=bf_location(0))
    second = put_unit(state, foe, location=bf_location(0))

    ray = give(state, caster, piercing_light)
    state._current_player = caster
    state.apply(PlayCard(ray, repeat=True))

    picks = [first, second]
    guard = 0
    while state.chain and guard < 30:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(ChooseTarget(picks.pop(0) if picks
                                     else state.legal_actions()[0].instance_id))
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert state.cards[first].damage == 2
    assert state.cards[second].damage == 2


# --- the cost is real -------------------------------------------------------


def test_repeat_is_not_offered_when_it_cannot_be_paid(decks):
    """820.1.c.1 -- the Repeat cost "is an Additional Cost to be paid during
    the steps of playing", so 358 Check legality bars a play that cannot
    afford it. The un-repeated play stays legal."""
    state = arena(decks)
    caster = state.turn_player
    put_unit(state, state.opponent(caster), location=bf_location(0))
    ray = give(state, caster, "OGN-009")
    state.players[caster].pool.energy = DB["OGN-009"].energy
    state.players[caster].pool.universal_power = DB["OGN-009"].power

    legal = state.legal_actions()
    assert PlayCard(ray) in legal
    assert PlayCard(ray, repeat=True) not in legal


def test_repeat_is_only_offered_on_cards_that_print_it(decks):
    """820.4 -- Repeat is a characteristic of the spell. A card without it is
    never offered the option."""
    state = arena(decks)
    caster = state.turn_player
    put_unit(state, state.opponent(caster), location=bf_location(0))
    ray = give(state, caster, "OGN-009")

    assert PlayCard(ray, repeat=True) not in state.legal_actions()
