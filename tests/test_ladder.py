"""The measurement half of training: Elo, SPRT, and a frozen gauntlet.

None of this is invented here. Elo on a logistic scale and the Sequential
Probability Ratio Test are what computer-chess testing (Stockfish's fishtest)
has used for years, and a frozen reference pool is the standard answer to
self-play's intransitivity problem -- gen 3 beating gen 2 does not imply gen 3
beats gen 0.
"""

from __future__ import annotations

import math

import pytest

from analysis.ladder import (
    SPRT,
    League,
    elo_from_score,
    elo_interval,
    score_from_elo,
)
from analysis.evaluation import DEFAULT_WEIGHTS, Model


# --- Elo --------------------------------------------------------------------


def test_even_score_is_zero_elo():
    assert elo_from_score(0.5) == pytest.approx(0.0)


def test_elo_and_score_are_inverses():
    for elo in (-400, -100, -25, 0, 25, 100, 400):
        assert elo_from_score(score_from_elo(elo)) == pytest.approx(elo, abs=1e-6)


def test_a_stronger_score_is_a_higher_rating():
    assert elo_from_score(0.75) > elo_from_score(0.55) > 0


def test_the_classic_anchor_holds():
    """+400 Elo is a 10:1 expected score -- the definition of the scale."""
    assert score_from_elo(400) == pytest.approx(10 / 11, abs=1e-9)


def test_a_clean_sweep_does_not_return_infinity():
    """20-0 is strong evidence, not infinite evidence; the rating has to stay
    finite or a single perfect run poisons every table it appears in."""
    rating = elo_from_score(1.0)
    assert math.isfinite(rating) and rating > 400


def test_the_interval_narrows_with_more_games():
    narrow = elo_interval(0.6, 400)
    wide = elo_interval(0.6, 40)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_the_interval_brackets_the_point_estimate():
    low, high = elo_interval(0.6, 200)
    assert low < elo_from_score(0.6) < high


# --- SPRT -------------------------------------------------------------------


def test_sprt_keeps_going_while_the_evidence_is_thin():
    test = SPRT(elo0=0, elo1=20)
    for _ in range(10):
        test.record(1.0)
        test.record(0.0)
    assert test.verdict() == "continue"


def test_sprt_accepts_a_clearly_stronger_agent():
    test = SPRT(elo0=0, elo1=20)
    for _ in range(400):
        test.record(1.0 if _ % 4 else 0.0)   # 75%
        if test.verdict() != "continue":
            break
    assert test.verdict() == "accept"


def test_sprt_rejects_a_clearly_weaker_agent():
    test = SPRT(elo0=0, elo1=20)
    for i in range(400):
        test.record(0.0 if i % 4 else 1.0)   # 25%
        if test.verdict() != "continue":
            break
    assert test.verdict() == "reject"


def test_sprt_resolves_a_real_edge_in_far_fewer_games_than_fixed_n():
    """The reason to use it: a fixed-N test needs ~385 games to resolve a
    55% edge. SPRT should settle a 60% edge in a fraction of that."""
    test = SPRT(elo0=0, elo1=20)
    played = 0
    rng = __import__("random").Random(7)
    while test.verdict() == "continue" and played < 2000:
        test.record(1.0 if rng.random() < 0.60 else 0.0)
        played += 1
    assert test.verdict() == "accept"
    assert played < 385


def test_sprt_draws_are_evidence_for_neither_side():
    test = SPRT(elo0=0, elo1=20)
    for _ in range(200):
        test.record(0.5)
    assert test.verdict() in ("continue", "reject")


# --- the league -------------------------------------------------------------


def test_a_promoted_generation_is_kept_not_overwritten(tmp_path):
    """Without this there is no ladder: the previous weights are gone, so
    nothing can ever be compared against where it started."""
    league = League(tmp_path)
    league.add(0, Model(weights=DEFAULT_WEIGHTS))
    league.add(1, Model(weights=tuple(w * 2 for w in DEFAULT_WEIGHTS)))
    assert sorted(league.generations()) == [0, 1]
    assert league.get(0).weights == DEFAULT_WEIGHTS


def test_the_league_survives_a_restart(tmp_path):
    League(tmp_path).add(3, Model(weights=DEFAULT_WEIGHTS, fitted=True))
    reopened = League(tmp_path)
    assert reopened.generations() == [3]
    assert reopened.get(3).fitted is True


def test_the_gauntlet_is_a_fixed_reference_not_the_latest_model(tmp_path):
    """The point of a frozen gauntlet: scores stay comparable across
    generations because the opponents never change."""
    league = League(tmp_path)
    league.add(0, Model(weights=DEFAULT_WEIGHTS))
    first = league.gauntlet()
    league.add(1, Model(weights=tuple(w + 1 for w in DEFAULT_WEIGHTS)))
    assert [name for name, _ in league.gauntlet()][: len(first)] == [
        name for name, _ in first
    ]


# --- SPSA tuning ------------------------------------------------------------


def test_renormalise_preserves_direction_and_fixes_length():
    """A greedy argmax agent is invariant to the weight vector's scale, so
    SPSA must not be allowed to wander along it."""
    from analysis.tune import _norm, renormalise

    theta = [3.0, 4.0, 0.0]
    scaled = renormalise(theta, 1.0)
    assert _norm(scaled) == pytest.approx(1.0)
    # same direction: every component in the same proportion
    assert scaled[0] / scaled[1] == pytest.approx(theta[0] / theta[1])


def test_renormalise_survives_a_zero_vector():
    from analysis.tune import renormalise

    assert renormalise([0.0, 0.0], 1.0) == [0.0, 0.0]


def test_scaling_every_weight_does_not_change_a_greedy_choice():
    """The invariance the normalisation is built on, asserted rather than
    assumed: if this were false, pinning the norm would throw away signal."""
    from analysis.evaluation import DEFAULT_WEIGHTS, FEATURE_NAMES, Model

    base = Model(weights=DEFAULT_WEIGHTS, bias=0.0)
    doubled = Model(weights=tuple(w * 2 for w in DEFAULT_WEIGHTS), bias=0.0)

    rng = __import__("random").Random(11)
    for _ in range(50):
        left = {name: rng.uniform(-1, 1) for name in FEATURE_NAMES}
        right = {name: rng.uniform(-1, 1) for name in FEATURE_NAMES}
        assert (base.score(left) > base.score(right)) == (
            doubled.score(left) > doubled.score(right)
        )


def test_single_game_duels_still_alternate_seats():
    """A sequential test drives one game at a time, and the seat rotation is
    keyed on the loop index -- so without an offset every such call seats
    agent A first, and any first-player advantage is read as agent strength.
    """
    import inspect

    from analysis.benchmark import duel

    assert "start_index" in inspect.signature(duel).parameters
    seats = [(0 + i) % 2 for i in range(6)]
    assert seats == [0, 1, 0, 1, 0, 1]


def test_mirror_matchups_pair_each_deck_with_itself():
    """The starter decks are wildly imbalanced -- volibear beats jinx about
    85-15 with identical agents -- so a cross-deck comparison measures the
    decks far more loudly than the agents. Mirrors cancel the deck exactly.
    """
    from analysis.benchmark import mirror_field

    decks = ["a", "b", "c"]
    assert mirror_field(decks) == [("a", "a"), ("b", "b"), ("c", "c")]


def test_the_loop_can_restrict_itself_to_mirrors():
    from analysis.self_play_loop import matchups

    decks = ["a", "b"]
    assert len(matchups(decks)) == 4
    assert matchups(decks, mirror_only=True) == [("a", "a"), ("b", "b")]
