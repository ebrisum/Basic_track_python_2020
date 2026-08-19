"""`TCG_AI_BUILD.md` section 30 -- configuration files.

The plan asks for YAML. These are TOML, read with the standard library, and
the substitution is deliberate: YAML needs PyYAML, the standing rule is stdlib
plus pytest with any dependency asked for first, and a configuration format is
a poor thing to spend the first dependency on.

The tests that matter are about what a config file is *not* allowed to do. A
config system is a quiet way to change behaviour, and two keys here could undo
decisions the rest of the project argues for at length.
"""

from __future__ import annotations

import pytest

from learning.config import CONFIG_DIR, DEFAULTS, ConfigError, load, validate


def test_every_shipped_config_loads():
    names = sorted(p.stem for p in CONFIG_DIR.glob("*.toml"))
    assert set(names) >= {"default", "debug", "eval"}
    for name in names:
        assert load(name)["run"]["games"] > 0


def test_a_config_only_names_what_it_changes():
    """Defaults fill in the rest, so a file is a diff and not a duplicate."""
    debug = load("debug")
    assert debug["run"]["games"] == 5                      # from the file
    assert debug["league"]["sampling"] == "uniform"        # from the defaults
    assert debug["reward"]["shaping"] is False


def test_reward_shaping_cannot_be_switched_on():
    """Section 15, defended in code rather than in a comment.

    `analysis/evaluation.py` spends a page explaining that the heuristic is a
    prior over winning and not a payout. A config key able to turn points into
    reward would undo that in one line, from a file nobody diffs.
    """
    with pytest.raises(ConfigError) as caught:
        validate({"reward": {"shaping": True}})
    assert "section 15" in str(caught.value).lower()


def test_an_unimplemented_curriculum_level_is_refused():
    """Section 26 has no content restrictions yet.

    A config naming level 3 must fail rather than run the full game and let
    the reader believe they measured a restricted one.
    """
    with pytest.raises(ConfigError):
        validate({"curriculum": {"level": 3}})


def test_an_unknown_config_name_lists_what_exists():
    with pytest.raises(ConfigError) as caught:
        load("nonesuch")
    assert "default" in str(caught.value)


def test_the_defaults_and_the_default_file_agree():
    """`config/default.toml` documents the built-ins, so it must match them.

    If it drifts, the file becomes a lie that reads like documentation.
    """
    on_disk = load("default")
    for section, values in DEFAULTS.items():
        for key, value in values.items():
            assert on_disk[section][key] == value, f"{section}.{key} drifted"
