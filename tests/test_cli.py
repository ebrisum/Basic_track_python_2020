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


def test_train_no_longer_refuses(capsys):
    """It used to exit 2 saying the dependency decision was open. It is not.

    The previous version of this test called `cli.main(["train"])` with no
    arguments and asserted a refusal. Once `train` was wired up that call
    started a real five-iteration training run *inside the test suite* — it
    did not fail, it hung, and it took ten minutes to notice. A test that
    encodes "this does nothing" has to be deleted the moment the thing starts
    doing something; there is no version of it that stays harmless.

    So this asserts the parser works without running anything.
    """
    with pytest.raises(SystemExit) as caught:
        cli.main(["train", "--help"])
    assert caught.value.code == 0
    assert "--iterations" in capsys.readouterr().out


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


def test_generate_refuses_to_clobber_an_existing_dataset(tmp_path, capsys):
    """A dataset is hours of compute; overwriting one silently is not on.

    `decks/build_decks.py` learned this the hard way -- a regenerate replaced
    its own inputs and changed the starter decks under the committed replays.
    """
    out = tmp_path / "ds.jsonl.gz"
    assert cli.main(["generate", "--games", "1", "--out", str(out),
                     "--agents", "random", "random", "--progress", "0"]) == 0
    capsys.readouterr()
    before = out.stat().st_size

    assert cli.main(["generate", "--games", "1", "--out", str(out),
                     "--agents", "random", "random", "--progress", "0"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert out.stat().st_size == before

    assert cli.main(["generate", "--games", "1", "--out", str(out), "--force",
                     "--agents", "random", "random", "--progress", "0"]) == 0


def test_train_is_wired_and_produces_a_checkpoint(tmp_path, capsys):
    """`train` used to exit 2 saying the decision was open. It is not now.

    Kept deliberately tiny: this asserts the command runs a real iteration and
    leaves a loadable checkpoint behind, not that it learns anything. Learning
    is `test_ppo.py`'s job.
    """
    pytest.importorskip("torch")
    out = tmp_path / "ck"
    assert cli.main([
        "train", "--config", "debug", "--iterations", "1", "--games", "1",
        "--eval-games", "2", "--out", str(out),
    ]) == 0
    printed = capsys.readouterr().out
    assert '"iteration": 1' in printed

    saved = sorted(out.glob("gen_*.pt"))
    assert saved, "no checkpoint was written"
    assert (out / "history.json").exists()

    from cards.database import load as load_pool
    from learning.action_encoding import ActionEncoder
    from learning.state_encoding import StateEncoder
    from model.checkpoint import load as load_checkpoint

    db = load_pool()
    network, payload = load_checkpoint(saved[0], StateEncoder(db), ActionEncoder())
    assert network is not None
    assert payload["extra"]["iteration"] == 1
    assert payload["provenance"]["engine_version"]


def test_train_says_what_is_missing_rather_than_failing_on_an_import():
    """The one command that needs a dependency should name it."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = refuse
    try:
        import io as _io
        import contextlib

        stderr = _io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["train", "--iterations", "1"])
        assert code == 2
        assert "torch is not installed" in stderr.getvalue()
        assert "standard library" in stderr.getvalue()
    finally:
        builtins.__import__ = real_import
