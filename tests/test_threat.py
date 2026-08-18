"""Can I take that battlefield, and can they take it back?

Riftbound is a closed system, so this is arithmetic rather than guesswork.
465.2.c says each side assigns damage equal to its summed Might among the
other's units, lethal-in-full before moving on -- so a side wipes the other
exactly when its Might covers the other's remaining Might. What is *not*
known is the opponent's hand; 103.1.b.2 bounds that, because every card they
can hold is legal in their Legend's Domain Identity.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.threat import (
    domain_identity,
    domain_pool,
    forecast,
    forecast_all,
    projected_might,
)
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


def put_unit(state, player, card_id, location=BASE_LOCATION, exhausted=False):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player,
        location=location, exhausted=exhausted,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- what the opponent could be holding (103.1.b) --------------------------


def test_domain_identity_comes_from_the_legend(decks):
    """103.1.b.2 -- the deck's Domain Identity is its Champion Legend's."""
    state = arena(decks)
    identity = domain_identity(state, 0)
    legend = state.cards[state.players[0].legend].card_id
    assert identity == frozenset(DB[legend].domains)
    assert identity


def test_the_domain_pool_admits_only_cards_they_could_legally_play(decks):
    """103.1.b.3-4 -- a multi-domain card needs *all* its domains covered."""
    state = arena(decks)
    identity = domain_identity(state, 1)
    for card in domain_pool(state, 1):
        assert set(card.domains) <= identity


def test_the_domain_pool_excludes_the_other_players_domains(decks):
    """The bound has to be worth something: a Chaos/Fury deck cannot be
    holding a Body card."""
    state = arena(decks)
    identity = domain_identity(state, 0)
    outside = [c for c in DB.cards.values()
               if c.type == "unit" and c.domains and not set(c.domains) <= identity]
    assert outside
    pool_ids = {c.card_id for c in domain_pool(state, 0)}
    assert not pool_ids & {c.card_id for c in outside}


def test_the_domain_pool_is_main_deck_cards_only(decks):
    """052 / 161.1 -- runes, legends and battlefields are not Main Deck."""
    state = arena(decks)
    for card in domain_pool(state, 0):
        assert card.type in ("unit", "spell", "gear")


# --- Might under a designation it does not yet have -------------------------


def test_projected_might_applies_assault_without_mutating(decks):
    """807 -- to forecast an attack you need the Might it *would* have."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")
    ref = state.cards[unit]
    assert projected_might(state, ref, "attacker") == (
        state.might_of(ref) + DB[ref.card_id].assault
    )
    assert ref.is_attacker is False   # unchanged


def test_projected_might_applies_shield_when_defending(decks):
    shielded = next(c for c in DB.cards.values() if c.type == "unit" and c.shield)
    state = arena(decks)
    unit = put_unit(state, 0, shielded.card_id)
    ref = state.cards[unit]
    assert projected_might(state, ref, "defender") == (
        state.might_of(ref) + shielded.shield
    )


# --- the forecast ----------------------------------------------------------


def test_a_stronger_attack_is_reported_as_winnable(decks):
    state = arena(decks)
    me = state.turn_player
    them = state.opponent(me)
    state.battlefields[0].controller = them
    put_unit(state, them, "OGN-175", bf_location(0))   # 3 Might garrison
    put_unit(state, me, "OGN-142")                     # 10 Might, ready at base

    view = forecast(state, me, 0)
    assert view.can_take is True
    assert view.reinforcement[me] >= 10


def test_a_weaker_attack_is_not_reported_as_winnable(decks):
    state = arena(decks)
    me = state.turn_player
    them = state.opponent(me)
    state.battlefields[0].controller = them
    put_unit(state, them, "OGN-142", bf_location(0))   # 10 Might garrison
    put_unit(state, me, "OGN-175")                     # 3 Might

    assert forecast(state, me, 0).can_take is False


def test_an_exhausted_unit_is_not_counted_as_reinforcement(decks):
    """144.2 -- a Standard Move costs exhausting, so an exhausted unit at
    base cannot join the fight this turn."""
    state = arena(decks)
    me = state.turn_player
    put_unit(state, me, "OGN-142", exhausted=True)
    assert forecast(state, me, 0).reinforcement[me] == 0


def test_an_undefended_battlefield_is_takeable_by_anyone_with_a_body(decks):
    state = arena(decks)
    me = state.turn_player
    state.battlefields[0].controller = state.opponent(me)
    put_unit(state, me, "OGN-175")
    assert forecast(state, me, 0).can_take is True


def test_holding_a_battlefield_alone_is_flagged_as_loseable(decks):
    """The other half of the question: what they can take back from me."""
    state = arena(decks)
    me = state.turn_player
    them = state.opponent(me)
    bf = state.battlefields[0]
    bf.controller = me
    put_unit(state, them, "OGN-142")                   # 10 Might, ready

    view = forecast(state, me, 0)
    assert view.can_lose is True


def test_a_garrison_bigger_than_anything_they_have_is_safe(decks):
    state = arena(decks)
    me = state.turn_player
    bf = state.battlefields[0]
    bf.controller = me
    put_unit(state, me, "OGN-142", bf_location(0))
    put_unit(state, me, "OGN-142", bf_location(0))
    put_unit(state, state.opponent(me), "OGN-175")     # 3 Might

    view = forecast(state, me, 0)
    assert view.can_lose is False


def test_the_hidden_threat_is_bounded_by_their_domain(decks):
    """The user's point: their hand is secret, but every card in it is legal
    in their Domain Identity, so the worst case is bounded, not unknown."""
    state = arena(decks)
    them = 1
    view = forecast(state, 0, 0)
    best = max((c.might for c in domain_pool(state, them) if c.type == "unit"),
               default=0)
    assert view.hidden_threat[them] <= best


def test_hidden_threat_is_zero_with_no_resources(decks):
    """They cannot deploy what they cannot pay for (163)."""
    state = arena(decks)
    state.players[1].pool.energy = 0
    state.players[1].pool.power = {}
    state.players[1].pool.universal_power = 0
    state.players[1].hand.clear()
    assert forecast(state, 0, 0).hidden_threat[1] == 0


def test_forecast_all_covers_every_battlefield(decks):
    state = arena(decks)
    views = forecast_all(state, 0)
    assert len(views) == len(state.battlefields)
    assert [v.battlefield for v in views] == list(range(len(state.battlefields)))


def test_a_forecast_never_reads_the_opponents_hand(decks):
    """128 Privacy -- swapping their hand must not change what I am told."""
    state = arena(decks)
    before = forecast_all(state, 0)
    hand = state.players[1].hand
    state.players[1].hand = list(reversed(hand))
    assert forecast_all(state, 0) == before
