"""Cloning a state must be a *copy*, not a view onto the same game.

Search clones the state once per legal action, so this is the hottest code in
the project -- 89% of a greedy game's runtime before `RiftboundState` grew a
targeted `__deepcopy__`. A hand-written clone is fast and is exactly the kind
of code that silently shares one mutable field and corrupts every search that
touches it, so it is pinned from both directions: nothing is shared that
should not be, and nothing is copied that must stay shared.
"""

from __future__ import annotations

import copy

import pytest

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from analysis.evaluation import load_model
from cards.database import load as load_db
from engine.replay import state_hash
from engine.setup import build_state, load_deck

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


# --- nothing mutable is shared ---------------------------------------------


def test_a_clone_starts_identical(decks):
    state = midgame(decks)
    assert state_hash(copy.deepcopy(state)) == state_hash(state)


def test_mutating_every_mutable_field_leaves_the_original_alone(decks):
    """One field-by-field sweep. If a future field is added to the state and
    not to `__deepcopy__`, it will almost certainly be caught here."""
    state = midgame(decks)
    before = state_hash(state)
    clone = copy.deepcopy(state)

    for player in clone.players:
        player.hand.append(999_001)
        player.main_deck.append(999_002)
        player.trash.append(999_003)
        player.base.append(999_004)
        player.channeled_runes.append(999_005)
        player.banishment.append(999_006)
        player.champion_zone.append(999_007)
        player.points += 7
        player.pool.energy += 11
        player.pool.power["Fury"] = 99
        player.pool.universal_power += 3
    for ref in clone.cards.values():
        ref.damage += 5
        ref.exhausted = not ref.exhausted
        ref.location = "bf:9"
        ref.might_this_turn += 4
        ref.attached_to = 123
    for bf in clone.battlefields:
        bf.controller = 1
        bf.contested = True
        bf.scored_by.add(1)
    clone.log.append("scribble")
    clone.chain.append(object())
    clone.units_enter_ready.add(1)
    clone.accelerated.add(1)
    clone.turn_number += 3
    clone.winner = 0

    assert state_hash(state) == before


def test_the_clones_random_stream_is_its_own(decks):
    """Two clones must produce the same numbers, and neither may advance the
    original -- otherwise one search branch eats another's randomness."""
    state = midgame(decks)
    first = [copy.deepcopy(state)._rng.random() for _ in range(3)]
    second = [copy.deepcopy(state)._rng.random() for _ in range(3)]
    assert first == second
    assert state._rng.random() == first[0]


def test_the_card_database_is_shared_not_copied(decks):
    """The one thing that must *not* be deep-copied: it is immutable, and
    copying 908 cards per clone is what made cloning slow in the first place."""
    state = midgame(decks)
    assert copy.deepcopy(state).db is state.db


def test_effect_context_payloads_are_not_shared(decks):
    """`EffectContext.payload` is the only mutable field hiding inside the
    pending-effect queue."""
    from cards.dsl import Draw
    from cards.primitives import EffectContext

    state = midgame(decks)
    state.pending.append((Draw(1), EffectContext(controller=0, payload={"n": 1})))
    clone = copy.deepcopy(state)
    clone.pending[-1][1].payload["n"] = 99
    assert state.pending[-1][1].payload["n"] == 1


# --- behaviour is unchanged ------------------------------------------------


def test_a_clone_plays_out_identically(decks):
    """The property that matters to search: continuing from a clone gives the
    same game as continuing from the original."""
    state = midgame(decks)
    clone = copy.deepcopy(state)

    def play_out(start, seed):
        agent = RandomAgent(seed)
        for _ in range(60):
            if start.is_terminal():
                break
            start.apply(agent.act(start))
        return state_hash(start), list(start.log)

    assert play_out(state, 3) == play_out(clone, 3)


def test_a_full_greedy_game_is_unaffected_by_cloning(decks):
    """Greedy clones once per legal action, so if cloning were subtly wrong
    this game would diverge from one played by an agent that never clones."""
    model = load_model()
    state = build_state(decks[0], decks[1], seed=11, db=DB)
    agents = (GreedyAgent(11, model), GreedyAgent(12, model))
    for _ in range(400):
        if state.is_terminal():
            break
        state.apply(agents[state.current_player].act(state))
    assert state.is_terminal()
    assert len(state.log) > 20


def test_every_card_ref_field_is_immutable():
    """`CardRef.copy` copies the instance dict wholesale, which is correct
    only while every field's value is immutable. Adding a list, dict or set
    field would alias it into every clone in every search -- so this fails
    the moment one appears, rather than corrupting results quietly.
    """
    from dataclasses import fields

    from engine.zones import CardRef

    immutable = (int, float, str, bool, tuple, frozenset, type(None))
    ref = CardRef(instance_id=1, card_id="X", owner=0, controller=0)
    for spec in fields(ref):
        value = getattr(ref, spec.name)
        assert isinstance(value, immutable), (
            f"CardRef.{spec.name} is {type(value).__name__}, which is mutable; "
            f"CardRef.copy() would alias it into every clone"
        )


def test_every_on_board_card_is_in_exactly_one_zone_list(decks):
    """An invariant, and a load-bearing one.

    `legal_actions` enumerates a player's activated abilities from their zone
    lists rather than by scanning all ~108 card instances, which is only
    correct while those lists account for every card with a location. It also
    catches a class of bug directly: a killed Equipment that kept its
    `attached_to` was dragged out of the trash onto a battlefield by the rule
    that moves attached cards with their host (719.3.a), leaving a card on the
    board that belonged to no zone at all.
    """
    from agents.random_agent import RandomAgent

    for seed in (3, 7, 11):
        state = build_state(decks[0], decks[1], seed=seed, db=DB)
        agent = RandomAgent(seed)
        for _ in range(220):
            if state.is_terminal():
                break
            state.apply(agent.act(state))
            for player in (0, 1):
                zones = state.players[player]
                tracked = set(zones.base) | set(zones.channeled_runes)
                on_board = {
                    ref.instance_id
                    for ref in state.cards.values()
                    if ref.controller == player and ref.location is not None
                }
                assert tracked == on_board, (
                    f"seed {seed} P{player}: on board but in no zone list "
                    f"{on_board - tracked}; in a zone list but not on the board "
                    f"{tracked - on_board}"
                )
