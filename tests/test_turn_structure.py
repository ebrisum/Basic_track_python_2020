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


# --- 430 Channel ------------------------------------------------------------


def test_channel_puts_runes_on_the_board_ready_by_default(decks):
    """430.2.a -- "By default, runes are channeled readied"."""
    state = arena(decks)
    player = state.turn_player
    before = len(state.players[player].channeled_runes)
    assert state.channel(player, 2) == 2
    fresh = state.players[player].channeled_runes[before:]
    assert len(fresh) == 2
    assert all(not state.cards[i].exhausted for i in fresh)


def test_channel_can_bring_runes_in_exhausted(decks):
    """430.2 -- "Channel 1 rune exhausted."."""
    state = arena(decks)
    player = state.turn_player
    before = len(state.players[player].channeled_runes)
    state.channel(player, 1, exhausted=True)
    fresh = state.players[player].channeled_runes[before:]
    assert state.cards[fresh[0]].exhausted is True


def test_channel_takes_as_many_as_the_rune_deck_allows(decks):
    """430.3 -- "If there aren't sufficient runes, channel as many as
    possible." The return value is what "if you couldn't channel 2 runes this
    way" needs to test."""
    state = arena(decks)
    player = state.turn_player
    state.players[player].rune_deck[:] = state.players[player].rune_deck[:1]
    assert state.channel(player, 2) == 1
    assert not state.players[player].rune_deck


def test_boneshiver_channels_a_rune_exhausted_on_conquer(decks):
    """SFD-118 end to end, through the Conquer trigger."""
    from engine.state import bf_location

    state = arena(decks)
    player = state.turn_player
    host = put_unit(state, player, "OGN-175", bf_location(0))
    gear = put_unit(state, player, "SFD-118", bf_location(0))
    state.cards[gear].attached_to = host
    before = len(state.players[player].channeled_runes)

    state._score(player, state.battlefields[0], "Conquer")
    fresh = state.players[player].channeled_runes[before:]
    assert len(fresh) == 1
    assert state.cards[fresh[0]].exhausted is True


# --- 179-187 / 439 Tokens ---------------------------------------------------


def test_a_created_token_is_a_token_and_enters_exhausted(decks):
    """439 / 143.4 -- a unit token enters exhausted like any other unit,
    unless the creating effect says otherwise (184.1)."""
    from cards.tokens import RECRUIT

    state = arena(decks)
    player = state.turn_player
    token = state.create_token(RECRUIT, player)
    ref = state.cards[token]
    assert ref.is_token is True
    assert ref.owner == player and ref.controller == player   # 182 / 183
    assert ref.exhausted is True
    assert state.db[ref.card_id].might == 1


def test_a_creating_effect_can_bring_a_token_in_ready(decks):
    """184.1 -- "The effect may state that the token enters ready"."""
    from cards.tokens import SPRITE

    state = arena(decks)
    token = state.create_token(SPRITE, state.turn_player, exhausted=False)
    assert state.cards[token].exhausted is False


def test_a_killed_token_ceases_to_exist_rather_than_going_to_the_trash(decks):
    """186.1 -- "If a token is put into any Non-Board Zone besides the chain,
    it ceases to exist immediately after moving to its new zone." """
    from cards.tokens import RECRUIT

    state = arena(decks)
    player = state.turn_player
    token = state.create_token(RECRUIT, player)
    trash_before = len(state.players[player].trash)

    state._kill(state.cards[token])
    assert token not in state.cards, "the token instance should be gone"
    assert len(state.players[player].trash) == trash_before
    assert token not in state.players[player].base


def test_tokens_do_not_break_card_conservation(decks):
    """A token is created, not dealt, so it must not read as a card that
    appeared from nowhere."""
    from cards.tokens import RECRUIT

    from engine.invariants import check

    state = arena(decks)
    assert check(state) == []
    state.create_token(RECRUIT, state.turn_player)
    assert check(state) == []


def test_faithful_manufactor_creates_a_recruit_where_it_is_played(decks):
    """OGN-211 end to end: "play a 1 Might Recruit unit token here"."""
    from cards.tokens import RECRUIT

    state = arena(decks)
    player = state.turn_player
    source = put_unit(state, player, "OGN-211")
    before = {i for i in state.cards}
    state._fire(__import__("cards.dsl", fromlist=["TriggerKind"]).TriggerKind.ON_PLAY,
                source)
    state._resolve_effects()
    fresh = [i for i in state.cards if i not in before]
    assert len(fresh) == 1
    assert state.cards[fresh[0]].card_id == RECRUIT
    assert state.cards[fresh[0]].is_token is True


# --- 421 Hide / 811 Hidden --------------------------------------------------


def hidable(state, player):
    """Put a HIDDEN card in `player`'s hand and give them a power to pay [A]."""
    from engine.zones import CardRef

    card_id = next(
        c.card_id for c in DB.cards.values()
        if c.type in ("unit", "spell", "gear") and c.has_hidden
    )
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    state.players[player].pool.universal_power += 1
    return instance_id


def test_hide_puts_a_card_facedown_at_a_battlefield_you_control(decks):
    """421.1 / 811.1.b."""
    from engine.actions import HideCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = player
    card = hidable(state, player)

    assert HideCard(card, 0) in state.legal_actions()
    state.apply(HideCard(card, 0))
    assert state.cards[card].hidden_at == 0
    assert card not in state.players[player].hand
    assert state.cards[card].location is None      # not a permanent


def test_hide_is_not_offered_at_a_battlefield_you_do_not_control(decks):
    """811.1.b -- "at a battlefield you control"."""
    from engine.actions import HideCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = state.opponent(player)
    card = hidable(state, player)
    assert HideCard(card, 0) not in state.legal_actions()


def test_only_one_card_can_be_hidden_at_a_battlefield(decks):
    """811.1.b -- "that doesn't already have a facedown card hidden there"."""
    from engine.actions import HideCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = player
    first, second = hidable(state, player), hidable(state, player)
    state.apply(HideCard(first, 0))
    assert HideCard(second, 0) not in state.legal_actions()


def test_a_hidden_card_cannot_be_played_on_the_turn_it_was_hidden(decks):
    """811.1.b -- "**Beginning on the next turn**, this gains [Reaction]"."""
    from engine.actions import HideCard, PlayCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = player
    card = hidable(state, player)
    state.apply(HideCard(card, 0))
    assert PlayCard(card) not in state.legal_actions()


def test_a_hidden_card_can_be_played_for_free_from_the_next_turn(decks):
    """811.1.b -- "you may play this, ignoring its base cost"."""
    from engine.actions import HideCard, PlayCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = player
    card = hidable(state, player)
    state.apply(HideCard(card, 0))

    state.turn_number += 1
    state.players[player].pool.clear()          # nothing left to pay with
    assert PlayCard(card) in state.legal_actions(), (
        "a hidden card is played ignoring its base cost"
    )


def test_the_opponent_sees_that_a_card_is_hidden_but_not_which(decks):
    """128 Privacy -- *that* a facedown card is there is public; what it is
    is not."""
    from engine.actions import HideCard

    state = arena(decks)
    player = state.turn_player
    other = state.opponent(player)
    state.battlefields[0].controller = player
    card = hidable(state, player)
    state.apply(HideCard(card, 0))

    view = state.observation(other)
    assert view.battlefields[0].facedown_by == player
    identities = {c.instance_id for c in view.board} | {
        c.instance_id for c in view.hand
    }
    assert card not in identities, "the hidden card's identity leaked"


def test_hiding_pays_one_power_of_any_domain(decks):
    """811.1.b's cost is [A] (135.2.e.5) -- any domain, not a matching one."""
    from engine.actions import HideCard

    state = arena(decks)
    player = state.turn_player
    state.battlefields[0].controller = player
    card = hidable(state, player)
    state.players[player].pool.universal_power = 0
    state.players[player].pool.power = {"Fury": 1}       # a mismatched domain

    assert HideCard(card, 0) in state.legal_actions()
    state.apply(HideCard(card, 0))
    assert state.players[player].pool.total_power() == 0
