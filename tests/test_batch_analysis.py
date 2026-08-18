"""Batch runner: aggregation correctness and the Milestone 1 speed budget."""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from analysis.batch import BatchResult, GameResult, run_batch
from tests.fixtures.toy_game import build_toy_state

FACTORIES = (lambda s: RandomAgent(s), lambda s: RandomAgent(s + 1000))


def result(returns, turns=10, diff=0):
    return GameResult(seed=0, returns=returns, turns=turns, point_differential=diff)


def test_win_rate_counts_draws_as_half():
    batch = BatchResult(
        results=[result((1.0, 0.0)), result((0.0, 1.0)), result((0.5, 0.5))]
    )
    assert batch.win_rate(0) == pytest.approx(0.5)
    assert batch.win_rate(1) == pytest.approx(0.5)


def test_record_splits_wins_losses_draws():
    batch = BatchResult(
        results=[result((1.0, 0.0)), result((1.0, 0.0)), result((0.0, 1.0)), result((0.5, 0.5))]
    )
    assert batch.record(0) == (2, 1, 1)
    assert batch.record(1) == (1, 2, 1)


def test_mean_turns():
    batch = BatchResult(results=[result((1.0, 0.0), turns=4), result((0.0, 1.0), turns=10)])
    assert batch.mean_turns == pytest.approx(7.0)


def test_point_differential_distribution_is_sorted_histogram():
    batch = BatchResult(
        results=[result((1.0, 0.0), diff=d) for d in (3, -1, 3, 0, -1, 3)]
    )
    assert batch.point_differential_distribution() == {-1: 2, 0: 1, 3: 3}


def test_empty_batch_does_not_divide_by_zero():
    batch = BatchResult()
    assert batch.win_rate(0) == 0.0
    assert batch.mean_turns == 0.0
    assert batch.point_differential_distribution() == {}


def test_run_batch_produces_one_result_per_game_with_distinct_seeds():
    batch = run_batch(build_toy_state, FACTORIES, games=30, base_seed=7)
    assert batch.games == 30
    assert [r.seed for r in batch.results] == list(range(7, 37))
    assert all(r.turns > 0 for r in batch.results)


def test_run_batch_win_rate_is_a_probability():
    batch = run_batch(build_toy_state, FACTORIES, games=200, base_seed=0)
    assert 0.0 <= batch.win_rate(0) <= 1.0
    assert batch.win_rate(0) + batch.win_rate(1) == pytest.approx(1.0)


def test_summary_reports_every_milestone_1_output():
    batch = run_batch(build_toy_state, FACTORIES, games=50, base_seed=0)
    summary = batch.summary()
    for expected in ("games:", "win rate", "mean game length", "point differential"):
        assert expected in summary


def test_runaway_game_raises_rather_than_hanging():
    with pytest.raises(RuntimeError, match="without terminating"):
        run_batch(build_toy_state, FACTORIES, games=1, base_seed=0, max_steps=2)


@pytest.mark.perf
def test_harness_overhead_is_far_under_the_budget():
    """Milestone 1 budget is 1,000 games in 60s, single-threaded.

    The toy game is not Riftbound, so this measures harness overhead only --
    batch loop, agent, interface dispatch. It establishes how much of the
    60s budget is already spent before the real engine does any work.
    """
    batch = run_batch(build_toy_state, FACTORIES, games=1000, base_seed=0)
    assert batch.games == 1000
    assert batch.elapsed_seconds < 10.0, (
        f"harness alone took {batch.elapsed_seconds:.1f}s for 1,000 games; "
        f"that is already a sixth of the Milestone 1 budget."
    )


# --- the large-scale validation runner (BUILD.md section 10) ----------------


def test_validate_reports_a_clean_short_run():
    """A small run of the acceptance harness must come back clean and exit 0.

    The real run is 10,000 games and far too slow for a test; this pins that
    the harness itself works -- that it counts decisions, checks invariants,
    and returns the exit code the acceptance criterion depends on.
    """
    from analysis.validate import main

    assert main(["--games", "6", "--check-every", "2", "--deep",
                 "--progress", "0"]) == 0


def test_validate_fails_loudly_on_a_broken_engine(monkeypatch):
    """The counterweight: a harness that cannot fail proves nothing."""
    import analysis.validate as validate

    def broken(*args, **kwargs):
        raise RuntimeError("engine is broken")

    monkeypatch.setattr(validate, "build_state", broken)
    assert validate.main(["--games", "2", "--progress", "0"]) == 1
