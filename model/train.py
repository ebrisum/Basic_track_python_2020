"""The training loop: self-play, PPO, checkpoint, evaluate (sections 21-24).

One iteration is: play `games` self-play games, turn them into samples, run
PPO over them, save a checkpoint, and — every `eval_every` iterations —
measure the current weights against a fixed baseline **deterministically**,
as section 22 requires, so a number is a property of the weights rather than
of a random draw.

What this loop does not do, and says so rather than pretending: it does not
promote checkpoints by SPRT into the league (section 24). `analysis/ladder.py`
and `analysis/rate.py` already do that for the linear evaluator, and pointing
them at a neural checkpoint is a separate job from making PPO run.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import torch

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from cards.database import load as load_pool
from engine.setup import build_state, load_deck
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model import checkpoint as checkpoints
from model.agent import NeuralAgent
from model.network import ModelConfig, RiftboundNet
from model.ppo import PPOConfig, PPOTrainer
from model.selfplay import play_many

BASELINES = {"random": RandomAgent, "greedy": GreedyAgent}


def evaluate_against(
    network: RiftboundNet,
    state_encoder: StateEncoder,
    action_encoder: ActionEncoder,
    db,
    decks: tuple[str, str],
    opponent: str = "random",
    games: int = 20,
    seed0: int = 900_000,
    action_cap: int = 3000,
) -> dict:
    """Deterministic evaluation: greedy policy, fixed seeds, seats alternated."""
    deck0, deck1 = load_deck(decks[0]), load_deck(decks[1])
    make_opponent = BASELINES[opponent]
    score = 0.0
    finished = 0
    for index in range(games):
        seed = seed0 + index
        state = build_state(deck0, deck1, seed=seed, db=db)
        seat = index % 2
        agents = [None, None]
        agents[seat] = NeuralAgent(network, state_encoder, action_encoder,
                                   seed=seed, greedy=True)
        agents[1 - seat] = make_opponent(seed + 7)
        steps = 0
        while not state.is_terminal() and steps < action_cap:
            legal = state.legal_actions()
            if not legal:
                break
            state.apply(agents[state.current_player].act(state))
            steps += 1
        if state.is_terminal():
            score += state.returns()[seat]
            finished += 1
    return {
        "opponent": opponent,
        "games": games,
        "finished": finished,
        # Unfinished games are excluded rather than scored 0.5, for the same
        # reason `learning/trajectory.py` refuses to call them draws.
        "win_rate": (score / finished) if finished else None,
    }


def train(
    iterations: int = 2,
    games_per_iteration: int = 4,
    config: dict | None = None,
    decks: tuple[str, str] = ("jinx_chaos_fury", "volibear_body_fury"),
    out: str | Path = "runs/checkpoints",
    seed: int = 0,
    eval_every: int = 1,
    eval_games: int = 20,
    action_cap: int = 3000,
    resume: str | Path | None = None,
    log=print,
) -> dict:
    config = config or {}
    db = load_pool()
    state_encoder = StateEncoder(db)
    action_encoder = ActionEncoder()

    if resume:
        network, payload = checkpoints.load(resume, state_encoder, action_encoder)
        log(f"resumed from {resume}"
            f" (iteration {payload.get('extra', {}).get('iteration')})")
    else:
        torch.manual_seed(seed)
        network = RiftboundNet(ModelConfig.from_config(config, state_encoder))

    trainer = PPOTrainer(network, state_encoder, action_encoder,
                         PPOConfig.from_config(config))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    history = []
    started = time.time()
    for iteration in range(1, iterations + 1):
        played = time.time()
        games = play_many(
            network, state_encoder, action_encoder, db, decks=decks,
            games=games_per_iteration,
            seed0=seed + iteration * 10_000, action_cap=action_cap,
        )
        samples, dropped = trainer.prepare(games)
        stats = trainer.update(samples)

        record = {
            "iteration": iteration,
            "games": len(games),
            "decisions": sum(len(g["decisions"]) for g in games),
            "samples": stats.samples,
            "dropped_truncated": dropped,
            "unfinished_games": sum(1 for g in games if not g["terminal"]),
            "policy_loss": round(stats.policy_loss, 5),
            "value_loss": round(stats.value_loss, 5),
            "entropy": round(stats.entropy, 4),
            "approx_kl": round(stats.approx_kl, 6),
            "clip_fraction": round(stats.clip_fraction, 4),
            "seconds": round(time.time() - played, 1),
        }
        if eval_every and iteration % eval_every == 0:
            record["evaluation"] = evaluate_against(
                network, state_encoder, action_encoder, db, decks,
                games=eval_games, action_cap=action_cap)
        history.append(record)
        log(json.dumps(record))

        checkpoints.save(
            out / f"gen_{iteration:03d}.pt", network, state_encoder,
            action_encoder, db=db,
            extra={"iteration": iteration, "stats": record,
                   "ppo": asdict(trainer.config)},
        )

    summary = {
        "iterations": iterations,
        "wall_seconds": round(time.time() - started, 1),
        "checkpoints": str(out),
        "history": history,
    }
    (out / "history.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
