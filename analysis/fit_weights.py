"""Fit the evaluation weights to self-play outcomes.

    .venv/bin/python analysis/fit_weights.py --games 300

Logistic regression by gradient descent, pure stdlib -- the label is "did this
player go on to win", so the weights are learned from what actually correlates
with winning rather than from anyone's intuition about what should.

Output goes to `weights.candidate.json`. It is NOT installed automatically,
because a better Brier score does not mean a better player -- see below.

Guards against fooling ourselves:

  * **Held-out split.** Weights are fitted on a training slice and scored on
    games the fit never saw. A model that improves on train and not on test has
    memorised noise.
  * **A candidate that does not improve held-out Brier is discarded**, loudly.
  * **Brier is not the promotion gate.** `analysis/benchmark.py` is. Promote
    with `--promote` only after the candidate has beaten the incumbent
    head-to-head.

## Why prediction is not control

Measured on this project: a fit improved held-out Brier from 0.1815 to 0.1666
and accuracy from 0.687 to 0.727 -- and then *lost* to the hand-set prior
14-26 when actually playing (95% interval 0.202-0.498, so the loss is real).

Two reasons, both worth remembering:

  * The labels come from **random** self-play, so the weights learn what
    correlates with winning among random agents, not what a player should steer
    toward. `hand_diff` came out negative because random agents that cannot
    play their cards accumulate them -- so a greedy agent maximising it
    actively dumps its hand.
  * **Distribution shift.** A greedy agent immediately moves the game off the
    random-play distribution the model was fitted on, into positions it has
    never scored.

Fixing this needs the data to come from the agents being trained (iterated
self-play), not from random rollouts.

The bias term is pinned to zero. Every feature is a difference between the two
players, so the model is antisymmetric and `evaluate(s, 0) + evaluate(s, 1)`
is exactly 1 -- an invariant search depends on. A learned bias is a constant
favouring one seat, which destroys it. Real seat advantage (485.7 gives the
player going second an extra rune) belongs in an antisymmetric feature, not in
a constant.

Position samples from the same game are correlated -- they share an outcome --
so the split is by *game*, never by position. Splitting by position would leak
the result across the boundary and report a flattering score.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.random_agent import RandomAgent  # noqa: E402
from analysis.evaluation import (  # noqa: E402
    CANDIDATE_PATH,
    FEATURE_NAMES,
    WEIGHTS_PATH,
    Model,
    features,
    load_model,
    save_model,
)
from cards.database import load as load_db  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402

MAX_STEPS = 40_000
Sample = tuple[list[float], float]  # (feature vector, label in {0,1})


def play_and_sample(seed: int, d0, d1, db, stride: int = 3) -> list[Sample]:
    """One self-play game -> (features, did-player-0-win) rows."""
    state = build_state(d0, d1, seed=seed, db=db, validate_decks=False)
    agents = (RandomAgent(seed), RandomAgent(seed + 104729))
    vectors: list[list[float]] = []
    steps = 0
    while not state.is_terminal():
        if steps >= MAX_STEPS:
            return []
        if steps % stride == 0:
            values = features(state, 0)
            vectors.append([values[name] for name in FEATURE_NAMES])
        state.apply(agents[state.current_player].act(state))
        steps += 1
    label = state.returns()[0]
    return [(vector, label) for vector in vectors]


def collect(games: int, base_seed: int, deck_names) -> list[list[Sample]]:
    db = load_db()
    d0, d1 = load_deck(deck_names[0]), load_deck(deck_names[1])
    per_game = []
    for i in range(games):
        rows = play_and_sample(base_seed + i, d0, d1, db)
        if rows:
            per_game.append(rows)
    return per_game


def brier(model: Model, samples: list[Sample]) -> float:
    if not samples:
        return float("nan")
    total = 0.0
    for vector, label in samples:
        score = model.bias + sum(w * x for w, x in zip(model.weights, vector))
        prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, score))))
        total += (prediction - label) ** 2
    return total / len(samples)


def fit(samples: list[Sample], epochs: int, lr: float, l2: float, seed: int) -> Model:
    n = len(FEATURE_NAMES)
    weights = [0.0] * n
    bias = 0.0  # pinned: a constant term would break antisymmetry
    rng = random.Random(seed)
    order = list(range(len(samples)))
    for epoch in range(epochs):
        rng.shuffle(order)
        # Simple decay: large steps early, fine adjustment late.
        step = lr / (1.0 + epoch)
        for index in order:
            vector, label = samples[index]
            score = bias + sum(w * x for w, x in zip(weights, vector))
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, score))))
            error = prediction - label
            for j in range(n):
                weights[j] -= step * (error * vector[j] + l2 * weights[j])
    return Model(weights=tuple(weights), bias=bias, fitted=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--lr", type=float, default=0.30)
    ap.add_argument("--l2", type=float, default=1e-4)
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--deck0", default="jinx_chaos_fury")
    ap.add_argument("--deck1", default="volibear_body_fury")
    ap.add_argument("--force", action="store_true",
                    help="write the candidate even if held-out Brier does not improve")
    ap.add_argument("--promote", action="store_true",
                    help="install as analysis/weights.json. Only do this after "
                         "analysis/benchmark.py shows the candidate beating the "
                         "incumbent head-to-head -- Brier alone is not enough.")
    args = ap.parse_args(argv)

    print(f"playing {args.games} self-play games…")
    per_game = collect(args.games, args.seed, (args.deck0, args.deck1))
    if len(per_game) < 8:
        print(f"only {len(per_game)} usable games; need more")
        return 1

    # Split by GAME, not by position: samples inside a game share a label.
    split = int(len(per_game) * (1 - args.test_frac))
    train = [row for game in per_game[:split] for row in game]
    test = [row for game in per_game[split:] for row in game]
    print(f"{len(per_game)} games -> {len(train)} train / {len(test)} test positions "
          f"({split} / {len(per_game) - split} games)")

    current = load_model()
    fitted = fit(train, args.epochs, args.lr, args.l2, args.seed)

    before_test, after_test = brier(current, test), brier(fitted, test)
    print(f"\ntest Brier  before {before_test:.4f}  ->  after {after_test:.4f}")
    print(f"train Brier before {brier(current, train):.4f}  ->  after {brier(fitted, train):.4f}")

    print("\nlearned weights:")
    for name, weight in sorted(zip(FEATURE_NAMES, fitted.weights), key=lambda p: -abs(p[1])):
        print(f"  {name:<20}{weight:+.4f}")
    print(f"  {'bias':<20}{fitted.bias:+.4f}")

    if after_test >= before_test and not args.force:
        print("\nNOT WRITTEN: the fit did not improve held-out Brier. "
              "Use --force to override, or collect more games.")
        return 1

    model = Model(weights=fitted.weights, bias=fitted.bias,
                  fitted=True, trained_on_games=len(per_game))
    save_model(model, CANDIDATE_PATH)
    print(f"\nwrote {CANDIDATE_PATH.name} (trained on {len(per_game)} games)")

    if args.promote:
        save_model(model, WEIGHTS_PATH)
        print(f"promoted to {WEIGHTS_PATH.name} -- benchmark it before trusting it")
    else:
        print(
            "\nNOT installed. A better Brier score does not mean a better "
            "player: on this project a fit that improved Brier lost 14-26 "
            "head-to-head. Run:\n"
            "  .venv/bin/python analysis/benchmark.py --games 40\n"
            "and re-run with --promote only if the candidate actually wins."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
