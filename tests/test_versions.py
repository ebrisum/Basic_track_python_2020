"""Provenance stamps (BUILD.md section 37).

"Training data generated under incompatible rule versions must not silently
mix." The word doing the work is *silently*. A version stamp that has to be
remembered will be forgotten, so the schema versions here are **derived** from
the dataclasses they describe: change a field, and the version moves whether
anyone thought about it or not.

This session moved the replay hashes twice under justified changes, and both
times diagnosing "representation change" versus "regression" was hand work.
That is the job these stamps do.
"""

from __future__ import annotations

import pathlib

import pytest

from cards.database import load as load_db
from engine import versions

DB = load_db()


# --- the stamp itself -------------------------------------------------------


def test_provenance_carries_every_field_the_plan_names():
    stamp = versions.provenance(DB)
    for key in ("engine_version", "rules_version", "card_pool_version",
                "observation_schema_version", "action_schema_version"):
        assert key in stamp, key
        assert stamp[key], f"{key} is empty"


def test_the_stamp_is_deterministic():
    """Two calls, and two loads of the same database, must agree -- otherwise
    every replay written is stamped differently from every replay read."""
    assert versions.provenance(DB) == versions.provenance(DB)
    assert versions.provenance(load_db()) == versions.provenance(DB)


def test_the_rules_version_names_the_cached_rules_text():
    assert versions.RULES_VERSION.startswith("CR-v1.4")


# --- derived, not remembered ------------------------------------------------


def test_the_card_pool_version_moves_when_a_card_changes():
    """A changed cost is exactly the case where mixing datasets is unsafe."""
    import dataclasses

    before = versions.card_pool_version(DB)
    tampered = load_db()
    card = tampered.cards["OGN-001"]
    tampered.cards["OGN-001"] = dataclasses.replace(card, energy=card.energy + 1)

    assert versions.card_pool_version(tampered) != before


def test_the_card_pool_version_ignores_presentation():
    """Image URLs are not gameplay. A dataset is not incompatible because a
    picture moved."""
    import dataclasses

    before = versions.card_pool_version(DB)
    tampered = load_db()
    card = tampered.cards["OGN-001"]
    tampered.cards["OGN-001"] = dataclasses.replace(card, image_url="elsewhere")

    assert versions.card_pool_version(tampered) == before


def test_the_observation_schema_version_moves_with_the_observation():
    """The regression this exists for: a new observation field changed every
    replay hash, and nothing said so."""
    from engine.observation import RiftboundObservation

    before = versions.observation_schema_version()
    original = RiftboundObservation.__dataclass_fields__
    try:
        RiftboundObservation.__dataclass_fields__ = dict(original)
        RiftboundObservation.__dataclass_fields__["a_new_field"] = None
        assert versions.observation_schema_version() != before
    finally:
        RiftboundObservation.__dataclass_fields__ = original


def test_the_action_schema_version_moves_when_an_action_changes():
    from engine import actions

    before = versions.action_schema_version()
    original = actions.PlayCard.__dataclass_fields__
    try:
        actions.PlayCard.__dataclass_fields__ = dict(original)
        actions.PlayCard.__dataclass_fields__["another_field"] = None
        assert versions.action_schema_version() != before
    finally:
        actions.PlayCard.__dataclass_fields__ = original


# --- replays carry it -------------------------------------------------------


def test_a_recorded_replay_is_stamped(tmp_path):
    from agents.random_agent import RandomAgent
    from engine.replay import Replay, record_game
    from engine.setup import build_state, load_deck

    decks = load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")

    def build(seed):
        return build_state(decks[0], decks[1], seed=seed, db=DB)

    replay = record_game(build, RandomAgent(2).act, seed=2,
                         replay_id="stamped",
                         provenance=versions.provenance(DB))

    assert replay.provenance["card_pool_version"]

    path = tmp_path / "r.json"
    replay.save(path)
    assert Replay.load(path).provenance == replay.provenance


def test_an_unstamped_replay_still_runs(tmp_path):
    """The committed toy replays predate stamping. An absent stamp means
    "nothing to compare", not "mismatch"."""
    from engine.replay import Replay, run_replay
    from tests.fixtures.toy_game import build_toy_state

    path = (pathlib.Path(__file__).resolve().parent
            / "replays" / "synthetic-toy-001.json")
    replay = Replay.load(path)
    assert replay.provenance == {}
    run_replay(replay, build_toy_state, provenance=versions.provenance(DB))


def test_the_committed_replays_are_stamped():
    """Not just supported -- actually applied to the fixtures that matter."""
    from pathlib import Path

    from engine.replay import Replay

    here = Path(__file__).resolve().parent / "replays"
    paths = sorted(here.glob("riftbound-*.json"))
    assert paths
    for path in paths:
        replay = Replay.load(path)
        assert replay.provenance, f"{path.name} carries no provenance"


def test_a_divergence_says_which_component_changed():
    """The payoff. A bare hash mismatch made me diagnose by hand twice; the
    error should name the suspect itself."""
    from engine.replay import describe_provenance_drift

    old = {"engine_version": "1", "rules_version": "CR-v1.4",
           "card_pool_version": "aaaa", "observation_schema_version": "bbbb",
           "action_schema_version": "cccc"}
    new = dict(old, observation_schema_version="dddd")

    message = describe_provenance_drift(old, new)
    assert "observation_schema_version" in message
    assert "card_pool_version" not in message


def test_no_drift_reads_as_no_drift():
    from engine.replay import describe_provenance_drift

    stamp = versions.provenance(DB)
    assert describe_provenance_drift(stamp, dict(stamp)) == ""
    assert describe_provenance_drift(None, stamp) == ""


@pytest.mark.parametrize("missing", ["card_pool_version", "rules_version"])
def test_a_partial_stamp_still_reports(missing):
    """An older replay stamped with fewer keys must not crash the reporter."""
    from engine.replay import describe_provenance_drift

    new = versions.provenance(DB)
    old = {k: v for k, v in new.items() if k != missing}
    assert describe_provenance_drift(old, new) != ""
