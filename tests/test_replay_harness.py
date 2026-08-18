"""The replay harness -- the guard against silent rules drift.

Covers both halves: that a correct engine reproduces a recorded game, and
(more importantly) that a *changed* engine is caught. A replay runner that
never fails is worse than none, so most of these tests deliberately break
something and assert the harness notices.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.random_agent import RandomAgent
from engine.replay import (
    Replay,
    ReplayDivergence,
    record_game,
    run_replay,
    state_hash,
)
from cards.database import load as load_db
from engine.setup import build_state as build_riftbound, load_deck
from tests.fixtures.toy_game import ToyAction, build_toy_state


def build_riftbound_state(seed: int):
    """Builder for the checked-in Riftbound replays."""
    db = load_db()
    return build_riftbound(
        load_deck("jinx_chaos_fury"),
        load_deck("volibear_body_fury"),
        seed=seed,
        db=db,
        validate_decks=False,
    )

REPLAY_DIR = Path(__file__).parent / "replays"


def test_state_hash_is_stable_and_sensitive():
    a, b = build_toy_state(3), build_toy_state(3)
    assert state_hash(a) == state_hash(b)
    b.apply(b.legal_actions()[0])
    assert state_hash(a) != state_hash(b)


def test_state_hash_covers_both_players_hidden_information():
    """Hashing only the mover's view would let the opponent's hand drift."""
    a, b = build_toy_state(3), build_toy_state(3)
    b.hands[1] = [v for v in b.hands[1][:-1]] + [(b.hands[1][-1] % 9) + 1]
    assert state_hash(a) != state_hash(b)


def test_record_then_replay_round_trips():
    replay = record_game(
        build_toy_state, RandomAgent(21), seed=21, replay_id="rt", game="toy"
    )
    assert replay.steps
    run_replay(replay, build_toy_state)  # must not raise


def test_replay_serializes_round_trip(tmp_path):
    replay = record_game(
        build_toy_state, RandomAgent(8), seed=8, replay_id="ser", game="toy"
    )
    path = tmp_path / "r.json"
    replay.save(path)
    run_replay(Replay.load(path), build_toy_state)


def test_divergent_state_hash_is_caught():
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="bad", game="toy"
    )
    replay.steps[0].expected_state_hash = "deadbeefdeadbeef"
    with pytest.raises(ReplayDivergence, match="state hash mismatch"):
        run_replay(replay, build_toy_state)


def test_divergent_initial_state_is_caught():
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="setup", game="toy"
    )
    replay.initial_state_hash = "0000000000000000"
    with pytest.raises(ReplayDivergence, match="initial state hash mismatch"):
        run_replay(replay, build_toy_state)


def test_illegal_recorded_action_is_caught():
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="illegal", game="toy"
    )
    replay.steps[0].action = "play:999"
    with pytest.raises(ReplayDivergence, match="is not legal"):
        run_replay(replay, build_toy_state)


def test_wrong_player_to_move_is_caught():
    """Catches turn-order and priority regressions specifically."""
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="turn", game="toy"
    )
    replay.steps[0].player = 1 - replay.steps[0].player
    with pytest.raises(ReplayDivergence, match="to move"):
        run_replay(replay, build_toy_state)


def test_early_termination_is_caught():
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="early", game="toy"
    )
    replay.steps.append(replay.steps[-1])
    with pytest.raises(ReplayDivergence, match="ended early"):
        run_replay(replay, build_toy_state)


def test_final_returns_mismatch_is_caught():
    replay = record_game(
        build_toy_state, RandomAgent(5), seed=5, replay_id="ret", game="toy"
    )
    replay.final_returns = (0.25, 0.75)
    with pytest.raises(ReplayDivergence, match="final returns mismatch"):
        run_replay(replay, build_toy_state)


def test_replay_detects_a_real_rules_change():
    """The scenario the harness exists for: scoring silently changes."""
    replay = record_game(
        build_toy_state, RandomAgent(13), seed=13, replay_id="drift", game="toy"
    )

    class DriftedState(type(build_toy_state(0))):
        def apply(self, action: ToyAction) -> None:
            if action.kind == "play":
                action = ToyAction("play", action.value)
                self.scores[self.current_player] += 1  # a "harmless" tweak
            super().apply(action)

    with pytest.raises(ReplayDivergence):
        run_replay(replay, lambda seed: DriftedState(seed=seed))


# --- the checked-in synthetic replays --------------------------------------


def committed_replays() -> list[Path]:
    return sorted(REPLAY_DIR.glob("*.json"))


def test_synthetic_replays_are_present():
    assert len(committed_replays()) >= 2, "Milestone 1 wants 2-3 synthetic replays"


@pytest.mark.parametrize("path", committed_replays(), ids=lambda p: p.stem)
def test_committed_replay_reproduces(path):
    replay = Replay.load(path)
    builders = {"toy": build_toy_state, "riftbound": build_riftbound_state}
    if replay.game not in builders:
        pytest.skip(f"no state builder registered for game {replay.game!r} yet")
    run_replay(replay, builders[replay.game])


@pytest.mark.parametrize("path", committed_replays(), ids=lambda p: p.stem)
def test_committed_replay_is_well_formed(path):
    raw = json.loads(path.read_text())
    for key in ("replay_id", "seed", "steps", "game", "source"):
        assert key in raw, f"{path.name} missing {key!r}"
    for i, step in enumerate(raw["steps"]):
        assert step["player"] in (0, 1), f"{path.name} step {i}: bad player"
        assert step["expected_state_hash"], f"{path.name} step {i}: empty hash"


def test_the_starter_decks_are_fixtures_not_outputs():
    """A recorded replay is only meaningful against the exact decklist it was
    played with, so rebuilding a deck silently invalidates every game recorded
    with it. This caught a real break: improving Equipment changed which cards
    counted as implemented, which changed the deck builder's ordering, which
    changed the starter decks under the committed replays.

    `build_decks.py` therefore refuses to overwrite an existing deck without
    `--force`.
    """
    import json

    from engine.setup import load_deck

    for slug, champion in (
        ("jinx_chaos_fury", "OGN-197"),
        ("volibear_body_fury", "OGN-036"),
    ):
        deck = load_deck(slug)
        assert len(deck.main) == 40, f"{slug} main deck changed size"
        assert deck.champion == champion, (
            f"{slug}'s champion changed to {deck.champion}; the committed "
            f"replays were recorded against {champion}"
        )
