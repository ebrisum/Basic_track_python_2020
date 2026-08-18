"""The no-cheating suite: hidden information must be *unobservable*, not just
undisplayed.

The rest of the test suite asks "does the observation show the right things?".
This file asks the harder question in the opposite direction: **if I change
something the player cannot legally know, does anything they can see move?**

That is the only test that catches a leak through a side channel -- instance
ids, list ordering, a size that shifts, a serialized field nobody meant to
include. A leak like that does not make the agent play illegally. It makes it
play *better than it should*, and every win rate measured afterwards is worth
nothing.

The mutations below are all things 128 Privacy says a player may not read:
another player's hand contents, either deck's order, a facedown card's face.
128.4 is the rule: "only the controller of a card on the board or the owner of
a card in any other zone may read or look at the face of the card."
"""

from __future__ import annotations

import copy
import random

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.replay import state_hash
from engine.setup import build_state, load_deck
from engine.state import Phase
from engine.zones import CardRef

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def midgame(decks, seed=5, steps=90):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    return state


def view(state, player: int) -> bytes:
    return state.observation(player).to_canonical_bytes()


SEEDS = [1, 4, 7, 12, 19]


# --- the mandatory test: identical view over different hidden truths --------


@pytest.mark.parametrize("seed", SEEDS)
def test_reordering_the_opponents_hand_is_invisible(decks, seed):
    """A hand is a set, not a sequence, as far as anyone else is concerned."""
    state = midgame(decks, seed)
    other = copy.deepcopy(state)
    if len(other.players[1].hand) < 2:
        pytest.skip("hand too small to reorder")
    random.Random(seed).shuffle(other.players[1].hand)

    assert view(state, 0) == view(other, 0), "P0 can see P1's hand *order*"


@pytest.mark.parametrize("seed", SEEDS)
def test_swapping_an_opponent_hand_card_for_a_deck_card_is_invisible(decks, seed):
    """BUILD.md's exact formulation: opponent holds X, or holds Y instead. If
    the observer was never shown either, the two states must encode the same.

    This is stronger than reordering -- the *identity* of a card changes, and
    a leak through an instance id or a card-derived field shows up here.
    """
    state = midgame(decks, seed)
    other = copy.deepcopy(state)
    them = other.players[1]
    if not them.hand or not them.main_deck:
        pytest.skip("nothing to swap")

    i = them.hand.index(them.hand[0])
    them.hand[i], them.main_deck[0] = them.main_deck[0], them.hand[i]

    assert view(state, 0) == view(other, 0), (
        "P0 can tell which card P1 is holding"
    )
    assert view(state, 1) != view(other, 1), (
        "P1 must still see their own hand change -- otherwise this test is "
        "passing because the observation is empty"
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_reordering_either_deck_is_invisible_to_both(decks, seed):
    """108.4.d -- deck contents are deducible, deck *order* is secret. Neither
    player may see it, including in their own deck."""
    state = midgame(decks, seed)
    other = copy.deepcopy(state)
    rng = random.Random(seed)
    for player in (0, 1):
        rng.shuffle(other.players[player].main_deck)
        rng.shuffle(other.players[player].rune_deck)

    for player in (0, 1):
        assert view(state, player) == view(other, player), (
            f"P{player} can see deck order"
        )


@pytest.mark.parametrize("seed", SEEDS)
def test_the_face_of_a_facedown_card_is_invisible_to_the_opponent(decks, seed):
    """128.4 -- "If a player controls a facedown card at a battlefield, that
    player and only that player may read or look at that card's face"."""
    state = midgame(decks, seed)
    player = state.turn_player
    other_player = state.opponent(player)

    card_id = next(c.card_id for c in DB.cards.values()
                   if c.type in ("unit", "spell", "gear") and c.has_hidden)
    alternative = next(c.card_id for c in DB.cards.values()
                       if c.type in ("unit", "spell", "gear") and c.has_hidden
                       and c.card_id != card_id)

    def with_face(face: str):
        clone = copy.deepcopy(state)
        instance_id = max(clone.cards) + 1
        clone.cards[instance_id] = CardRef(
            instance_id=instance_id, card_id=face, owner=player,
            controller=player, hidden_at=0, hidden_on_turn=clone.turn_number,
        )
        clone.battlefields[0].controller = player
        return clone

    one, two = with_face(card_id), with_face(alternative)
    assert view(one, other_player) == view(two, other_player), (
        "the opponent can read a facedown card's face"
    )
    assert view(one, player) != view(two, player) or card_id == alternative, (
        "its controller must be able to tell which card it is (128.4)"
    )


# --- the same, through the replay hash, which is what search compares -------


@pytest.mark.parametrize("seed", SEEDS)
def test_the_state_hash_does_move_when_hidden_information_moves(decks, seed):
    """The counterweight. The observation must hide the opponent's hand *from
    the opponent*, but the replay hash covers both players' views, so it must
    still notice -- otherwise a determinization bug would replay clean.
    """
    state = midgame(decks, seed)
    other = copy.deepcopy(state)
    them = other.players[1]
    if not them.hand or not them.main_deck:
        pytest.skip("nothing to swap")
    them.hand[0], them.main_deck[0] = them.main_deck[0], them.hand[0]

    assert state_hash(state) != state_hash(other)


# --- observing must not change anything ------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_building_an_observation_does_not_touch_the_state(decks, seed):
    """BUILD.md section 10: "observations do not mutate engine state"."""
    state = midgame(decks, seed)
    before = state_hash(state)
    legal_before = state.legal_actions()
    for _ in range(3):
        state.observation(0)
        state.observation(1)
    assert state_hash(state) == before
    assert state.legal_actions() == legal_before


@pytest.mark.parametrize("seed", SEEDS)
def test_legal_actions_never_depend_on_the_opponents_hidden_cards(decks, seed):
    """A softer leak than the observation: the *action set* could betray what
    the opponent holds even if the observation does not."""
    state = midgame(decks, seed)
    other = copy.deepcopy(state)
    them = other.players[1 - state.current_player]
    if not them.hand or not them.main_deck:
        pytest.skip("nothing to swap")
    them.hand[0], them.main_deck[0] = them.main_deck[0], them.hand[0]

    assert state.legal_actions() == other.legal_actions()
