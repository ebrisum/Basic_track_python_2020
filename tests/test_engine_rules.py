"""Rules tests for the Riftbound engine.

Each test names the rule it pins. These are the regression net for rules drift
in the parts the replay harness cannot reach on its own.
"""

from __future__ import annotations

import copy
import random

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.keywords import Keyword, parse
from engine.actions import PassPhase, PlayCard, StandardMove
from engine.interface import Action, GameState, Observation
from engine.setup import (
    MIN_MAIN_DECK,
    RUNE_DECK_SIZE,
    Deck,
    DeckError,
    build_state,
    load_deck,
    roll_for_first_player,
    validate,
)
from engine.state import (
    OPENING_HAND,
    VICTORY_SCORE,
    Phase,
    bf_location,
)
from engine.zones import BASE_LOCATION, RunePool

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def fresh(decks, seed=1):
    return build_state(decks[0], decks[1], seed=seed, db=DB)


def play_out(state, seed=0, cap=20_000):
    agent = RandomAgent(seed)
    steps = 0
    while not state.is_terminal():
        assert steps < cap, "game failed to terminate"
        state.apply(agent.act(state))
        steps += 1
    return steps


# --- interface contract, now against the real state ------------------------


def test_riftbound_state_satisfies_the_frozen_protocols(decks):
    state = fresh(decks)
    assert isinstance(state, GameState)
    assert isinstance(state.legal_actions()[0], Action)
    assert isinstance(state.observation(0), Observation)


def test_legal_actions_are_canonical_and_unique(decks):
    state = fresh(decks)
    agent = RandomAgent(3)
    for _ in range(200):
        if state.is_terminal():
            break
        actions = state.legal_actions()
        assert actions == sorted(actions)
        reprs = [repr(a) for a in actions]
        assert len(set(reprs)) == len(reprs)
        state.apply(agent.act(state))


def test_state_is_deep_copyable_and_independent(decks):
    state = fresh(decks)
    clone = copy.deepcopy(state)
    clone.apply(clone.legal_actions()[0])
    assert state.observation(0).to_canonical_bytes() != clone.observation(0).to_canonical_bytes()


@pytest.mark.parametrize("seed", range(6))
def test_games_terminate_and_return_zero_sum(decks, seed):
    state = fresh(decks, seed)
    play_out(state, seed)
    assert state.is_terminal()
    assert sum(state.returns()) == pytest.approx(1.0)


def test_same_seed_reproduces_identical_game(decks):
    def trace(seed):
        state = fresh(decks, seed)
        agent = RandomAgent(seed)
        out = []
        while not state.is_terminal():
            action = agent.act(state)
            out.append(repr(action))
            state.apply(action)
        return out

    assert trace(11) == trace(11)


def test_global_rng_does_not_affect_the_game(decks):
    random.seed(1)
    state = fresh(decks, 5)
    first = play_out(state, 5)
    random.seed(999)
    [random.random() for _ in range(100)]
    state2 = fresh(decks, 5)
    assert play_out(state2, 5) == first


# --- setup (110-118, 485) ---------------------------------------------------


def test_die_roll_breaks_ties_and_picks_a_first_player():
    """115 -- any fair random method; ties are rerolled."""
    for seed in range(50):
        a, b, first = roll_for_first_player(random.Random(seed))
        assert a != b
        assert first == (0 if a > b else 1)


def test_setup_starts_with_battlefield_selection(decks):
    """485.5 -- each player selects 1 of their 3 battlefields."""
    state = fresh(decks)
    assert state.phase is Phase.SETUP_BATTLEFIELD
    assert len(state.legal_actions()) == 3
    assert state.current_player == state.first_player


def test_both_players_choose_then_hands_are_dealt(decks):
    """116 -- each player draws 4."""
    state = fresh(decks)
    state.apply(state.legal_actions()[0])
    state.apply(state.legal_actions()[0])
    assert len(state.battlefields) == 2
    assert state.phase is Phase.SETUP_MULLIGAN
    assert all(len(p.hand) == OPENING_HAND for p in state.players)


def test_mulligan_sets_aside_draws_then_recycles(decks):
    """117.1-117.3 -- up to two cards, redrawn, then recycled to the deck."""
    state = fresh(decks)
    state.apply(state.legal_actions()[0])
    state.apply(state.legal_actions()[0])
    player = state.current_player
    deck_before = len(state.players[player].main_deck)

    mulligans = [a for a in state.legal_actions() if len(getattr(a, "instance_ids", ())) == 2]
    set_aside = mulligans[0].instance_ids
    state.apply(mulligans[0])

    hand = state.players[player].hand
    assert len(hand) == OPENING_HAND  # drew as many as set aside
    assert len(state.players[player].main_deck) == deck_before  # -2 drawn, +2 recycled
    for instance_id in set_aside:
        assert instance_id not in hand
        assert state.players[player].main_deck[-1] in state.players[player].main_deck


def test_mulligan_offers_at_most_two_cards(decks):
    """117.1 -- 'up to two cards'."""
    state = fresh(decks)
    state.apply(state.legal_actions()[0])
    state.apply(state.legal_actions()[0])
    sizes = {len(a.instance_ids) for a in state.legal_actions()}
    assert sizes == {0, 1, 2}


def test_going_second_channels_an_extra_rune_once(decks):
    """485.7 -- the player going second channels +1 on their first Channel."""
    state = fresh(decks)
    for _ in range(4):
        state.apply(state.legal_actions()[0])
    second = state.opponent(state.first_player)
    # Run until the second player has had their first channel.
    agent = RandomAgent(2)
    while not state.players[second].has_channeled and not state.is_terminal():
        state.apply(agent.act(state))
    assert len(state.players[second].channeled_runes) >= 3


# --- resources (163-168, 316.3) ---------------------------------------------


def test_energy_and_power_are_separate_resources():
    """163 -- energy has no domain; power is domain-associated."""
    pool = RunePool(energy=2)
    pool.add_power("Fury")
    assert pool.can_pay(2, ["Fury"])
    assert not pool.can_pay(3, [])
    assert not pool.can_pay(0, ["Calm"])


def test_universal_power_pays_any_domain():
    """163.2.b."""
    pool = RunePool(energy=0, universal_power=1)
    assert pool.can_pay(0, ["Chaos"])
    pool.pay(0, ["Chaos"])
    assert pool.universal_power == 0


def test_domain_power_is_spent_before_universal():
    pool = RunePool(universal_power=1)
    pool.add_power("Body")
    pool.pay(0, ["Body"])
    assert pool.universal_power == 1
    assert pool.power.get("Body", 0) == 0


def test_pool_empties_at_main_phase_start(decks):
    """316.3 -- unspent Energy and Power are lost."""
    pool = RunePool(energy=5, universal_power=2)
    pool.add_power("Fury")
    pool.clear()
    assert pool.energy == 0 and pool.total_power() == 0


def test_paying_more_than_the_pool_raises():
    with pytest.raises(ValueError):
        RunePool(energy=1).pay(2, [])


# --- turn structure ---------------------------------------------------------


def test_awaken_readies_only_the_turn_players_objects(decks):
    """315.1."""
    state = fresh(decks)
    agent = RandomAgent(4)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    for ref in state.cards.values():
        if ref.controller == state.turn_player and ref.location is not None:
            assert not ref.exhausted


def test_channel_moves_runes_from_rune_deck_to_board(decks):
    """315.3 -- channel 2 from the Rune Deck."""
    state = fresh(decks)
    agent = RandomAgent(6)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    player = state.turn_player
    assert len(state.players[player].channeled_runes) >= 2
    assert len(state.players[player].rune_deck) <= RUNE_DECK_SIZE - 2


# --- movement (144, 190, 450) -----------------------------------------------


def reach_main(decks, seed=1):
    state = fresh(decks, seed)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    return state


def test_standard_move_exhausts_the_unit_and_contests(decks):
    """144.2 -- exhausting is the cost. 450 -- destination becomes Contested."""
    state = reach_main(decks, 8)
    agent = RandomAgent(8)
    for _ in range(400):
        if state.is_terminal():
            pytest.skip("game ended before a move happened")
        moves = [
            a
            for a in state.legal_actions()
            if isinstance(a, StandardMove) and a.destination != BASE_LOCATION
        ]
        if moves:
            move = moves[0]
            state.apply(move)
            ref = state.cards[move.instance_id]
            assert ref.exhausted
            assert ref.location == move.destination
            index = int(move.destination.split(":")[1])
            bf = state.battlefields[index]
            assert bf.controller == ref.controller or bf.contested
            return
        state.apply(agent.act(state))
    pytest.skip("no move became available")


def test_exhausted_units_cannot_standard_move(decks):
    state = reach_main(decks, 9)
    for ref in state.cards.values():
        if ref.controller == state.turn_player and ref.location is not None:
            ref.exhausted = True
    moves = [a for a in state.legal_actions() if isinstance(a, StandardMove)]
    assert moves == []


# --- scoring (467-471) ------------------------------------------------------


def test_victory_requires_more_points_than_the_opponent(decks):
    """194.2 -- >= Victory Score AND strictly more than any other player."""
    state = reach_main(decks, 12)
    state.players[0].points = VICTORY_SCORE
    state.players[1].points = VICTORY_SCORE
    state._cleanup()
    assert not state.is_terminal(), "a tie at the victory score must not win"
    state.players[0].points += 1
    state._cleanup()
    assert state.is_terminal() and state.winner == 0


def test_score_is_once_per_battlefield_per_turn(decks):
    """470."""
    state = reach_main(decks, 13)
    bf = state.battlefields[0]
    before = state.players[0].points
    state._score(0, bf, "Hold")
    state._score(0, bf, "Hold")
    assert state.players[0].points == before + 1


def test_final_point_needs_every_battlefield_scored(decks):
    """471.1.b -- otherwise the player draws instead of taking the point."""
    state = reach_main(decks, 14)
    state.players[0].points = VICTORY_SCORE - 1
    hand_before = len(state.players[0].hand)
    bf = state.battlefields[0]
    bf.scored_by.clear()
    state.battlefields[1].scored_by.clear()

    state._score(0, bf, "Conquer")
    assert state.players[0].points == VICTORY_SCORE - 1, "must not take the point"
    assert len(state.players[0].hand) == hand_before + 1, "must draw instead"


def test_final_point_is_taken_when_all_battlefields_scored(decks):
    state = reach_main(decks, 15)
    state.players[0].points = VICTORY_SCORE - 1
    for bf in state.battlefields:
        bf.scored_by.add(0)
    bf = state.battlefields[0]
    bf.scored_by.discard(0)
    state._score(0, bf, "Conquer")
    assert state.players[0].points == VICTORY_SCORE


# --- combat (459-466, 815, 826) --------------------------------------------


def test_lethal_damage_is_damage_at_or_above_might(decks):
    """142.4.a."""
    state = reach_main(decks, 16)
    unit = next(
        r for r in state.cards.values() if DB[r.card_id].type == "unit"
    )
    might = DB[unit.card_id].might
    unit.damage = might - 1
    assert not state._has_lethal(unit)
    unit.damage = might
    assert state._has_lethal(unit)


def test_zero_damage_is_never_lethal(decks):
    """142.4.a -- 'nonzero damage'."""
    state = reach_main(decks, 17)
    unit = next(r for r in state.cards.values() if DB[r.card_id].type == "unit")
    unit.damage = 0
    assert not state._has_lethal(unit)


# --- keywords (804-829) -----------------------------------------------------


def test_keyword_parser_reads_printed_parameters():
    assert parse("ASSAULT 3 (+3 Might while it's an attacker.)") == (Keyword("Assault", 3),)


def test_keyword_parser_recovers_a_value_from_reminder_text():
    """Chemtech Enforcer prints 'ASSAULT' with the value only in the reminder."""
    assert parse("ASSAULT (+2 Might while I'm an attacker.)") == (Keyword("Assault", 2),)


def test_keyword_parser_ignores_keywords_inside_reminder_text_only():
    keywords = parse("TANK (I must be assigned combat damage first.)")
    assert [k.name for k in keywords] == ["Tank"]


def test_assault_raises_might_only_while_attacking(decks):
    """807."""
    state = reach_main(decks, 18)
    unit = next(
        (r for r in state.cards.values() if DB[r.card_id].assault > 0), None
    )
    if unit is None:
        pytest.skip("no Assault unit in these decks")
    base = DB[unit.card_id].might
    unit.is_attacker = False
    assert state.might_of(unit) == base
    unit.is_attacker = True
    assert state.might_of(unit) == base + DB[unit.card_id].assault


# --- deck construction (101-103) -------------------------------------------


def test_starter_decks_are_legal(decks):
    for deck in decks:
        assert validate(deck, DB) == []


def test_short_main_deck_is_rejected(decks):
    bad = Deck(**{**decks[0].__dict__, "main": decks[0].main[:10]})
    assert any("main deck" in p for p in validate(bad, DB))


def test_wrong_rune_count_is_rejected(decks):
    bad = Deck(**{**decks[0].__dict__, "runes": decks[0].runes[:5]})
    assert any("rune deck" in p for p in validate(bad, DB))


def test_four_copies_of_a_card_is_rejected(decks):
    name_id = decks[0].main[0]
    bad = Deck(**{**decks[0].__dict__, "main": [name_id] * 4 + decks[0].main[4:]})
    assert any("copies" in p for p in validate(bad, DB))


def test_build_state_rejects_an_illegal_deck(decks):
    bad = Deck(**{**decks[0].__dict__, "runes": []})
    with pytest.raises(DeckError):
        build_state(bad, decks[1], seed=1, db=DB)


# --- observation privacy (128) ---------------------------------------------


def test_observation_hides_the_opponents_hand(decks):
    state = reach_main(decks, 19)
    observation = state.observation(0)
    own = {c.instance_id for c in observation.hand}
    assert own == set(state.players[0].hand)
    assert observation.opponent_hand_size == len(state.players[1].hand)
    serialized = observation.to_canonical_bytes()
    for instance_id in state.players[1].hand:
        assert f'"instance_id":{instance_id},'.encode() not in serialized.replace(b" ", b"")


def test_observation_does_not_leak_deck_order(decks):
    state = reach_main(decks, 20)
    observation = state.observation(0)
    assert observation.deck_sizes[0] == len(state.players[0].main_deck)
    assert not hasattr(observation, "main_deck")


def test_observation_serialization_is_stable(decks):
    state = reach_main(decks, 21)
    assert state.observation(0).to_canonical_bytes() == state.observation(0).to_canonical_bytes()


def test_log_wording_does_not_affect_the_state_hash(decks):
    """The log is presentation; hashing it would make replays wording-dependent."""
    state = reach_main(decks, 22)
    before = state.observation(0).to_canonical_bytes()
    state.log.append("cosmetic message")
    assert state.observation(0).to_canonical_bytes() == before


# --- illegal actions --------------------------------------------------------


def test_applying_an_illegal_action_raises(decks):
    state = fresh(decks)
    with pytest.raises(ValueError):
        state.apply(PlayCard(99999))


def test_terminal_state_rejects_actions(decks):
    state = fresh(decks, 2)
    play_out(state, 2)
    with pytest.raises(ValueError):
        state.apply(PassPhase())


def test_concede_is_off_by_default_and_on_for_interactive_play(decks):
    """649 -- real, but a random policy that concedes makes statistics junk."""
    state = reach_main(decks, 23)
    assert all(repr(a) != "concede" for a in state.legal_actions())
    state.allow_concede = True
    assert any(repr(a) == "concede" for a in state.legal_actions())


# --- observation exposes the zones the table needs (107-108) ---------------


def test_observation_exposes_exactly_one_legend_and_champion_per_player(decks):
    """107.4 / 108.3 -- one Champion Legend, one Chosen Champion, each."""
    state = fresh(decks)
    observation = state.observation(0)
    assert len(observation.legend) == 2
    assert all(card is not None for card in observation.legend)
    assert all(card.type == "legend" for card in observation.legend)
    assert len(observation.champion_zone) == 2
    assert all(card is not None for card in observation.champion_zone)
    assert all(card.is_champion for card in observation.champion_zone)


def test_both_trashes_are_public(decks):
    """108.2.d -- trash contents, not just counts."""
    state = reach_main(decks, 24)
    state.players[1].trash.append(state.players[1].main_deck.pop(0))
    observation = state.observation(0)
    assert len(observation.trash[1]) == len(state.players[1].trash)
    assert observation.trash_sizes[1] == len(state.players[1].trash)


def test_champion_zone_empties_once_the_champion_is_played(decks):
    """108.3.c -- the Chosen Champion cannot return here by normal means."""
    state = reach_main(decks, 25)
    player = state.turn_player
    champion = state.players[player].champion_zone[0]
    state.players[player].pool.energy = 20
    state.players[player].pool.universal_power = 20
    state._current_player = player
    state.apply(PlayCard(champion))
    assert state.players[player].champion_zone == []
    assert state.observation(player).champion_zone[player] is None


def test_observation_carries_card_art_and_current_might(decks):
    state = reach_main(decks, 26)
    board = state.observation(0).board
    assert board, "expected runes on the board by the first main phase"
    assert any(c.image_url for c in board), "card art must reach the UI"
    for card in board:
        assert card.current_might >= 0
