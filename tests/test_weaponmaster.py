"""Weaponmaster (821).

821.1.c: "When you play me, you may choose a Card you control with the
Equipment tag. Necessary portions of its Rules Text are no longer Inactive if
they are currently Inactive. Pay the cost of its Equip ability, reduced by
[A], to attach it to this unit."

16 cards carry it. It is a *derived* triggered ability, like Quick-Draw
(819.1.d), so it needs no per-card script -- which is the whole point of
deriving abilities from printed keywords.

Two things make it more than a discount:

* **821.1.c** and **725.3** are an explicit exception to 718.2. An Equipment
  already attached to another unit has Inactive Rules Text, so its Equip
  ability cannot normally be activated -- Weaponmaster reaches it anyway.
* **821.1.c.6** -- "The Equip ability is not activated this way, and the unit
  with the Weaponmaster ability is not chosen." So no Equip trigger fires and
  no Deflect is paid for choosing the host.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.dsl import TriggerKind
from cards.gear import equipment_profile
from cards.scripts import abilities_of_kind
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


def weaponmaster_card():
    import cards.keywords as kw

    for card in sorted(DB.cards.values(), key=lambda c: c.card_id):
        if kw.has(card.parsed_keywords, "Weaponmaster"):
            return card
    pytest.skip("no Weaponmaster card in the pool")


def priced_equipment(nonzero_after_discount: bool = False):
    """An Equipment whose Equip cost could be read off the card (RQ-13).

    `nonzero_after_discount` picks one that still costs something once
    821.1.c's [A] reduction is applied -- several Equipment cost a single
    Power and are therefore *free* to a Weaponmaster, which is the rule
    working but makes a can't-afford test vacuous.
    """
    from cards.gear import discounted_equip_cost

    for card in sorted(DB.cards.values(), key=lambda c: c.card_id):
        profile = equipment_profile(card)
        if profile is None or profile.equip_cost is None:
            continue
        if not nonzero_after_discount:
            return card
        energy, domains = discounted_equip_cost(profile.equip_cost)
        if energy or domains:
            return card
    pytest.skip("no Equipment with a readable Equip cost")


def put(state, player, card_id, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- the ability is derived from the keyword --------------------------------


def test_weaponmaster_derives_a_play_trigger():
    """821.1 -- "Weaponmaster is a Triggered Ability keyword", and 821.1.b
    makes it a Play Effect. No per-card script needed."""
    card = weaponmaster_card()
    triggers = abilities_of_kind(card, TriggerKind.ON_PLAY, attached=False)
    assert triggers, f"{card.card_id} has WEAPONMASTER but no derived trigger"


def test_a_unit_without_the_keyword_derives_nothing():
    """The counterweight."""
    plain = DB["OGN-142"]
    assert not abilities_of_kind(plain, TriggerKind.ON_PLAY, attached=False)


def test_weaponmaster_has_no_function_on_the_board():
    """821.2 -- "Weaponmaster has no function while on the board." It is a
    play effect and nothing else, so it must not appear as an activated
    ability a unit can use later."""
    from cards.scripts import activated_abilities

    card = weaponmaster_card()
    for ability in activated_abilities(card):
        assert "weaponmaster" not in (ability.text or "").lower()


# --- the discount -----------------------------------------------------------


def test_the_equip_cost_is_reduced_by_one_power(decks):
    """821.1.c -- "Pay the cost of its Equip ability, reduced by [A]"."""
    from cards.gear import weaponmaster_ability

    card = weaponmaster_card()
    ability = weaponmaster_ability(card)
    assert ability is not None
    assert "reduced" in (ability.text or "").lower()


def test_a_cost_without_power_is_not_reduced(decks):
    """821.1.c.3 -- "If the chosen card's Equip cost does not contain [A], it
    can still be paid, but will not be reduced.\""""
    from cards.gear import discounted_equip_cost

    assert discounted_equip_cost((2, ())) == (2, ())          # energy only
    assert discounted_equip_cost((1, ("Fury",))) == (1, ())   # one power off
    assert discounted_equip_cost((0, ("Fury", "Body"))) == (0, ("Body",))


def test_an_equipment_with_no_equip_cost_cannot_be_chosen():
    """821.1.c.4 -- "If the chosen card doesn't have an Equip cost, it can't
    be paid." RQ-13 records four Equipment whose cost cannot be read; they
    must not be handed a guessed one here either."""
    from cards.gear import discounted_equip_cost

    assert discounted_equip_cost(None) is None


# --- the effect itself ------------------------------------------------------


def equip_to_me(state, unit, chosen):
    from cards.dsl import EquipToMe, Selector
    from cards.primitives import EffectContext, execute

    return execute(
        state,
        EquipToMe(selector=Selector(scope="choose", type="gear",
                                    controller="friendly")),
        EffectContext(controller=state.cards[unit].controller, source=unit,
                      chosen=(chosen,)),
    )


def test_it_attaches_the_chosen_equipment_to_the_unit(decks):
    """821.1.b -- "allows you to pay its Equip cost at a discount ... to
    Attach that Equipment to the unit with Weaponmaster"."""
    state = arena(decks)
    player = state.turn_player
    unit = put(state, player, weaponmaster_card().card_id, bf_location(0))
    gear = put(state, player, priced_equipment().card_id, BASE_LOCATION)

    equip_to_me(state, unit, gear)

    assert state.cards[gear].attached_to == unit
    assert state.cards[gear].location == bf_location(0)   # 719.3


def test_it_pays_the_discounted_cost(decks):
    """821.1.c -- the Equip cost "reduced by [A]"."""
    state = arena(decks)
    player = state.turn_player
    equipment = priced_equipment()
    unit = put(state, player, weaponmaster_card().card_id, bf_location(0))
    gear = put(state, player, equipment.card_id, BASE_LOCATION)

    energy, domains = equipment_profile(equipment).equip_cost
    before_energy = state.players[player].pool.energy
    before_power = state.players[player].pool.total_power()

    equip_to_me(state, unit, gear)

    assert state.players[player].pool.energy == before_energy - energy
    assert state.players[player].pool.total_power() == before_power - max(
        0, len(domains) - 1
    )


def test_nothing_happens_if_the_cost_cannot_be_paid(decks):
    """821.1.c.5 -- "If the chosen card's Equip cost can't be paid ... it stays
    in its current location, Attached to anything it was already Attached
    to.\""""
    state = arena(decks)
    player = state.turn_player
    unit = put(state, player, weaponmaster_card().card_id, bf_location(0))
    gear = put(state, player,
               priced_equipment(nonzero_after_discount=True).card_id,
               BASE_LOCATION)
    state.players[player].pool.energy = 0
    state.players[player].pool.universal_power = 0
    state.players[player].pool.power = {}

    equip_to_me(state, unit, gear)

    assert state.cards[gear].attached_to is None
    assert state.cards[gear].location == BASE_LOCATION


def test_a_non_equipment_gear_cannot_be_chosen(decks):
    """821.1.c -- "a Card you control with the Equipment tag". 150.1 makes
    that exactly the gear carrying the tag, and most gear do not."""
    state = arena(decks)
    player = state.turn_player
    plain = next(c for c in sorted(DB.cards.values(), key=lambda c: c.card_id)
                 if c.type == "gear" and equipment_profile(c) is None)
    unit = put(state, player, weaponmaster_card().card_id, bf_location(0))
    gear = put(state, player, plain.card_id, BASE_LOCATION)

    equip_to_me(state, unit, gear)

    assert state.cards[gear].attached_to is None


def test_it_reaches_an_equipment_already_attached_elsewhere(decks):
    """821.1.c and 725.3 -- an explicit exception to 718.2. An Equipment worn
    by another unit has Inactive Rules Text and cannot normally have its Equip
    ability activated; Weaponmaster moves it anyway."""
    state = arena(decks)
    player = state.turn_player
    first = put(state, player, "OGN-142", bf_location(1))
    gear = put(state, player, priced_equipment().card_id, bf_location(1))
    state.cards[gear].attached_to = first
    unit = put(state, player, weaponmaster_card().card_id, bf_location(0))

    equip_to_me(state, unit, gear)

    assert state.cards[gear].attached_to == unit, "718.2 blocked 821.1.c"
    assert state.cards[gear].location == bf_location(0)


def test_a_single_power_equip_cost_becomes_free(decks):
    """821.1.c's reduction is a full Power, so an Equipment costing exactly
    one Power is free to a Weaponmaster. Worth pinning: it is the rule
    working, and it is what makes the can't-afford case above need a more
    expensive card to be meaningful at all."""
    from cards.gear import discounted_equip_cost

    for card in sorted(DB.cards.values(), key=lambda c: c.card_id):
        profile = equipment_profile(card)
        if profile is None or profile.equip_cost != (0, ("Fury",)):
            continue
        assert discounted_equip_cost(profile.equip_cost) == (0, ())
        return
    pytest.skip("no Equipment costs exactly one Fury")
