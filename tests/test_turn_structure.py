"""The turn, rule by rule (167, 305-323, 431).

Working through the printed rules one at a time and asserting the engine
follows each. Where it did not, the gap is named in the test that found it.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.setup import build_state, load_deck
from engine.state import Phase

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


# --- 431 Burn Out -----------------------------------------------------------


def test_burn_out_recycles_the_trash_into_the_main_deck(decks):
    """431.2.b -- "Recycles their trash into their Main Deck."

    The engine gave the opponent a point and stopped, so a burned-out player
    stayed permanently deckless and could never draw again.
    """
    state = arena(decks)
    player = state.turn_player
    zones = state.players[player]
    zones.trash.extend(zones.main_deck)
    zones.main_deck.clear()
    trashed = len(zones.trash)
    assert trashed > 0

    state._phase_draw()
    assert not zones.trash, "the trash should have gone into the deck"
    # 431.2.d -- and the draw that caused it still completes.
    assert len(zones.main_deck) == trashed - 1


def test_burn_out_still_draws_the_card_that_caused_it(decks):
    """431.2.d -- "Completes the remainder of the action"."""
    state = arena(decks)
    player = state.turn_player
    zones = state.players[player]
    zones.trash.extend(zones.main_deck)
    zones.main_deck.clear()
    before = len(zones.hand)

    state._phase_draw()
    assert len(zones.hand) == before + 1


def test_burn_out_gives_an_opponent_a_point(decks):
    """431.2.c."""
    state = arena(decks)
    player = state.turn_player
    other = state.opponent(player)
    zones = state.players[player]
    zones.trash.extend(zones.main_deck)
    zones.main_deck.clear()
    before = state.players[other].points

    state._phase_draw()
    assert state.players[other].points == before + 1


def test_burning_out_with_nothing_to_recycle_still_scores(decks):
    """431.2.a -- "as much of the prescribed action as possible". An empty
    trash means an empty deck afterwards, but the point is still awarded."""
    state = arena(decks)
    player = state.turn_player
    other = state.opponent(player)
    state.players[player].main_deck.clear()
    state.players[player].trash.clear()
    before = state.players[other].points

    state._phase_draw()
    assert state.players[other].points == before + 1
    assert not state.players[player].main_deck


# --- 167 rune pools ---------------------------------------------------------


def test_the_pool_empties_at_the_end_of_a_turn(decks):
    """167 -- "at the start of each player's Main Phase **and the end of each
    player's turn**". Only the first half was implemented, so power survived
    into the opponent's Awaken, Beginning, Channel and Draw phases, where
    Reactions can be played with it."""
    state = arena(decks)
    for zones in state.players:
        zones.pool.energy = 5
        zones.pool.universal_power = 5

    state._phase_ending()
    for index, zones in enumerate(state.players):
        assert zones.pool.energy == 0, f"P{index} kept energy past end of turn"
        assert zones.pool.total_power() == 0, f"P{index} kept power"


def test_the_pool_empties_at_the_start_of_a_main_phase(decks):
    """316.3."""
    state = arena(decks)
    for zones in state.players:
        zones.pool.energy = 4
    state._phase_main_start()
    assert all(z.pool.energy == 0 for z in state.players)


# --- 315 the ABCD start of turn --------------------------------------------


def test_awaken_readies_everything_the_turn_player_controls(decks):
    """315.1.b -- *all* Game Objects, not just units: runes and gear too."""
    state = arena(decks)
    player = state.turn_player
    for ref in state.cards.values():
        ref.exhausted = True

    state._phase_awaken()
    mine = [r for r in state.cards.values() if r.controller == player]
    assert mine and all(not r.exhausted for r in mine)


def test_awaken_does_not_ready_the_opponent(decks):
    """315.1.b -- "they control"."""
    state = arena(decks)
    other = state.opponent(state.turn_player)
    for ref in state.cards.values():
        ref.exhausted = True
    state._phase_awaken()
    theirs = [r for r in state.cards.values() if r.controller == other]
    assert theirs and all(r.exhausted for r in theirs)


def test_channel_takes_two_runes_or_as_many_as_remain(decks):
    """315.3.b / 315.3.b.1."""
    state = arena(decks)
    player = state.turn_player
    zones = state.players[player]
    zones.rune_deck[:] = zones.rune_deck[:1]        # only one left
    before = len(zones.channeled_runes)
    state._phase_channel()
    assert len(zones.channeled_runes) == before + 1
    assert not zones.rune_deck


# --- 313 Focus --------------------------------------------------------------


def test_no_player_has_focus_in_a_neutral_state(decks):
    """313.5."""
    state = arena(decks)
    assert state.showdown is None
    assert state.focus is None


# --- 423 Stun --------------------------------------------------------------


def put_unit(state, player, card_id, location="base"):
    from engine.zones import CardRef

    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def test_a_stunned_unit_contributes_no_might_to_combat_damage(decks):
    """423.1.b -- "A Stunned Unit does not contribute its might to damage in
    the combat damage step." The whole point of the status."""
    from engine.state import bf_location

    state = arena(decks)
    attacker = state.turn_player
    location = bf_location(0)
    strong = put_unit(state, attacker, "OGN-142", location)   # 10 Might

    assert state.combat_might(attacker, location) == state.might_of(
        state.cards[strong]
    )
    state.cards[strong].stunned = True
    assert state.combat_might(attacker, location) == 0


def test_stun_does_not_change_a_units_might_for_lethal_damage(decks):
    """423.1.b is about *contributing* damage. It does not say a stunned unit
    is easier to kill, so its Might is unchanged everywhere else."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")
    before = state.might_of(state.cards[unit])
    state.cards[unit].stunned = True
    assert state.might_of(state.cards[unit]) == before


def test_a_stunned_unit_cannot_be_stunned_again(decks):
    """423.1.a.1 -- and the reason it matters: "when you stun an enemy unit"
    triggers must not fire on a unit that was already stunned."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")
    assert state.stun(state.cards[unit]) is True
    assert state.stun(state.cards[unit]) is False


def test_stun_expires_at_the_end_of_the_turn(decks):
    """423.1.a.2 -- lost during step 3d of the end of turn cleanup, which
    317.2.c places alongside every other "this turn" expiry."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")
    state.cards[unit].stunned = True
    state._phase_ending()
    assert state.cards[unit].stunned is False


# --- 471.2 Score triggers ---------------------------------------------------


def test_hold_abilities_trigger_when_a_battlefield_is_held(decks):
    """471.2.b -- "Hold abilities trigger at a Battlefield that was Held."

    There was no Hold trigger at all, so ten cards' printed text did nothing:
    Ahri - Alluring reads "When I hold, you score 1 point" and never scored.
    """
    from cards.dsl import TriggerKind

    assert hasattr(TriggerKind, "ON_HOLD")

    from engine.state import bf_location

    state = arena(decks)
    player = state.turn_player
    bf = state.battlefields[0]
    bf.controller = player
    unit = put_unit(state, player, "OGN-066", bf_location(0))   # Ahri - Alluring
    before = state.players[player].points

    state._phase_beginning()
    # One point for the Hold itself, one from Ahri's triggered ability.
    assert state.players[player].points == before + 2, state.log[-4:]
    assert state.cards[unit].location == bf_location(0)


def test_score_triggers_only_fire_at_the_battlefield_that_scored(decks):
    """471.2 -- "Trigger Score abilities **at the Battlefield that Scored**".

    Conquer triggers used to fire for every card the player controlled
    anywhere, so a unit sitting at battlefield 1 triggered when its owner
    conquered battlefield 0.
    """
    from engine.state import bf_location

    state = arena(decks)
    player = state.turn_player
    elsewhere = put_unit(state, player, "OGN-066", bf_location(1))
    state.battlefields[1].controller = player
    before = state.players[player].points

    # Score battlefield 0, where that unit is not.
    state._score(player, state.battlefields[0], "Hold")
    assert state.players[player].points == before + 1, (
        "a unit at another battlefield triggered on a score it was not part of"
    )
    assert state.cards[elsewhere].location == bf_location(1)


def test_a_hold_trigger_does_not_fire_on_a_conquer(decks):
    """471.2.a/b keep the two apart -- a Hold ability is not a Conquer one."""
    from engine.state import bf_location

    state = arena(decks)
    player = state.turn_player
    put_unit(state, player, "OGN-066", bf_location(0))
    before = state.players[player].points
    state._score(player, state.battlefields[0], "Conquer")
    assert state.players[player].points == before + 1


def test_a_conquer_trigger_elsewhere_does_not_fire(decks):
    """471.2 with a scripted card, so the test can actually fail.

    Warmog's Armor reads "When I conquer, buff me". Attached to a unit at
    battlefield 1, it used to buff itself every time its controller conquered
    battlefield 0 — because Conquer triggers fired for every card the player
    controlled, anywhere.
    """
    from engine.state import bf_location

    state = arena(decks)
    player = state.turn_player
    host = put_unit(state, player, "OGN-175", bf_location(1))
    gear = put_unit(state, player, "SFD-108", bf_location(1))   # Warmog's Armor
    state.cards[gear].attached_to = host
    before = state.cards[gear].might_permanent

    state._score(player, state.battlefields[0], "Conquer")
    assert state.cards[gear].might_permanent == before, (
        "a Conquer trigger at battlefield 1 fired on a Conquer at battlefield 0"
    )

    # ...and it does fire when its own battlefield is the one scored.
    state._score(player, state.battlefields[1], "Conquer")
    assert state.cards[gear].might_permanent > before
