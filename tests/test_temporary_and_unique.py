"""Temporary (816) and Unique (825).

Two keywords the 800-series audit found unimplemented that do *not* depend on
RQ-19, so they could be built rather than logged.

* **816 Temporary** -- "At the start of this permanent's controller's
  Beginning Phase, before scoring, kill this." A board rule: without it, a
  permanent meant to last one round lives forever.
* **825 Unique** -- "A deck can contain only one card of a given name if the
  card has Unique." A deck-construction rule, alongside the 103.2.b limit of
  three copies that was already enforced.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.setup import build_state, load_deck, validate
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
TEMP_GEAR = "SFD-186"      # Spinning Axe, [Temporary]
TEMP_UNIT = "OGN-106"      # Sprite Mother, [Temporary]
UNIQUE_GEAR = "SFD-190"    # Forgefire Cape, [Unique]


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    return state


def put(state, player, card_id, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- 816 Temporary ----------------------------------------------------------


def test_a_temporary_permanent_dies_at_its_controllers_beginning_phase(decks):
    """816.1.b -- "At the start of this permanent's controller's Beginning
    Phase, before scoring, kill this"."""
    state = arena(decks)
    owner = state.turn_player
    gear = put(state, owner, TEMP_GEAR)

    state._phase_beginning()

    assert state.cards[gear].location is None
    assert gear in state.players[owner].trash


def test_it_survives_the_opponents_beginning_phase(decks):
    """816.1.c -- "The Trigger Condition is the controller **of the
    permanent's** Beginning Phase starting." Not anybody's."""
    state = arena(decks)
    other = state.opponent(state.turn_player)
    gear = put(state, other, TEMP_GEAR)

    state._phase_beginning()          # the *turn player's* Beginning Phase

    assert state.cards[gear].location is not None


def test_temporary_kills_before_scoring(decks):
    """816.1.b -- "before scoring". A Temporary unit holding a battlefield
    cannot Hold it on the turn it dies, because it is already dead when
    315.2.b runs."""
    state = arena(decks)
    owner = state.turn_player
    unit = put(state, owner, TEMP_UNIT, bf_location(0))
    state.battlefields[0].controller = owner
    state.battlefields[0].scored_by.clear()
    state.players[owner].points = 0

    state._phase_beginning()

    assert state.cards[unit].location is None
    assert state.players[owner].points == 0, "it scored a Hold while dying"


def test_a_permanent_without_temporary_is_untouched(decks):
    """The counterweight."""
    state = arena(decks)
    owner = state.turn_player
    unit = put(state, owner, "OGN-142", bf_location(0))

    state._phase_beginning()

    assert state.cards[unit].location is not None


def test_temporary_is_redundant_in_multiples(decks):
    """816.2.a -- "Regardless of how many instances there are, the ability
    will only trigger once." Nothing to observe beyond it not erroring, but a
    card cannot be killed twice, and the trash must hold one copy."""
    state = arena(decks)
    owner = state.turn_player
    gear = put(state, owner, TEMP_GEAR)

    state._phase_beginning()
    state._phase_beginning()

    assert state.players[owner].trash.count(gear) == 1


# --- 825 Unique -------------------------------------------------------------


def test_two_copies_of_a_unique_card_are_an_illegal_deck(decks):
    """825.3.a -- "A deck can contain only one card of a given name if the
    card has Unique"."""
    import dataclasses

    deck = dataclasses.replace(
        decks[0], main=[UNIQUE_GEAR, UNIQUE_GEAR] + list(decks[0].main[2:])
    )
    problems = validate(deck, DB)
    assert any("825" in p for p in problems), problems


def test_one_copy_of_a_unique_card_is_fine(decks):
    import dataclasses

    deck = dataclasses.replace(
        decks[0], main=[UNIQUE_GEAR] + list(decks[0].main[1:])
    )
    assert not any("825" in p for p in validate(deck, DB))


def test_the_starter_decks_are_still_legal(decks):
    """The rule must not reject decks that were legal before it existed."""
    for deck in decks:
        assert validate(deck, DB) == []
