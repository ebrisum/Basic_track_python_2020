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
