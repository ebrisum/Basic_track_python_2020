"""`TCG_AI_BUILD.md` section 31 -- one entry point for the project.

The tests here are about the door, not the rooms behind it: that every
advertised command dispatches, that an unknown one fails loudly rather than
silently doing nothing, and -- the one that matters -- that `train` refuses
instead of pretending.

A stub that prints "training started" and exits 0 is worse than no command at
all, because the next person believes it.
"""

from __future__ import annotations

import json

import pytest

import cli


def test_help_lists_every_command():
    assert set(cli.HANDLERS) >= {
        "validate", "simulate", "benchmark", "evaluate", "generate",
        "fit", "selfplay", "play", "train",
    }
    for name in cli.HANDLERS:
        assert name in cli.COMMANDS, f"{name} is dispatchable but undocumented"


def test_no_arguments_prints_help_and_succeeds(capsys):
    assert cli.main([]) == 0
    assert "commands:" in capsys.readouterr().out


def test_an_unknown_command_fails(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_train_refuses_rather_than_pretending(capsys):
    """The command exists in the plan and not in this repository."""
    assert cli.main(["train"]) == 2
    err = capsys.readouterr().err
    assert "not implemented" in err
    # And it says *why*, so the reader knows it is a decision, not an oversight.
    assert "dependency" in err


def test_evaluate_grades_an_agent(capsys):
    assert cli.main(["evaluate", "--agent", "random"]) == 0
    out = capsys.readouterr().out
    assert "random" in out


def test_generate_writes_a_readable_dataset(tmp_path, capsys):
    out = tmp_path / "ds.jsonl.gz"
    assert cli.main([
        "generate", "--games", "2", "--out", str(out),
        "--agents", "random", "random", "--progress", "0",
    ]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["games"] == 2
    assert summary["bytes"] > 0
    assert summary["p0_wins"] + summary["p1_wins"] + summary["draws"] == 2

    from learning.trajectory import read_trajectory
    records = list(read_trajectory(out))
    assert records[0]["record"] == "header"
    kinds = {r["record"] for r in records}
    assert {"header", "generator", "transition", "result"} <= kinds
    # Two games in one file: one header, two results.
    assert sum(1 for r in records if r["record"] == "result") == 2
    assert sum(1 for r in records if r["record"] == "header") == 1


def test_generate_rejects_an_unknown_agent(capsys):
    assert cli.main(["generate", "--agents", "random", "wizard"]) == 2
    assert "unknown agent" in capsys.readouterr().err


def test_the_generator_record_names_who_played(tmp_path, capsys):
    """Data from different pairings is a different distribution."""
    out = tmp_path / "ds.jsonl.gz"
    cli.main(["generate", "--games", "1", "--out", str(out),
              "--agents", "greedy", "random", "--progress", "0"])
    capsys.readouterr()
    from learning.trajectory import read_trajectory
    record = [r for r in read_trajectory(out) if r["record"] == "generator"][0]
    assert record["agents"] == ["GreedyAgent", "RandomAgent"]
    assert record["seats_alternate"] is True
