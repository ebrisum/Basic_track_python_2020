"""`TCG_AI_BUILD.md` section 29 -- a run's numbers must outlive its terminal.

A validation run that prints and exits leaves nothing to compare the next run
against. `--metrics PATH` writes the numbers *and* the provenance stamp into
one file, together on purpose: separating them is exactly how a number
outlives the engine that produced it and gets quoted about a different game.
"""

from __future__ import annotations

import json

from analysis.validate import main


def test_a_run_can_write_its_numbers_and_its_stamp(tmp_path):
    path = tmp_path / "nested" / "metrics.json"
    code = main([
        "--games", "3", "--check-every", "1", "--deep",
        "--progress", "0", "--metrics", str(path),
    ])
    assert code == 0
    record = json.loads(path.read_text())

    assert record["metrics"]["games"] == 3
    assert record["metrics"]["decisions"] > 0
    assert record["failures"] == {
        "crashes": 0, "illegal": 0, "impossible": 0,
        "unresolved": 0, "first_problem": [],
    }
    # The stamp is what makes the numbers comparable at all.
    for key in ("engine_version", "rules_version", "card_pool_version",
                "observation_schema_version", "action_schema_version"):
        assert record["provenance"][key]
    # And the settings, because 3 games checked shallowly and 3 games checked
    # after every action are different claims wearing the same numbers.
    assert record["settings"]["deep"] is True
    assert record["settings"]["check_every"] == 1


def test_no_metrics_flag_writes_nothing(tmp_path):
    """The default stays exactly as it was: print and exit."""
    assert main(["--games", "2", "--progress", "0"]) == 0
    assert not list(tmp_path.iterdir())


def test_workers_produce_identical_results(tmp_path):
    """`TCG_AI_BUILD.md` section 13 -- parallelism must not change the answer.

    It is safe here for one reason: a game's outcome depends only on its seed,
    so the games are independent and every counter is a sum, which does not
    care what order it is summed in. That is an argument, and this is the
    check -- the same games through one process and four must agree on every
    number, not merely on the pass/fail verdict.
    """
    serial = tmp_path / "serial.json"
    parallel = tmp_path / "parallel.json"
    common = ["--games", "8", "--check-every", "1", "--deep", "--progress", "0"]

    assert main(common + ["--metrics", str(serial)]) == 0
    assert main(common + ["--workers", "4", "--metrics", str(parallel)]) == 0

    one = json.loads(serial.read_text())
    many = json.loads(parallel.read_text())
    for key in ("games", "decisions", "mean_branching_factor",
                "mean_turns_per_game", "states_checked_games"):
        assert one["metrics"][key] == many["metrics"][key], key
    assert one["failures"] == many["failures"]
    assert one["provenance"] == many["provenance"]
