"""Threat features and ISMCTS.

The judgments the repo owner described -- deny the 8th point but not the
first, sweep when behind, hold a Reaction for something worth answering -- are
mostly *conditional*, and a weighted sum of features cannot represent a
condition. These tests pin the one part that is representable (a threshold),
and the search that represents the rest.
"""

from __future__ import annotations

import copy
import random

import pytest

from agents.ismcts import ISMCTSAgent, Node, determinize
from agents.random_agent import RandomAgent
from analysis.evaluation import (
    DEFAULT_WEIGHTS,
    FEATURE_NAMES,
    Model,
    evaluate,
    features,
    load_model,
)
from cards.database import load as load_db
from engine.setup import build_state, load_deck
from engine.state import VICTORY_SCORE, Phase, bf_location
from engine.zones import CardRef

DB = load_db()
PRIOR = Model()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def midgame(decks, seed=1, steps=80):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    return state


# --- the threat threshold ---------------------------------------------------


def test_feature_vector_and_weights_stay_aligned():
    assert len(FEATURE_NAMES) == len(DEFAULT_WEIGHTS)


def test_victory_pressure_depends_on_the_board_not_just_the_score():
    """The point the repo owner made: 7 points with no board is less urgent
    than 5 points holding both battlefields, because control is the rate at
    which points arrive (469.2)."""
    from analysis.evaluation import _pressure

    assert _pressure(5, 2) > _pressure(7, 0)
    assert _pressure(7, 2) > _pressure(7, 1) > _pressure(7, 0)
    assert _pressure(7, 1) > _pressure(3, 1)


def test_control_lengthens_or_shortens_the_clock_at_a_fixed_score():
    """Board state moves the urgency at every score, which is the whole point
    of measuring in turns rather than points."""
    from analysis.evaluation import _pressure

    # Losing the board slows a 7-point player down without making them safe.
    assert _pressure(7, 0) < _pressure(7, 2)
    # And a full board does not make a player at 0 urgent -- that is 4 turns away.
    assert _pressure(0, 2) < _pressure(7, 0)


def test_victory_pressure_is_maximal_once_the_score_is_reached():
    from analysis.evaluation import _pressure

    assert _pressure(VICTORY_SCORE, 0) == 1.0


def test_an_opponent_about_to_win_collapses_the_evaluation(decks):
    state = midgame(decks, 3)
    state.players[0].points = state.players[1].points = 4
    for bf in state.battlefields:
        bf.controller = None
    even = evaluate(state, 0, PRIOR)
    state.players[1].points = VICTORY_SCORE - 1
    for bf in state.battlefields:
        bf.controller = 1          # and they hold the board, so it is a real clock
    threatened = evaluate(state, 0, PRIOR)
    assert threatened < even - 0.2, "an imminent loss must dominate, not nudge"


def test_early_points_matter_less_than_late_ones(decks):
    """'The first point often cannot be prevented and that is fine.'"""
    state = midgame(decks, 4)
    state.players[0].points = state.players[1].points = 0
    base = evaluate(state, 0, PRIOR)
    state.players[1].points = 1
    after_first = evaluate(state, 0, PRIOR)
    state.players[1].points = VICTORY_SCORE - 1
    after_seventh = evaluate(state, 0, PRIOR)
    # (board held constant, so only the score is moving)
    assert (base - after_first) < (after_first - after_seventh), (
        "conceding the first point must cost far less than the seventh"
    )


def test_threat_features_keep_the_evaluation_zero_sum(decks):
    for seed in range(4):
        state = midgame(decks, seed)
        assert evaluate(state, 0, PRIOR) + evaluate(state, 1, PRIOR) == pytest.approx(1.0)


# --- determinization respects hidden information ----------------------------


def test_determinization_preserves_what_the_searcher_can_see(decks):
    """128 Privacy -- own hand and the board are known, the rest is not."""
    state = midgame(decks, 5)
    sampled = determinize(state, 0, random.Random(0))
    assert sampled.players[0].hand == state.players[0].hand
    assert len(sampled.players[1].hand) == len(state.players[1].hand)
    assert len(sampled.cards) == len(state.cards)
    board_before = {r.instance_id for r in state.cards.values() if r.location}
    board_after = {r.instance_id for r in sampled.cards.values() if r.location}
    assert board_before == board_after, "the board is public and must not move"


def test_determinization_actually_resamples_the_opponent_hand(decks):
    state = midgame(decks, 6)
    seen = {
        tuple(determinize(state, 0, random.Random(s)).players[1].hand)
        for s in range(8)
    }
    assert len(seen) > 1, "every determinization identical means no sampling"


def test_determinization_does_not_mutate_the_real_state(decks):
    state = midgame(decks, 7)
    before = state.observation(0).to_canonical_bytes()
    determinize(state, 0, random.Random(1))
    assert state.observation(0).to_canonical_bytes() == before


def test_determinized_worlds_conserve_the_opponents_cards(decks):
    """Cards may be reshuffled between hand and deck, never created or lost."""
    state = midgame(decks, 8)
    sampled = determinize(state, 0, random.Random(2))
    before = sorted(state.players[1].hand + state.players[1].main_deck)
    after = sorted(sampled.players[1].hand + sampled.players[1].main_deck)
    assert before == after


# --- the search itself ------------------------------------------------------


def test_ismcts_returns_a_legal_action(decks):
    state = midgame(decks, 9)
    if state.is_terminal():
        pytest.skip("game ended")
    action = ISMCTSAgent(9, iterations=30, model=PRIOR).act(state)
    assert action in state.legal_actions()


def test_ismcts_does_not_mutate_the_state(decks):
    state = midgame(decks, 10)
    before = state.observation(0).to_canonical_bytes()
    ISMCTSAgent(10, iterations=30, model=PRIOR).act(state)
    assert state.observation(0).to_canonical_bytes() == before


def test_ismcts_is_deterministic_for_a_seed(decks):
    state = midgame(decks, 11)
    a = ISMCTSAgent(11, iterations=40, model=PRIOR).act(copy.deepcopy(state))
    b = ISMCTSAgent(11, iterations=40, model=PRIOR).act(copy.deepcopy(state))
    assert repr(a) == repr(b)


def test_ismcts_availability_is_counted_not_just_visits():
    """ISMCTS divides by availability, so an action legal in only some
    determinizations is not punished for the iterations it never appeared in."""
    node = Node()
    node.availability["play:1"] = 10
    node.children["play:1"] = Node(visits=2, total_value=1.0)
    assert node.children["play:1"].value() == pytest.approx(0.5)
    assert node.availability["play:1"] == 10


def test_ismcts_plays_a_full_game_without_error(decks):
    state = build_state(decks[0], decks[1], seed=12, db=DB)
    search = ISMCTSAgent(12, iterations=15, model=PRIOR)
    opponent = RandomAgent(13)
    steps = 0
    while not state.is_terminal() and steps < 3000:
        agent = search if state.current_player == 0 else opponent
        state.apply(agent.act(state))
        steps += 1
    assert state.is_terminal()
    assert sum(state.returns()) == pytest.approx(1.0)


def test_ismcts_takes_the_win_when_it_is_available(decks):
    """Search should not need a heuristic to see a won position through."""
    state = midgame(decks, 14)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(RandomAgent(14).act(state))
    if state.is_terminal():
        pytest.skip("game ended during setup")
    player = state.turn_player
    state.players[player].points = VICTORY_SCORE - 1
    state.players[1 - player].points = 0
    for bf in state.battlefields:
        bf.controller = player
        bf.scored_by.clear()
        # 323.6 -- control without a garrison is taken away at the next
        # cleanup, so a "holding a battlefield" position needs a unit on it.
        instance_id = max(state.cards) + 1
        state.cards[instance_id] = CardRef(
            instance_id=instance_id, card_id="OGN-142", owner=player,
            controller=player, location=bf_location(bf.index),
        )
        state.players[player].base.append(instance_id)

    agent = ISMCTSAgent(15, iterations=30, model=PRIOR)
    for _ in range(80):
        if state.is_terminal():
            break
        state.apply(agent.act(state))
    assert state.is_terminal() and state.winner == player


# --- MCTS backup perspective ------------------------------------------------


def test_value_for_flips_perspective_for_the_other_seat():
    """`evaluate` is antisymmetric, so the other seat's valuation is 1 - v."""
    from agents.ismcts import value_for

    assert value_for(0.7, player=0, scored_for=0) == pytest.approx(0.7)
    assert value_for(0.7, player=1, scored_for=0) == pytest.approx(0.3)
    assert value_for(0.5, player=1, scored_for=0) == pytest.approx(0.5)


def test_opponent_nodes_are_scored_from_the_opponents_side(decks, monkeypatch):
    """The bug this guards: every node used to be backed up from the
    searching player's point of view, so nodes where the *opponent* chooses
    were selected to maximise the searcher's value -- the search assumed the
    opponent would help it. More iterations then bought a more confidently
    wrong plan, and ISMCTS(60) scored 0.350 (14-26) against the very greedy
    agent it is built on.

    Pinned by fixing the leaf value at a lopsided constant and walking the
    tree: every node must hold a multiple of that value or of its complement,
    and at least one opponent-move node must hold the complement.
    """
    import agents.ismcts as ismcts

    leaf = 0.8
    monkeypatch.setattr(ismcts, "evaluate", lambda state, player, model: leaf)

    state = midgame(decks, seed=4, steps=70)
    if state.is_terminal():
        pytest.skip("game ended before a midgame position was reached")

    agent = ismcts.ISMCTSAgent(1, iterations=120, model=load_model())
    root = ismcts.Node()
    for _ in range(agent.iterations):
        agent._iterate(root, ismcts.determinize(state, state.current_player,
                                                agent._rng),
                       state.current_player)

    seen_complement = False
    stack = list(root.children.values())
    while stack:
        node = stack.pop()
        if node.visits:
            mine = abs(node.value() - leaf) < 1e-9
            theirs = abs(node.value() - (1 - leaf)) < 1e-9
            # A node can mix perspectives only if the same action key is
            # reached with different players to move, which the engine's
            # action vocabulary does not do at these depths.
            assert mine or theirs, (
                f"node value {node.value():.3f} is neither {leaf} nor "
                f"{1 - leaf}: the backup is not using a consistent side"
            )
            seen_complement |= theirs
        stack.extend(node.children.values())
    assert seen_complement, (
        "no node was scored from the opponent's side, so opponent decisions "
        "are still being selected to maximise the searcher's value"
    )


def test_a_prior_seeds_a_new_node_with_the_evaluation(decks, monkeypatch):
    """With a 60-iteration budget spread over a dozen actions, most nodes are
    decided on one or two samples. A prior starts them from what the
    evaluation already knows; search then has to earn any departure from it."""
    import agents.ismcts as ismcts

    monkeypatch.setattr(ismcts, "evaluate", lambda state, player, model: 0.8)
    state = midgame(decks, seed=4, steps=70)
    if state.is_terminal():
        pytest.skip("game ended before a midgame position was reached")

    plain = ismcts.ISMCTSAgent(1, iterations=1, model=load_model(), prior=0)
    seeded = ismcts.ISMCTSAgent(1, iterations=1, model=load_model(), prior=3)

    def visits(agent):
        root = ismcts.Node()
        agent._iterate(root, ismcts.determinize(state, state.current_player,
                                                agent._rng),
                       state.current_player)
        return max(c.visits for c in root.children.values())

    assert visits(seeded) == visits(plain) + 3


def test_the_prior_is_off_by_default():
    """It costs an extra evaluate per expansion, so it ships measured or not
    at all."""
    from agents.ismcts import DEFAULT_PRIOR, ISMCTSAgent

    assert DEFAULT_PRIOR == 0
    assert ISMCTSAgent(1).prior == 0
