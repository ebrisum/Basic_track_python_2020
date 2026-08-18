"""Deduction from public information.

Riftbound is a closed system -- a 40-card Main Deck (103.2) fixed before the
game, and most zones public -- so the opponent's hand is *bounded* by
subtraction rather than guessed at. These tests pin the arithmetic and, more
importantly, that it never reads a zone the observer is not entitled to.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from agents.ismcts import determinize
from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.knowledge import (
    KnownDecklists,
    decklist_counts,
    deduce,
    draw_odds,
    hypergeometric_at_least_one,
    public_counts,
)
from engine.setup import build_state, load_deck

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def midgame_with_channeled_runes(decks, player: int, seeds: int = 20):
    """A midgame state where `player` has runes on the board.

    Searched rather than pinned to a seed: a random walk's path depends on
    the whole legal-action set, so any unrelated new action (Equip, say)
    silently changes where seed N lands and the precondition evaporates.
    """
    for seed in range(1, seeds + 1):
        state = midgame(decks, seed)
        if state.players[player].channeled_runes:
            return state
    raise AssertionError(f"no seed in 1..{seeds} left P{player} holding runes")


def midgame(decks, seed=1, steps=120):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    return state


# --- the subtraction is exact ----------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_unseen_pool_is_exactly_hand_plus_deck(decks, seed):
    """The core identity: decklist minus public == what is still hidden."""
    state = midgame(decks, seed)
    for subject in (0, 1):
        knowledge = deduce(state, 1 - subject, subject)
        assert knowledge.unseen_total() == knowledge.hand_size + knowledge.deck_size


def test_a_player_knows_their_own_deck_contents_but_not_its_order(decks):
    """108.4.d -- order is secret, contents are deducible."""
    state = midgame(decks, 6)
    own = deduce(state, 0, 0)
    assert own.unseen_total() == own.deck_size
    # The hand is located, not unseen, because its owner can see it.
    hand_ids = Counter(state.cards[i].card_id for i in state.players[0].hand)
    for card_id, count in hand_ids.items():
        assert own.located[card_id] >= count


def test_public_counts_covers_trash_board_and_champion_zone(decks):
    """108.2.d, 107.1.d, 108.3.e -- for Main Deck cards only."""
    state = midgame(decks, 7)
    counts = public_counts(state, 1)
    main_deck_types = {"unit", "spell", "gear"}
    for instance_id in state.players[1].trash:
        if DB[state.cards[instance_id].card_id].type in main_deck_types:
            assert counts[state.cards[instance_id].card_id] >= 1
    for ref in state.cards.values():
        if ref.owner == 1 and ref.location is not None:
            if DB[ref.card_id].type in main_deck_types:
                assert counts[ref.card_id] >= 1


def test_channeled_runes_are_not_main_deck_cards(decks):
    """161.1 / 052 -- a rune in a base is public, but counting it would
    inflate the deduced 40-card list every time one was channeled."""
    state = midgame_with_channeled_runes(decks, player=1)
    counts = public_counts(state, 1)
    channeled = state.players[1].channeled_runes
    for instance_id in channeled:
        assert state.cards[instance_id].card_id not in counts


def test_public_counts_never_includes_a_hidden_hand(decks):
    """The whole point: a hand card must not appear as publicly located."""
    state = midgame(decks, 8)
    counts = public_counts(state, 1)
    located = sum(counts.values())
    decklist = sum(decklist_counts(state, 1).values())
    assert located == decklist - len(state.players[1].hand) - len(state.players[1].main_deck)


def test_decklist_totals_stay_constant_through_a_game(decks):
    """Main Deck cards do not appear or vanish (103.2).

    Constancy is the invariant, not any particular number: these starter decks
    are 40 Main Deck cards plus a Chosen Champion in the Champion Zone, so the
    total is 41. A drifting total would mean the deduction is double-counting
    a zone -- which it was, for channeled runes, before they were excluded.
    """
    state = build_state(decks[0], decks[1], seed=9, db=DB)
    agent = RandomAgent(9)
    first = sum(decklist_counts(state, 1).values())
    for _ in range(200):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
        assert sum(decklist_counts(state, 1).values()) == first


# --- what the observer is allowed to know -----------------------------------


def test_without_a_known_decklist_nothing_can_be_deduced(decks):
    """Game one against an unknown list: only sizes are public."""
    state = midgame(decks, 10)
    blind = deduce(state, 0, 1, mode=KnownDecklists.OWN_ONLY)
    assert blind.unseen_total() == 0
    assert not blind.decklist_known
    assert blind.hand_size == len(state.players[1].hand)  # size is still public


def test_own_deduction_works_even_in_own_only_mode(decks):
    state = midgame(decks, 11)
    own = deduce(state, 0, 0, mode=KnownDecklists.OWN_ONLY)
    assert own.decklist_known and own.unseen_total() == own.deck_size


def test_probability_in_hand_is_bounded(decks):
    state = midgame(decks, 12)
    knowledge = deduce(state, 0, 1)
    for card_id in list(knowledge.unseen)[:10]:
        assert 0.0 <= knowledge.probability_in_hand(card_id) <= 1.0


def test_expected_in_hand_never_exceeds_the_hand_size(decks):
    state = midgame(decks, 13)
    knowledge = deduce(state, 0, 1)
    assert knowledge.expected_in_hand(lambda _cid: True) == pytest.approx(
        knowledge.hand_size
    )


# --- draw mathematics -------------------------------------------------------


def test_hypergeometric_edges():
    assert hypergeometric_at_least_one(0, 40, 5) == 0.0
    assert hypergeometric_at_least_one(3, 40, 0) == 0.0
    assert hypergeometric_at_least_one(40, 40, 1) == 1.0
    assert hypergeometric_at_least_one(1, 40, 40) == 1.0


def test_hypergeometric_matches_a_known_value():
    """3 copies in 40 cards, 5 draws -> 1 - C(37,5)/C(40,5)."""
    from math import comb

    expected = 1 - comb(37, 5) / comb(40, 5)
    assert hypergeometric_at_least_one(3, 40, 5) == pytest.approx(expected)


def test_more_draws_never_lowers_the_odds():
    previous = 0.0
    for draws in range(1, 12):
        odds = hypergeometric_at_least_one(4, 40, draws)
        assert odds >= previous
        previous = odds


def test_draw_odds_use_only_the_players_own_deck(decks):
    state = midgame(decks, 14)
    odds = draw_odds(state, 0, lambda cid: DB[cid].has_reaction, draws=3)
    assert 0.0 <= odds <= 1.0


# --- determinization samples from exactly this pool -------------------------


def test_determinization_draws_from_the_deduced_unseen_pool(decks):
    """ISMCTS must sample worlds consistent with the deduction -- not from
    the real hand, and not from cards already visible on the table."""
    state = midgame(decks, 15)
    knowledge = deduce(state, 0, 1)
    for seed in range(6):
        world = determinize(state, 0, random.Random(seed))
        sampled = Counter(
            world.cards[i].card_id
            for i in world.players[1].hand + world.players[1].main_deck
        )
        assert sampled == knowledge.unseen, "sampled world contradicts public information"


def test_determinization_keeps_public_zones_identical(decks):
    state = midgame(decks, 16)
    before = public_counts(state, 1)
    world = determinize(state, 0, random.Random(3))
    assert public_counts(world, 1) == before


# --- 424 Reveal: what you were shown, you remember (RQ-16) -------------------


def reveal_a_hand_card(state, subject: int):
    """424 -- announce the first card of `subject`'s hand to all players."""
    from cards.dsl import Reveal, Who
    from cards.primitives import EffectContext, execute

    instance_id = state.players[subject].hand[0]
    execute(state, Reveal(who=Who.YOU, count=1, zone="hand"),
            EffectContext(controller=subject))
    return instance_id


def test_a_revealed_hand_card_stops_being_unseen(decks):
    """424.1 -- revealing "presents a card to all players". The observer knows
    exactly where that card is, so it is located, not part of the pool a
    determinization samples from."""
    state = midgame(decks, 8)
    subject, observer = 1, 0
    card = state.cards[state.players[subject].hand[0]].card_id

    before = deduce(state, observer, subject)
    reveal_a_hand_card(state, subject)
    after = deduce(state, observer, subject)

    assert after.unseen[card] == before.unseen[card] - 1
    assert after.located[card] == before.located[card] + 1
    assert after.known_in_hand[card] == 1


def test_a_revealed_card_is_known_to_be_in_hand_with_certainty(decks):
    state = midgame(decks, 8)
    subject, observer = 1, 0
    card = state.cards[state.players[subject].hand[0]].card_id
    reveal_a_hand_card(state, subject)

    knowledge = deduce(state, observer, subject)
    assert knowledge.probability_in_hand(card) == 1.0


def test_the_memory_expires_when_the_card_leaves_the_hand(decks):
    """424.1.a -- Revealed is a *temporary state*, not a zone. The knowledge is
    about where the card was; once it moves, it is no longer in the hand and
    the deduction must not keep claiming it is."""
    state = midgame(decks, 8)
    subject, observer = 1, 0
    instance_id = reveal_a_hand_card(state, subject)
    card = state.cards[instance_id].card_id
    assert deduce(state, observer, subject).known_in_hand[card] == 1

    state.players[subject].hand.remove(instance_id)
    state.players[subject].main_deck.append(instance_id)

    assert deduce(state, observer, subject).known_in_hand[card] == 0


def test_revealing_never_reveals_more_than_was_shown(decks):
    """Only the announced card becomes known -- not the rest of the hand."""
    state = midgame(decks, 8)
    subject, observer = 1, 0
    reveal_a_hand_card(state, subject)

    knowledge = deduce(state, observer, subject)
    assert sum(knowledge.known_in_hand.values()) == 1
    assert knowledge.hidden_hand_size == len(state.players[subject].hand) - 1


def test_a_determinization_keeps_a_revealed_card_in_the_hand(decks):
    """The search must not sample worlds it has already been shown are false.

    Without this, ISMCTS shuffles a revealed card back into the deck on most
    iterations and plans as though it had never seen it.
    """
    state = midgame(decks, 8)
    observer = 0
    instance_id = reveal_a_hand_card(state, 1)

    rng = random.Random(4)
    for _ in range(20):
        world = determinize(state, observer, rng)
        assert instance_id in world.players[1].hand
        assert len(world.players[1].hand) == len(state.players[1].hand)


def test_the_observer_sees_the_revealed_card_in_the_observation(decks):
    """The observation is the agent's whole legal view; public information the
    agent is entitled to belongs in it."""
    state = midgame(decks, 8)
    instance_id = reveal_a_hand_card(state, 1)

    view = state.observation(0)
    assert instance_id in {c.instance_id for c in view.revealed_opponent_hand}
    assert instance_id not in {c.instance_id for c in view.hand}


def test_an_unrevealed_hand_stays_out_of_the_observation(decks):
    """The no-leak direction of the same test."""
    state = midgame(decks, 8)
    view = state.observation(0)
    assert view.revealed_opponent_hand == ()
