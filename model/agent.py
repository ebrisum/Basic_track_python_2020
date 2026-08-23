"""An agent that plays from the network (`TCG_AI_BUILD.md` sections 18, 21).

Sees exactly what every other agent sees: `legal_actions()`, `observation()`,
`current_player`. It holds a model rather than a heuristic, and that is the
only difference — `agents/greedy_agent.py` and this one are interchangeable in
the benchmark, the league, the scenario suite and the validation runner.

After each decision it leaves `last_policy` and `last_value` on itself.
`learning/trajectory.py` reads those with `getattr`, so a self-play game
records the probability the policy assigned and the value it predicted without
either module knowing about the other. Section 21 asks for exactly those two
fields per decision, and section 22 needs them to compute a ratio and an
advantage.
"""

from __future__ import annotations

import random

import torch

from engine.interface import Action, GameState
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.batching import action_batch, state_batch
from model.network import RiftboundNet


class NeuralAgent:
    """Samples from the policy, or takes its argmax for deterministic play."""

    def __init__(
        self,
        network: RiftboundNet,
        state_encoder: StateEncoder,
        action_encoder: ActionEncoder | None = None,
        seed: int = 0,
        temperature: float = 1.0,
        greedy: bool = False,
        device=None,
    ) -> None:
        self.network = network
        self.state_encoder = state_encoder
        self.action_encoder = action_encoder or ActionEncoder()
        self.temperature = temperature
        # Section 22 asks for "deterministic evaluation": the same checkpoint
        # in the same position must play the same move when it is being
        # measured, so a rating is a property of the weights and not of a
        # random draw.
        self.greedy = greedy
        self.device = device
        self._rng = random.Random(seed)
        self.seed = seed
        self.last_policy: list[float] | None = None
        self.last_value: float | None = None

    def act(self, state: GameState) -> Action:
        legal = state.legal_actions()
        if not legal:
            raise ValueError("no legal actions -- state should have been terminal")

        player = state.current_player
        obs = state.observation(player)
        encoded = self.state_encoder.encode(obs)
        batch = state_batch([encoded], device=self.device)
        actions = action_batch([encoded], [legal], [obs], self.action_encoder,
                               device=self.device)

        self.network.eval()
        with torch.no_grad():
            logits, value = self.network(batch, actions)

        row = logits[0, : len(legal)]
        if self.temperature != 1.0:
            row = row / max(self.temperature, 1e-6)
        probabilities = torch.softmax(row, dim=-1)

        # Recorded before the choice, so a trajectory carries the distribution
        # the policy actually had rather than a one-hot of what it picked.
        self.last_policy = [float(p) for p in probabilities]
        self.last_value = float(value[0])

        if self.greedy:
            return legal[int(torch.argmax(row))]

        # Sampled with the agent's own seeded RNG rather than torch's global
        # generator: every other agent in this project is reproducible from
        # its seed alone, and a policy that reached into a global stream would
        # make a self-play game depend on whatever else had drawn from it.
        threshold = self._rng.random()
        cumulative = 0.0
        for index, probability in enumerate(self.last_policy):
            cumulative += probability
            if threshold <= cumulative:
                return legal[index]
        return legal[-1]

    def __repr__(self) -> str:
        mode = "greedy" if self.greedy else f"T={self.temperature}"
        return f"NeuralAgent(seed={self.seed}, {mode})"
