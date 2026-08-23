"""`TCG_AI_BUILD.md` sections 21 and 22 -- self-play and PPO.

The test that matters most is `test_the_two_seats_get_opposite_advantages`.
Riftbound alternates players, so consecutive decisions belong to opposite
seats. Running GAE across a whole game would credit one player's advantage to
the other, off by one decision — and it would look completely fine: the loss
would fall, the value head would fit something, and the policy would learn a
blend of both seats' preferences. Nothing crashes. That is exactly the kind of
bug a test has to catch, because nothing else will.

Second is `test_a_positive_advantage_raises_that_action_probability`. Every
other test here checks that PPO is *wired up*; that one checks it *works*.
"""

from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")

from analysis.validate import load_db, load_deck
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.network import ModelConfig, RiftboundNet
from model.ppo import (
    PPOConfig,
    PPOTrainer,
    advantages_for_one_player,
    outcome_for,
)


@pytest.fixture(scope="module")
def db():
    return load_db()


@pytest.fixture(scope="module")
def encoders(db):
    return StateEncoder(db), ActionEncoder()


def _tiny(encoders, seed: int = 0) -> RiftboundNet:
    state_encoder, _ = encoders
    torch.manual_seed(seed)
    return RiftboundNet(ModelConfig.from_config(
        {"model": {"embedding_dim": 32, "transformer_layers": 1,
                   "attention_heads": 2, "ff_dim": 64}},
        state_encoder,
    ))


# --- the outcome mapping --------------------------------------------------

def test_returns_map_to_the_interval_section_17_asks_for():
    assert outcome_for((1.0, 0.0), 0) == 1.0
    assert outcome_for((1.0, 0.0), 1) == -1.0
    assert outcome_for((0.5, 0.5), 0) == 0.0
    # A truncated game has no outcome, and must not be given one.
    assert outcome_for(None, 0) is None


# --- the two-player trap ---------------------------------------------------

def test_the_two_seats_get_opposite_advantages(db, encoders):
    """One game, two seats, opposite outcomes -- and opposite advantages.

    If GAE ran across the interleaved sequence instead of per player, the
    losing seat's decisions would inherit the winner's positive advantage on
    alternate steps. The signs below would be mixed rather than clean.
    """
    trainer = PPOTrainer(_tiny(encoders), *encoders, config=PPOConfig())

    class _Obs:                       # never encoded; `prepare` only reads keys
        pass

    decisions = []
    for step in range(6):
        decisions.append({
            "player": step % 2,       # strictly alternating, as the game is
            "observation": _Obs(), "legal": [], "action_index": 0,
            "log_probability": -1.0, "value": 0.0,
        })
    samples, dropped = trainer.prepare(
        [{"returns": (1.0, 0.0), "decisions": decisions}])

    assert dropped == 0
    assert len(samples) == 6
    # `prepare` groups by player: the first three samples are seat 0's.
    winner, loser = samples[:3], samples[3:]
    assert all(s.advantage > 0 for s in winner), [s.advantage for s in winner]
    assert all(s.advantage < 0 for s in loser), [s.advantage for s in loser]


def test_a_truncated_game_contributes_nothing(db, encoders):
    """`returns: None` means the action cap, not a draw.

    Bootstrapping these from the value head would train the model on its own
    guess and call it evidence.
    """
    trainer = PPOTrainer(_tiny(encoders), *encoders, config=PPOConfig())
    decisions = [{"player": i % 2, "observation": None, "legal": [],
                  "action_index": 0, "log_probability": -1.0, "value": 0.3}
                 for i in range(8)]
    samples, dropped = trainer.prepare(
        [{"returns": None, "decisions": decisions}])
    assert samples == []
    assert dropped == 8


def test_a_draw_is_a_real_outcome_and_is_kept(db, encoders):
    trainer = PPOTrainer(_tiny(encoders), *encoders, config=PPOConfig())
    decisions = [{"player": i % 2, "observation": None, "legal": [],
                  "action_index": 0, "log_probability": -1.0, "value": 0.0}
                 for i in range(4)]
    samples, dropped = trainer.prepare(
        [{"returns": (0.5, 0.5), "decisions": decisions}])
    assert len(samples) == 4 and dropped == 0
    assert all(s.target == 0.0 for s in samples)


# --- GAE itself ------------------------------------------------------------

def test_gae_credits_a_win_and_debits_a_loss():
    config = PPOConfig()
    won, _ = advantages_for_one_player([0.0, 0.0, 0.0], 1.0, config)
    lost, _ = advantages_for_one_player([0.0, 0.0, 0.0], -1.0, config)
    assert all(a > 0 for a in won)
    assert all(a < 0 for a in lost)
    assert won[-1] == pytest.approx(1.0)


def test_a_model_that_saw_it_coming_gets_a_small_advantage():
    """The point of a value baseline: an expected win teaches little."""
    config = PPOConfig()
    surprised, _ = advantages_for_one_player([0.0, 0.0], 1.0, config)
    expected, _ = advantages_for_one_player([0.9, 0.9], 1.0, config)
    assert abs(expected[-1]) < abs(surprised[-1])


def test_gamma_defaults_to_one_because_discounting_would_be_shaping():
    """Section 15's rule, arriving at the other end of the pipeline.

    A gamma below 1 makes a win worth less for having taken longer, which is
    reward shaping through a hyperparameter. `learning/config.py` refuses
    shaping on the way in; this is the same rule on the way out.
    """
    from learning.config import load
    assert PPOConfig().gamma == 1.0
    assert load("default")["ppo"]["gamma"] == 1.0


# --- the update actually learning -----------------------------------------

def _one_position(db, encoders, seed: int = 1, steps: int = 40):
    import random

    from engine.setup import build_state
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


def test_a_positive_advantage_raises_that_action_probability(db, encoders):
    """Wiring is not learning. This checks the update moves the policy.

    One real position, one action given a positive advantage and the rest
    negative, a handful of updates: the favoured action's probability must go
    up. If the clipped objective's sign were flipped -- the single easiest
    mistake to make here -- this would fail and nothing else would.
    """
    from model.ppo import Sample

    state = _one_position(db, encoders)
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    if len(legal) < 2:
        pytest.skip("position offered no choice")

    network = _tiny(encoders, seed=3)
    trainer = PPOTrainer(network, *encoders,
                         config=PPOConfig(learning_rate=0.05, epochs=1,
                                          minibatch=8, entropy_coef=0.0))

    from model.agent import NeuralAgent
    agent = NeuralAgent(network, encoders[0], encoders[1], seed=0, greedy=True)
    agent.act(state)
    before = agent.last_policy[0]

    favoured = 0
    samples = [
        Sample(observation=obs, legal=legal, action_index=favoured,
               log_probability=math.log(max(before, 1e-12)), value=0.0,
               advantage=1.0, target=1.0)
    ] + [
        Sample(observation=obs, legal=legal, action_index=other,
               log_probability=math.log(max(agent.last_policy[other], 1e-12)),
               value=0.0, advantage=-1.0, target=-1.0)
        for other in range(1, len(legal))
    ]

    for _ in range(6):
        trainer.update(samples)

    agent.act(state)
    after = agent.last_policy[0]
    assert after > before, (
        f"the favoured action's probability fell, {before:.4f} -> {after:.4f}"
    )


def test_gradients_are_clipped(db, encoders):
    """Section 22 lists gradient clipping; this checks it binds."""
    from model.ppo import Sample

    state = _one_position(db, encoders, seed=2, steps=50)
    obs = state.observation(state.current_player)
    legal = state.legal_actions()
    trainer = PPOTrainer(_tiny(encoders, seed=5), *encoders,
                         config=PPOConfig(max_grad_norm=0.5, epochs=1,
                                          minibatch=16))
    samples = [Sample(observation=obs, legal=legal, action_index=0,
                      log_probability=-5.0, value=0.0,
                      advantage=50.0, target=25.0)] * 4
    stats = trainer.update(samples)
    # `clip_grad_norm_` returns the norm *before* clipping, so a value above
    # the threshold is the evidence that clipping had something to do.
    assert stats.extra["grad_norm"] > 0.5


def test_entropy_is_finite_with_padded_action_sets(db, encoders):
    """Illegal entries are -inf logits, and 0 * -inf is nan if not masked.

    A nan here would poison the whole loss, and it only appears when the
    batch contains positions with different numbers of legal actions -- which
    is every real batch.
    """
    from model.ppo import Sample

    positions = []
    for seed in (1, 4, 7):
        state = _one_position(db, encoders, seed=seed, steps=25 + seed * 5)
        positions.append((state.observation(state.current_player),
                          state.legal_actions()))
    widths = {len(legal) for _obs, legal in positions}
    assert len(widths) > 1, "the batch is uniform; this test measures nothing"

    trainer = PPOTrainer(_tiny(encoders, seed=9), *encoders,
                         config=PPOConfig(epochs=1, minibatch=8))
    samples = [Sample(observation=obs, legal=legal, action_index=0,
                      log_probability=-1.0, value=0.0,
                      advantage=0.5, target=0.5)
               for obs, legal in positions]
    stats = trainer.update(samples)
    assert math.isfinite(stats.entropy)
    assert math.isfinite(stats.policy_loss)
    assert math.isfinite(stats.value_loss)
