"""PPO with self-play (`TCG_AI_BUILD.md` sections 21 and 22).

Section 22 lists what a first RL implementation must have: a clipped policy
objective, a value loss, an entropy bonus, gradient clipping, checkpointing and
deterministic evaluation. All of those are here. The parts worth reading are
the two places where a standard PPO implementation would be *wrong* for this
game, and one place where a default is not a default.

**Riftbound alternates players, so a game is not one trajectory.**
Consecutive decisions belong to opposite seats. Running GAE across them
straight would propagate one player's advantage into the other's — the
opponent's good position would look like this player's, off by one decision.
So a game is split into per-player subsequences and each is treated as its own
episode, with its own terminal reward. This is the single most likely place for
a plausible-looking bug to hide, and `test_ppo.py` pins it with a game whose
two seats get opposite outcomes.

**The reward is terminal and undiscounted.** `gamma` defaults to 1.0, and not
because a Riftbound game is short: discounting would make a win worth less for
having taken longer, which is reward shaping arriving through a hyperparameter.
`analysis/evaluation.py` and `learning/config.py` both refuse shaping on the
way in; this is the same rule at the other end. `returns()` is remapped from
1.0 / 0.5 / 0.0 to +1 / 0 / -1 to match section 17's value range.

**A truncated game contributes no value target.** `learning/trajectory.py`
records a game that hit the action cap as `terminal: false` with no returns,
because inventing a draw would be a fabricated label. That distinction has to
survive into training: those transitions are dropped rather than bootstrapped
from a value estimate the model has not earned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch
from torch import nn

from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.batching import action_batch, state_batch
from model.network import RiftboundNet


@dataclass(frozen=True)
class PPOConfig:
    """Section 22's example configuration, treated as defaults."""

    learning_rate: float = 3e-4
    gamma: float = 1.0
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 0.5
    epochs: int = 4
    minibatch: int = 64

    @classmethod
    def from_config(cls, config: dict) -> "PPOConfig":
        ppo = dict(config.get("ppo", {}))
        return cls(
            learning_rate=float(ppo.get("learning_rate", 3e-4)),
            gamma=float(ppo.get("gamma", 1.0)),
            gae_lambda=float(ppo.get("gae_lambda", 0.95)),
            clip_range=float(ppo.get("clip_range", 0.2)),
            entropy_coef=float(ppo.get("entropy_coef", 0.01)),
            value_coef=float(ppo.get("value_coef", 0.5)),
            max_grad_norm=float(ppo.get("max_grad_norm", 0.5)),
            epochs=int(ppo.get("epochs", 4)),
            minibatch=int(ppo.get("minibatch", 64)),
        )


@dataclass
class Sample:
    """One decision, ready for a gradient step."""

    observation: object            # a RiftboundObservation
    legal: list                    # the legal actions at that decision
    action_index: int              # position within `legal` that was taken
    log_probability: float         # under the policy that generated it
    value: float                   # what that policy's value head predicted
    advantage: float = 0.0
    target: float = 0.0


@dataclass
class Stats:
    policy_loss: float = 0.0
    value_loss: float = 0.0
    entropy: float = 0.0
    approx_kl: float = 0.0
    clip_fraction: float = 0.0
    samples: int = 0
    dropped_truncated: int = 0
    updates: int = 0
    extra: dict = field(default_factory=dict)


def outcome_for(returns: tuple[float, ...] | None, player: int) -> float | None:
    """`returns()`'s 1.0 / 0.5 / 0.0 as section 17's +1 / 0 / -1.

    None for a truncated game: it produced no outcome, and a fabricated one is
    worse than a dropped sample.
    """
    if returns is None:
        return None
    return float(returns[player]) * 2.0 - 1.0


def advantages_for_one_player(
    values: list[float], outcome: float, config: PPOConfig
) -> tuple[list[float], list[float]]:
    """GAE over *one seat's* decisions in one game.

    Called per player, never across a whole game: consecutive decisions in
    Riftbound belong to opposite seats, and running this over the mixed
    sequence would credit one player's advantage to the other.

    Rewards are zero everywhere but the last decision, which receives the
    game's outcome, so this reduces to a lambda-weighted blend between the
    plain return and the one-step TD error.
    """
    count = len(values)
    advantages = [0.0] * count
    running = 0.0
    for step in reversed(range(count)):
        last = step == count - 1
        reward = outcome if last else 0.0
        next_value = 0.0 if last else values[step + 1]
        not_done = 0.0 if last else 1.0
        delta = reward + config.gamma * next_value * not_done - values[step]
        running = delta + config.gamma * config.gae_lambda * not_done * running
        advantages[step] = running
    targets = [a + v for a, v in zip(advantages, values)]
    return advantages, targets


class PPOTrainer:
    """Owns the network, the optimiser, and one update over a batch of games."""

    def __init__(
        self,
        network: RiftboundNet,
        state_encoder: StateEncoder,
        action_encoder: ActionEncoder | None = None,
        config: PPOConfig | None = None,
        device=None,
    ) -> None:
        self.network = network
        self.state_encoder = state_encoder
        self.action_encoder = action_encoder or ActionEncoder()
        self.config = config or PPOConfig()
        self.device = device
        self.optimizer = torch.optim.Adam(
            network.parameters(), lr=self.config.learning_rate
        )

    # -- preparing a batch -------------------------------------------------

    def prepare(self, games: list[dict]) -> tuple[list[Sample], int]:
        """Turn recorded games into samples with advantages and targets.

        Each game is `{"returns": tuple | None, "decisions": [...]}` where a
        decision carries `player`, `observation`, `legal`, `action_index`,
        `log_probability` and `value`.
        """
        samples: list[Sample] = []
        dropped = 0
        for game in games:
            returns = game.get("returns")
            for player in (0, 1):
                theirs = [d for d in game["decisions"] if d["player"] == player]
                if not theirs:
                    continue
                outcome = outcome_for(returns, player)
                if outcome is None:
                    # 'terminal: false' -- the action cap, not a draw.
                    dropped += len(theirs)
                    continue
                values = [float(d["value"]) for d in theirs]
                advantages, targets = advantages_for_one_player(
                    values, outcome, self.config
                )
                for decision, advantage, target in zip(theirs, advantages, targets):
                    samples.append(Sample(
                        observation=decision["observation"],
                        legal=decision["legal"],
                        action_index=int(decision["action_index"]),
                        log_probability=float(decision["log_probability"]),
                        value=float(decision["value"]),
                        advantage=advantage,
                        target=target,
                    ))
        return samples, dropped

    # -- one update --------------------------------------------------------

    def _forward(self, batch: list[Sample]):
        encoded = [self.state_encoder.encode(s.observation) for s in batch]
        states = state_batch(encoded, device=self.device)
        actions = action_batch(
            encoded, [s.legal for s in batch], [s.observation for s in batch],
            self.action_encoder, device=self.device,
        )
        logits, values = self.network(states, actions)
        log_probabilities = torch.log_softmax(logits, dim=-1)
        taken = torch.tensor([s.action_index for s in batch],
                             dtype=torch.long, device=self.device)
        chosen = log_probabilities.gather(1, taken.unsqueeze(1)).squeeze(1)

        # Entropy over the legal set only. `-inf` logits contribute a 0 * -inf
        # that must be masked rather than computed, or the whole loss is nan.
        probabilities = log_probabilities.exp()
        finite = torch.isfinite(log_probabilities)
        entropy = -(probabilities * torch.where(
            finite, log_probabilities, torch.zeros_like(log_probabilities)
        )).sum(dim=-1)
        return chosen, values, entropy

    def update(self, samples: list[Sample]) -> Stats:
        """Section 22's clipped objective, value loss, entropy and clipping."""
        stats = Stats(samples=len(samples))
        if not samples:
            return stats

        advantages = torch.tensor([s.advantage for s in samples],
                                  dtype=torch.float32, device=self.device)
        # Normalised per update, which is standard and matters here: the
        # advantage scale is set by the outcome (+1/-1), so an unnormalised
        # batch makes the learning rate mean different things at different
        # stages of a game.
        if len(samples) > 1:
            advantages = (advantages - advantages.mean()) / (
                advantages.std(unbiased=False) + 1e-8)

        order = list(range(len(samples)))
        size = max(1, self.config.minibatch)
        self.network.train()

        for _epoch in range(self.config.epochs):
            for start in range(0, len(order), size):
                chunk = order[start:start + size]
                batch = [samples[i] for i in chunk]
                chosen, values, entropy = self._forward(batch)

                old = torch.tensor([s.log_probability for s in batch],
                                   dtype=torch.float32, device=self.device)
                target = torch.tensor([s.target for s in batch],
                                      dtype=torch.float32, device=self.device)
                advantage = advantages[chunk]

                ratio = torch.exp(chosen - old)
                unclipped = ratio * advantage
                clipped = torch.clamp(
                    ratio, 1.0 - self.config.clip_range,
                    1.0 + self.config.clip_range) * advantage
                policy_loss = -torch.min(unclipped, clipped).mean()
                value_loss = nn.functional.mse_loss(values, target)
                entropy_bonus = entropy.mean()

                loss = (policy_loss
                        + self.config.value_coef * value_loss
                        - self.config.entropy_coef * entropy_bonus)

                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                grad_norm = nn.utils.clip_grad_norm_(
                    self.network.parameters(), self.config.max_grad_norm)
                self.optimizer.step()

                with torch.no_grad():
                    log_ratio = chosen - old
                    stats.approx_kl += float(
                        ((log_ratio.exp() - 1) - log_ratio).mean())
                    stats.clip_fraction += float(
                        ((ratio - 1.0).abs() > self.config.clip_range)
                        .float().mean())
                    stats.policy_loss += float(policy_loss.detach())
                    stats.value_loss += float(value_loss.detach())
                    stats.entropy += float(entropy_bonus.detach())
                    stats.extra["grad_norm"] = float(grad_norm)
                stats.updates += 1

        if stats.updates:
            for name in ("policy_loss", "value_loss", "entropy",
                         "approx_kl", "clip_fraction"):
                setattr(stats, name, getattr(stats, name) / stats.updates)
        return stats
