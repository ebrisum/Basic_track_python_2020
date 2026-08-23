"""`TCG_AI_BUILD.md` sections 17 and 18 -- the model.

Two tests here are worth more than the rest.

`test_padding_cannot_change_the_answer` encodes the same position twice with
different amounts of padding and asserts the outputs are identical. Padding
bugs are the classic silent failure in a masked transformer: the model trains,
the loss falls, and the value quietly depends on how many empty slots a
position happened to need.

`test_no_probability_mass_can_land_on_an_illegal_action` is section 29's
"the agent never attempts an illegal action" enforced in the architecture
rather than checked after the fact.
"""

from __future__ import annotations

import random

import pytest

torch = pytest.importorskip("torch")

from analysis.validate import build_state, load_db, load_deck
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.batching import action_batch, state_batch
from model.network import ModelConfig, RiftboundNet


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


@pytest.fixture(scope="module")
def db():
    return load_db()


@pytest.fixture(scope="module")
def encoders(db):
    return StateEncoder(db), ActionEncoder()


@pytest.fixture(scope="module")
def small(db, encoders):
    """A deliberately tiny model: these tests are about wiring, not capacity."""
    state_encoder, _ = encoders
    torch.manual_seed(0)
    config = ModelConfig.from_config(
        {"model": {"embedding_dim": 32, "transformer_layers": 2,
                   "attention_heads": 2, "ff_dim": 64}},
        state_encoder,
    )
    return RiftboundNet(config).eval()


def _position(db, seed: int = 1, steps: int = 40):
    state = build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)
    rng = random.Random(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        state.apply(rng.choice(legal))
    return state


def _batch(state, encoders, state_encoder=None):
    senc, aenc = encoders
    senc = state_encoder or senc
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    encoded = senc.encode(obs)
    return (state_batch([encoded]),
            action_batch([encoded], [legal], [obs], aenc),
            legal)


def test_a_forward_pass_gives_one_logit_per_legal_action(db, encoders, small):
    state = _position(db)
    batch, actions, legal = _batch(state, encoders)
    with torch.no_grad():
        logits, value = small(batch, actions)
    assert logits.shape == (1, len(legal))
    assert torch.isfinite(logits).all()
    assert value.shape == (1,)


def test_the_value_head_stays_in_the_interval_section_17_specifies(db, encoders, small):
    for seed in range(4):
        state = _position(db, seed=seed, steps=30 + seed * 20)
        batch, actions, _ = _batch(state, encoders)
        with torch.no_grad():
            _logits, value = small(batch, actions)
        assert -1.0 <= float(value[0]) <= 1.0


def test_padding_cannot_change_the_answer(db, encoders, small):
    """The same position with more empty slots must encode to the same thing.

    A padding bug here is the classic silent failure: everything trains, the
    loss falls, and the value depends on how much padding a position needed.
    """
    _senc, aenc = encoders
    state = _position(db, seed=3, steps=60)
    obs = state.observation(state.current_player)
    legal = state.legal_actions()

    outputs = []
    for max_tokens in (192, 320):
        encoder = StateEncoder(db, max_tokens=max_tokens)
        encoded = encoder.encode(obs)
        batch = state_batch([encoded])
        actions = action_batch([encoded], [legal], [obs], aenc)
        with torch.no_grad():
            outputs.append(small(batch, actions))

    (first_logits, first_value), (second_logits, second_value) = outputs
    assert torch.allclose(first_value, second_value, atol=1e-5), (
        "the value moved when only the amount of padding changed"
    )
    assert torch.allclose(first_logits, second_logits, atol=1e-5)


def test_no_probability_mass_can_land_on_an_illegal_action(db, encoders, small):
    """Section 29, enforced by the architecture rather than checked after."""
    senc, aenc = encoders
    states, legals, observations = [], [], []
    for seed in (1, 2, 3):
        state = _position(db, seed=seed, steps=20 + seed * 15)
        obs = state.observation(state.current_player)
        observations.append(obs)
        legals.append(state.legal_actions())
        states.append(senc.encode(obs))

    batch = state_batch(states)
    actions = action_batch(states, legals, observations, aenc)
    with torch.no_grad():
        logits, _value = small(batch, actions)

    probabilities = torch.softmax(logits, dim=-1)
    for row, legal in enumerate(legals):
        assert torch.allclose(probabilities[row].sum(), torch.tensor(1.0), atol=1e-5)
        # Every padded column is exactly zero, not merely small.
        assert float(probabilities[row, len(legal):].sum()) == 0.0


def test_an_action_points_at_the_object_it_acts_on(db, encoders):
    """The policy head reads H[row], so the row had better be the right card."""
    senc, aenc = encoders
    state = _position(db, seed=5, steps=50)
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    encoded = senc.encode(obs)
    actions = action_batch([encoded], [legal], [obs], aenc)

    checked = 0
    for column, action in enumerate(legal):
        instance_id = getattr(action, "instance_id", None)
        if instance_id is None:
            assert actions["row"][0, column] == -1
            continue
        row = int(actions["row"][0, column])
        assert row >= 0, f"{action!r} names {instance_id} but points nowhere"
        assert encoded.instances[row] == instance_id
        checked += 1
    assert checked, "no action in this position named an object"


def test_the_flat_action_index_round_trips(db, encoders):
    """`index` is how a sampled column becomes an action again."""
    senc, aenc = encoders
    state = _position(db, seed=7, steps=35)
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    encoded = senc.encode(obs)
    actions = action_batch([encoded], [legal], [obs], aenc)
    for column, action in enumerate(legal):
        assert aenc.decode(obs, int(actions["index"][0, column])) == action


def test_both_heads_receive_gradient(db, encoders, small):
    """A head with no gradient path is a head that never learns."""
    state = _position(db, seed=2, steps=45)
    batch, actions, _legal = _batch(state, encoders)
    logits, value = small(batch, actions)
    (logits.logsumexp(dim=-1).sum() + value.sum()).backward()

    assert small.value[0].weight.grad is not None
    assert small.policy[0].weight.grad is not None
    assert small.card.weight.grad is not None
    assert float(small.policy[0].weight.grad.abs().sum()) > 0
    small.zero_grad(set_to_none=True)


def test_model_size_comes_from_configuration(db, encoders):
    """Section 17: "make model size configurable".

    Asserted against two *different* shipped configs rather than against the
    defaults. A first version of this test read `default.toml` and compared it
    to 128 — which passed before `[model]` existed at all, because the
    dataclass defaults to 128 too. A configuration test that passes with no
    configuration is not testing configuration.
    """
    state_encoder, _ = encoders
    from learning.config import load

    full = ModelConfig.from_config(load("default"), state_encoder)
    assert (full.embedding_dim, full.transformer_layers,
            full.attention_heads, full.ff_dim) == (128, 4, 4, 512)

    tiny = ModelConfig.from_config(load("debug"), state_encoder)
    assert (tiny.embedding_dim, tiny.transformer_layers,
            tiny.attention_heads, tiny.ff_dim) == (32, 2, 2, 64)

    # And it reaches the built model, not just the dataclass.
    assert RiftboundNet(tiny).kind.weight.shape[1] == 32
    assert RiftboundNet(full).kind.weight.shape[1] == 128

    # Vocabulary sizes come from the encoder, never from a config file: a card
    # pool that grows must not be able to leave a stale number behind.
    assert full.cards == state_encoder.widths["cards"]
    assert full.kinds == state_encoder.widths["kinds"]
    assert "cards" not in load("default")["model"]


def test_the_same_seed_builds_the_same_weights(db, encoders):
    state_encoder, _ = encoders
    config = ModelConfig.from_config(
        {"model": {"embedding_dim": 16, "transformer_layers": 1,
                   "attention_heads": 2, "ff_dim": 32}},
        state_encoder,
    )
    torch.manual_seed(11)
    first = RiftboundNet(config)
    torch.manual_seed(11)
    second = RiftboundNet(config)
    for a, b in zip(first.parameters(), second.parameters()):
        assert torch.equal(a, b)
