"""Self-play rollouts (`TCG_AI_BUILD.md` section 21).

"For every decision save: observation, legal actions, selected action, action
probability, value estimate, final outcome." That is what a game here is.

The games are held in memory as Python objects rather than written through
`learning/trajectory.py`, and the reason is that they are different artefacts.
A trajectory file is a *dataset* -- slimmed, gzipped, and meant to outlive the
run that made it. A rollout is a buffer that PPO consumes and discards within
the same process, so serialising it would cost time and lose the observation
objects the encoder wants. `cli.py generate` still writes trajectories, and
`--record` here writes one too when a run should leave a dataset behind.
"""

from __future__ import annotations

import math

from engine.setup import build_state, load_deck
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.agent import NeuralAgent
from model.network import RiftboundNet

ACTION_CAP = 3000


def play_one(
    network: RiftboundNet,
    state_encoder: StateEncoder,
    action_encoder: ActionEncoder,
    deck0,
    deck1,
    seed: int,
    db,
    temperature: float = 1.0,
    action_cap: int = ACTION_CAP,
    opponent: object | None = None,
) -> dict:
    """One game. Returns `{"returns": ..., "decisions": [...], "terminal": ...}`.

    `opponent` lets the network be measured against a baseline agent instead of
    itself; only the network's own decisions are recorded, since the other
    seat's policy is not the one being trained.
    """
    state = build_state(deck0, deck1, seed=seed, db=db)
    learner = NeuralAgent(network, state_encoder, action_encoder,
                          seed=seed, temperature=temperature)
    # Seats alternate by seed so the policy is not trained only on the play.
    learner_seat = seed % 2
    agents = [None, None]
    agents[learner_seat] = learner
    agents[1 - learner_seat] = opponent if opponent is not None else NeuralAgent(
        network, state_encoder, action_encoder, seed=seed + 104729,
        temperature=temperature)

    decisions: list[dict] = []
    steps = 0
    while not state.is_terminal() and steps < action_cap:
        legal = state.legal_actions()
        if not legal:
            break
        player = state.current_player
        agent = agents[player]
        observation = state.observation(player)
        action = agent.act(state)

        policy = getattr(agent, "last_policy", None)
        value = getattr(agent, "last_value", None)
        if policy is not None and value is not None:
            index = legal.index(action)
            probability = max(policy[index], 1e-12)
            decisions.append({
                "player": player,
                "observation": observation,
                "legal": legal,
                "action_index": index,
                "action": action,
                "probability": policy[index],
                "log_probability": math.log(probability),
                "value": value,
            })
        state.apply(action)
        steps += 1

    terminal = state.is_terminal()
    return {
        "seed": seed,
        "returns": tuple(state.returns()) if terminal else None,
        "terminal": terminal,
        "winner": state.winner,
        "turns": state.turn_number,
        "steps": steps,
        "learner_seat": learner_seat,
        "decisions": decisions,
    }


def play_many(
    network: RiftboundNet,
    state_encoder: StateEncoder,
    action_encoder: ActionEncoder,
    db,
    decks: tuple[str, str] = ("jinx_chaos_fury", "volibear_body_fury"),
    games: int = 8,
    seed0: int = 0,
    temperature: float = 1.0,
    action_cap: int = ACTION_CAP,
    opponent_for=None,
) -> list[dict]:
    deck0, deck1 = load_deck(decks[0]), load_deck(decks[1])
    out = []
    for index in range(games):
        seed = seed0 + index
        out.append(play_one(
            network, state_encoder, action_encoder, deck0, deck1,
            seed=seed, db=db, temperature=temperature, action_cap=action_cap,
            opponent=opponent_for(seed) if opponent_for else None,
        ))
    return out
