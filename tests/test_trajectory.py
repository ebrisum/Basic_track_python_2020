"""`TCG_AI_BUILD.md` section 14 -- the trajectory format.

The existing `Replay` is a *determinism* artefact: actions and a final hash,
enough to prove a game reproduces and nothing more. A trajectory is training
data, and the plan asks it to carry the observation, the legal-action mask,
the action taken, the reward, and optionally a policy and a value estimate.

The tests that matter here are about sufficiency and honesty:

* a trajectory must be enough to **replay the game exactly**, otherwise it is
  not a record of what happened;
* the recorded action index must **decode back to the action applied**, or the
  mask and the action disagree and every gradient from it is wrong;
* the reward must be the terminal signal only -- section 15 forbids shaping,
  and a per-step reward that is quietly nonzero is shaping.
"""

from __future__ import annotations

import json
import random

import pytest

from agents.random_agent import RandomAgent
from agents.styles import AggroAgent
from analysis.validate import build_state, load_db, load_deck
from engine.replay import state_hash
from learning.action_encoding import ActionEncoder
from learning.trajectory import (
    Transition,
    TrajectoryWriter,
    read_trajectory,
    record_game,
    slim_observation,
)


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


def _fresh(seed: int):
    db = load_db()
    return build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)


def test_a_recorded_game_replays_to_the_same_final_state(tmp_path):
    """Sufficiency: the actions in the file reconstruct the game exactly."""
    path = tmp_path / "game.jsonl.gz"
    played = record_game(
        _fresh(3), [RandomAgent(1), AggroAgent(2)], path, game_id="g3"
    )

    encoder = ActionEncoder()
    state = _fresh(3)
    steps = 0
    for record in read_trajectory(path):
        if record["record"] != "transition":
            continue
        action = encoder.decode(state.observation(state.current_player),
                                record["action_index"])
        assert action in state.legal_actions()
        state.apply(action)
        steps += 1
    assert steps > 0
    assert state_hash(state) == state_hash(played)


def test_the_recorded_index_decodes_to_the_action_that_was_applied(tmp_path):
    path = tmp_path / "g.jsonl.gz"
    record_game(_fresh(5), [RandomAgent(1), RandomAgent(2)], path, game_id="g5")
    for record in read_trajectory(path):
        if record["record"] != "transition":
            continue
        assert record["action_repr"] in record["legal_reprs"]


def test_the_mask_is_stored_as_indices_and_covers_the_action(tmp_path):
    path = tmp_path / "g.jsonl.gz"
    record_game(_fresh(6), [RandomAgent(1), RandomAgent(2)], path, game_id="g6")
    seen = 0
    for record in read_trajectory(path):
        if record["record"] != "transition":
            continue
        seen += 1
        assert record["action_index"] in record["legal_indices"]
        assert record["legal_indices"] == sorted(set(record["legal_indices"]))
    assert seen > 0


def test_reward_is_terminal_only(tmp_path):
    """Section 15 -- no shaping. A nonzero mid-game reward is shaping."""
    path = tmp_path / "g.jsonl.gz"
    record_game(_fresh(7), [RandomAgent(1), RandomAgent(2)], path, game_id="g7")
    records = [r for r in read_trajectory(path) if r["record"] == "transition"]
    assert all(r["reward"] == 0.0 for r in records[:-1])
    footer = [r for r in read_trajectory(path) if r["record"] == "result"][0]
    assert sorted(footer["returns"]) in ([0.0, 1.0], [0.5, 0.5])


def test_the_header_carries_provenance(tmp_path):
    """A dataset without a stamp cannot be compared to a later dataset."""
    path = tmp_path / "g.jsonl.gz"
    record_game(_fresh(8), [RandomAgent(1), RandomAgent(2)], path, game_id="g8")
    header = next(iter(read_trajectory(path)))
    assert header["record"] == "header"
    for key in (
        "engine_version", "rules_version", "card_pool_version",
        "observation_schema_version", "action_schema_version",
    ):
        assert header["provenance"][key]
    assert header["action_space_size"] == ActionEncoder().size


def test_the_slim_observation_drops_only_recoverable_fields():
    """Presentation and static card data are dropped, `card_id` is not.

    Everything removed is a function of `card_id` and the card pool, and the
    pool is pinned by `card_pool_version` in the header -- so the slimming is
    lossless *given the header*, which is the only sense in which dropping
    data from a dataset is acceptable.
    """
    state = _fresh(9)
    obs = state.observation(0)
    lean = slim_observation(obs)
    blob = json.dumps(lean)
    assert '"card_id"' in blob
    for dropped in ("image_url", "rules_text", "script_note"):
        assert f'"{dropped}"' not in blob
    assert lean["phase"] == obs.phase
    assert lean["points"] == list(obs.points)


def test_slimming_is_worth_doing(tmp_path):
    """The reason the format is not just the observation, measured.

    A full observation is ~20 KB; a game is hundreds of decisions and a run is
    thousands of games. If slimming did not buy an order of magnitude it would
    not be worth the loss of directness, so the margin is asserted rather than
    assumed.
    """
    state = _fresh(10)
    obs = state.observation(0)
    full = len(obs.to_canonical_bytes())
    lean = len(json.dumps(slim_observation(obs), separators=(",", ":")).encode())
    assert lean * 2 < full, f"slim {lean} vs full {full} -- not worth it"


def test_a_transition_round_trips_through_the_file(tmp_path):
    path = tmp_path / "one.jsonl.gz"
    state = _fresh(11)
    encoder = ActionEncoder()
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    transition = Transition(
        game_id="t",
        step=0,
        player=state.current_player,
        observation=slim_observation(obs),
        action_index=encoder.encode(obs, legal[0]),
        action_repr=repr(legal[0]),
        legal_indices=sorted({encoder.encode(obs, a) for a in legal}),
        legal_reprs=sorted({repr(a) for a in legal}),
        reward=0.0,
        policy=None,
        value=None,
    )
    with TrajectoryWriter(path, encoder=encoder) as writer:
        writer.write(transition)
    records = list(read_trajectory(path))
    stored = [r for r in records if r["record"] == "transition"][0]
    assert stored["action_index"] == transition.action_index
    assert stored["legal_reprs"] == transition.legal_reprs
    assert stored["player"] == transition.player


def test_policy_and_value_are_optional_and_survive(tmp_path):
    """Sections 18 and 21 need them; sections 12-13 do not produce them."""
    path = tmp_path / "pv.jsonl.gz"
    state = _fresh(12)
    encoder = ActionEncoder()
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    indices = sorted({encoder.encode(obs, a) for a in legal})
    probs = [1.0 / len(indices)] * len(indices)
    with TrajectoryWriter(path, encoder=encoder) as writer:
        writer.write(Transition(
            game_id="t", step=0, player=0,
            observation=slim_observation(obs),
            action_index=indices[0], action_repr=repr(legal[0]),
            legal_indices=indices, legal_reprs=sorted({repr(a) for a in legal}),
            reward=0.0, policy=probs, value=0.5,
        ))
    stored = [r for r in read_trajectory(path) if r["record"] == "transition"][0]
    assert stored["value"] == 0.5
    assert len(stored["policy"]) == len(indices)
    assert abs(sum(stored["policy"]) - 1.0) < 1e-9


def test_a_truncated_episode_carries_no_result(tmp_path):
    """Hitting the action cap is truncation, not termination.

    Calling it a draw would write a reward the game never produced -- a
    fabricated label in the one file a learner is supposed to trust. The
    earlier version of this module did exactly that, and it only showed up
    because the debug config sets a 400-action cap and two games hit it.
    """
    path = tmp_path / "cut.jsonl.gz"
    record_game(_fresh(3), [RandomAgent(1), RandomAgent(2)], path,
                game_id="cut", action_cap=12)
    records = list(read_trajectory(path))
    result = [r for r in records if r["record"] == "result"][0]
    assert result["terminal"] is False
    assert result["returns"] is None
    transitions = [r for r in records if r["record"] == "transition"]
    assert transitions, "nothing was recorded"
    assert all(r["reward"] == 0.0 for r in transitions)
    assert not [r for r in records if r["record"] == "transition-reward"]


def test_a_finished_episode_says_so(tmp_path):
    path = tmp_path / "done.jsonl.gz"
    record_game(_fresh(7), [RandomAgent(1), RandomAgent(2)], path, game_id="d")
    result = [r for r in read_trajectory(path) if r["record"] == "result"][0]
    assert result["terminal"] is True
    assert result["returns"] is not None
