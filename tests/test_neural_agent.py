"""The agent and its checkpoints (sections 18, 21, 22).

A checkpoint is weights plus the shape of the world they were trained in.
Loading a policy head against a different token layout does not crash: the
tensors still multiply, the outputs still look like logits, and every column
means something else. `test_a_checkpoint_refuses_a_changed_world` is the one
that matters here.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from agents.random_agent import RandomAgent
from analysis.validate import load_db, load_deck
from engine.setup import build_state
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.agent import NeuralAgent
from model.checkpoint import CheckpointMismatch, load, save
from model.network import ModelConfig, RiftboundNet


@pytest.fixture(scope="module")
def db():
    return load_db()


@pytest.fixture(scope="module")
def encoders(db):
    return StateEncoder(db), ActionEncoder()


@pytest.fixture(scope="module")
def network(encoders):
    torch.manual_seed(0)
    return RiftboundNet(ModelConfig.from_config(
        {"model": {"embedding_dim": 32, "transformer_layers": 1,
                   "attention_heads": 2, "ff_dim": 64}},
        encoders[0],
    ))


def _state(db, seed=1, steps=30):
    import random
    state = build_state(load_deck("jinx_chaos_fury"),
                        load_deck("volibear_body_fury"), seed=seed, db=db)
    rng = random.Random(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        state.apply(rng.choice(legal))
    return state


def test_the_agent_only_ever_returns_a_legal_action(db, encoders, network):
    """Section 29. The mask makes it structural; this checks the wiring too."""
    state = _state(db)
    agent = NeuralAgent(network, *encoders, seed=0)
    for _ in range(60):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        action = agent.act(state)
        assert action in legal
        state.apply(action)


def test_it_records_the_distribution_not_just_the_pick(db, encoders, network):
    """Section 21 wants the action probability, which a one-hot is not."""
    state = _state(db, seed=4)
    agent = NeuralAgent(network, *encoders, seed=2)
    legal = state.legal_actions()
    agent.act(state)
    assert agent.last_policy is not None
    assert len(agent.last_policy) == len(legal)
    assert sum(agent.last_policy) == pytest.approx(1.0, abs=1e-5)
    assert -1.0 <= agent.last_value <= 1.0


def test_greedy_play_is_deterministic(db, encoders, network):
    """Section 22 asks for deterministic evaluation.

    A rating has to be a property of the weights, not of a random draw.
    """
    state = _state(db, seed=6)
    first = NeuralAgent(network, *encoders, seed=0, greedy=True).act(state)
    second = NeuralAgent(network, *encoders, seed=999, greedy=True).act(state)
    assert first == second


def test_sampling_uses_the_agents_own_seed(db, encoders, network):
    """Every agent here is reproducible from its seed alone.

    A policy drawing from torch's global generator would make a self-play game
    depend on whatever else had drawn from it — including, in a test run, the
    order pytest happened to execute in.
    """
    state = _state(db, seed=8, steps=45)
    torch.manual_seed(1234)
    first = NeuralAgent(network, *encoders, seed=17).act(state)
    torch.manual_seed(4321)
    second = NeuralAgent(network, *encoders, seed=17).act(state)
    assert first == second


def test_it_is_interchangeable_with_the_baseline_agents(db, encoders, network):
    """It plays a whole game against RandomAgent through the frozen interface."""
    state = _state(db, seed=3, steps=0)
    agents = [NeuralAgent(network, *encoders, seed=1), RandomAgent(2)]
    steps = 0
    while not state.is_terminal() and steps < 600:
        legal = state.legal_actions()
        if not legal:
            break
        state.apply(agents[state.current_player].act(state))
        steps += 1
    assert steps > 0


def test_a_checkpoint_round_trips(tmp_path, db, encoders, network):
    path = save(tmp_path / "gen1.pt", network, encoders[0], encoders[1], db=db)
    restored, payload = load(path, encoders[0], encoders[1])
    for a, b in zip(network.parameters(), restored.parameters()):
        assert torch.equal(a, b)
    assert payload["provenance"]["engine_version"]
    assert payload["state_schema"] == encoders[0].schema_version()


def test_a_checkpoint_refuses_a_changed_world(tmp_path, db, encoders, network):
    """The failure this prevents is silent, which is why it must be loud.

    A policy head loaded against a different token layout runs perfectly and
    means nothing: the columns have moved underneath it.
    """
    path = save(tmp_path / "gen1.pt", network, encoders[0], encoders[1], db=db)
    # Captured *before* the patch. `schema_version()` reads the module's
    # column lists live, so comparing two post-patch calls compares the new
    # world to itself -- which is how the first version of this test passed
    # its own assertion and proved nothing.
    as_saved = encoders[0].schema_version()

    import learning.state_encoding as encoding
    original = encoding.SCALARS
    try:
        encoding.SCALARS = original + ("a_new_column",)
        moved = StateEncoder(db)
        assert moved.schema_version() != as_saved
        with pytest.raises(CheckpointMismatch) as caught:
            load(path, moved, encoders[1])
        assert "state encoding changed" in str(caught.value)
        # And it can still be forced open, for someone who knows why.
        network_again, _payload = load(path, moved, encoders[1], strict=False)
        assert network_again is not None
    finally:
        encoding.SCALARS = original


def test_a_checkpoint_refuses_a_changed_action_space(tmp_path, db, encoders, network):
    path = save(tmp_path / "gen1.pt", network, encoders[0], encoders[1], db=db)
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["action_segments"] = ["Something", "Else"]
    torch.save(payload, path)
    with pytest.raises(CheckpointMismatch) as caught:
        load(path, encoders[0], encoders[1])
    assert "action space" in str(caught.value)
