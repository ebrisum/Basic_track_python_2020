"""Tune the evaluation weights directly against games won.

    .venv/bin/python analysis/tune.py --iterations 120 --games 12 --field

`fit_weights.py` optimises a *prediction* loss, and this project already
learned the hard way where that leads: a candidate that improved held-out
Brier from 0.1815 to 0.1666 then lost 14-26 when it actually played. Brier
rewards being right about positions the agent may never steer into; it does
not reward steering.

SPSA -- Simultaneous Perturbation Stochastic Approximation -- optimises the
thing we actually want. It is what Stockfish uses to tune evaluation
parameters, and the loop is short:

    1. Pick a random direction, one +/-1 per weight.
    2. Build two agents: weights nudged along that direction, and against it.
    3. Play them against each other.
    4. Step the weights toward whichever side won.

The trick that makes it affordable is step 3. A naive gradient needs one
measurement per weight -- thirteen matches per iteration here. SPSA perturbs
*every* weight at once and extracts a usable gradient estimate from a single
match, because over many iterations the random directions decorrelate and the
irrelevant components average out. It is noisy per step and sound in
aggregate, which is exactly the right trade when each measurement costs a
game of Riftbound.

Playing the two perturbations against *each other* rather than against a fixed
opponent doubles the signal per game: one match yields both y+ and y-, since
one side's score is one minus the other's.

## What this does not do

It does not promote anything. Tuning on N games and then reporting the score
from those same N games is how you measure noise and call it progress. The
tuned candidate is written to `analysis/weights.candidate.json`, and
`--validate` re-tests it on *fresh seeds* with SPRT before anything is
installed.

The bias stays pinned at zero throughout. A non-zero bias breaks the
antisymmetry that makes `evaluate(s, 0) + evaluate(s, 1) == 1`, which has
already been broken once here by a fitted bias.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from analysis.benchmark import duel  # noqa: E402
from analysis.evaluation import (  # noqa: E402
    FEATURE_NAMES,
    CANDIDATE_PATH,
    Model,
    load_model,
)
from analysis.ladder import SPRT, elo_from_score, elo_interval  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import available_decks, load_deck  # noqa: E402


def matchups(decks, mirror_only: bool = False):
    """Deck pairings to play over.

    `mirror_only` restricts to same-deck matchups, which removes deck
    imbalance from the comparison entirely -- see `benchmark.mirror_field`.
    """
    if mirror_only:
        return [(deck, deck) for deck in decks]
    return [(a, b) for a in decks for b in decks]


def _norm(weights) -> float:
    return sum(w * w for w in weights) ** 0.5


def renormalise(theta: list[float], target: float) -> list[float]:
    """Rescale `theta` back to a fixed length.

    A greedy agent takes the argmax of a weighted sum, so multiplying every
    weight by the same positive constant changes nothing it does. The overall
    scale is therefore a direction in which SPSA can wander forever while
    measuring pure noise, and it will: the gradient estimate has a component
    along it every iteration. Pinning the length removes the degeneracy and
    spends the whole budget on the ratios, which are the only thing that
    affects play.
    """
    length = _norm(theta)
    if length <= 1e-9:
        return theta
    factor = target / length
    return [w * factor for w in theta]


def spsa(
    start: Model,
    decks,
    db,
    iterations: int,
    games: int,
    seed: int,
    a: float = 0.02,
    c: float = 0.15,
    alpha: float = 0.602,
    gamma: float = 0.101,
    report_every: int = 10,
) -> tuple[Model, list[dict]]:
    """Run SPSA on the weight vector. Returns the tuned model and a trace.

    `a` and `c` are the step and perturbation sizes; `alpha` and `gamma` are
    the standard decay exponents from Spall's original paper, which shrink
    both over time so the search settles instead of rattling around.
    """
    theta = list(start.weights)
    target_norm = _norm(theta)
    rng = random.Random(seed)
    # Mirrors. Each iteration's gradient signal is a two-game match between
    # two perturbations of the same weights -- an agent-vs-agent comparison,
    # and the decks are far louder than the agents: volibear beats jinx about
    # 85-15 with identical agents. Tuning across mismatched decks feeds SPSA a
    # gradient made mostly of deck luck, which is how a 1,600-game run moved
    # almost nothing.
    field = matchups(decks, mirror_only=True)
    trace: list[dict] = []
    started = time.perf_counter()

    for k in range(iterations):
        step = a / (k + 1 + 0.1 * iterations) ** alpha
        perturb = c / (k + 1) ** gamma
        # +/-1 per weight (a Bernoulli perturbation, as SPSA requires: the
        # estimator needs a distribution with finite inverse moments, which
        # rules out the obvious choice of Gaussian noise).
        delta = [1.0 if rng.random() < 0.5 else -1.0 for _ in theta]

        plus = Model(weights=tuple(t + perturb * d for t, d in zip(theta, delta)))
        minus = Model(weights=tuple(t - perturb * d for t, d in zip(theta, delta)))

        pair = field[k % len(field)]
        score, wins, losses, draws = duel(
            lambda s: GreedyAgent(s, plus),
            lambda s: GreedyAgent(s, minus),
            games, seed + 50_000 + k * games, pair, db, start_index=k,
        )
        if wins + losses + draws == 0:
            continue

        # y+ - y- = 2*score - 1, and 1/delta_i == delta_i for +/-1.
        gradient_scale = (2.0 * score - 1.0) / (2.0 * perturb)
        for i in range(len(theta)):
            theta[i] += step * gradient_scale * delta[i]
        theta = renormalise(theta, target_norm)

        if report_every and (k + 1) % report_every == 0:
            elapsed = time.perf_counter() - started
            moved = max(
                range(len(theta)), key=lambda i: abs(theta[i] - start.weights[i])
            )
            print(
                f"  iter {k + 1:4d}/{iterations}  score {score:.2f}  "
                f"step {step:.4f}  biggest move: {FEATURE_NAMES[moved]} "
                f"{start.weights[moved]:+.2f}->{theta[moved]:+.2f}  "
                f"[{elapsed / 60:.1f} min]"
            )
        trace.append({"iteration": k, "score": score, "weights": list(theta)})

    return Model(weights=tuple(theta), bias=0.0, fitted=True), trace


def validate(candidate: Model, incumbent: Model, decks, db, max_games: int,
             seed: int, elo0: float, elo1: float):
    """SPRT the candidate against the incumbent on seeds tuning never saw."""
    test = SPRT(elo0=elo0, elo1=elo1)
    field = matchups(decks, mirror_only=True)   # see benchmark.mirror_field
    wins = losses = draws = 0
    for i in range(max_games):
        pair = field[i % len(field)]
        score, w, l, d = duel(
            lambda s: GreedyAgent(s, candidate),
            lambda s: GreedyAgent(s, incumbent),
            1, seed + i, pair, db, start_index=i,
        )
        if w + l + d == 0:
            continue
        wins, losses, draws = wins + w, losses + l, draws + d
        test.record(score)
        if i % 2 == 1 and test.verdict() != "continue":
            break
    return test, wins, losses, draws


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=120)
    ap.add_argument("--games", type=int, default=12,
                    help="games per SPSA iteration; noisy is fine, biased is not")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--a", type=float, default=0.02, help="SPSA step size")
    ap.add_argument("--c", type=float, default=0.15,
                    help="SPSA perturbation size; larger means a cleaner "
                         "gradient signal per game and a coarser search")
    ap.add_argument("--field", action="store_true",
                    help="rotate through every deck in decks/")
    ap.add_argument("--deck0", default="jinx_chaos_fury")
    ap.add_argument("--deck1", default="volibear_body_fury")
    ap.add_argument("--validate", type=int, default=400,
                    help="max games for the fresh-seed SPRT; 0 to skip")
    ap.add_argument("--elo0", type=float, default=0.0)
    ap.add_argument("--elo1", type=float, default=20.0)
    ap.add_argument("--trace", default=None, help="write the weight trace here")
    args = ap.parse_args(argv)

    db = load_db()
    if args.field:
        decks = tuple(load_deck(slug) for slug in available_decks())
    else:
        decks = (load_deck(args.deck0), load_deck(args.deck1))
    print(f"tuning over {len(decks)} deck(s), "
          f"{args.iterations} iterations x {args.games} games "
          f"= {args.iterations * args.games} games")

    incumbent = load_model()
    tuned, trace = spsa(incumbent, decks, db, args.iterations, args.games,
                        args.seed, a=args.a, c=args.c)

    print("\nweight changes:")
    for name, before, after in zip(FEATURE_NAMES, incumbent.weights, tuned.weights):
        arrow = "" if abs(after - before) < 0.02 else "   <-- moved"
        print(f"  {name:20} {before:+.3f} -> {after:+.3f}{arrow}")

    CANDIDATE_PATH.write_text(
        json.dumps({"weights": list(tuned.weights), "bias": 0.0,
                    "fitted": True, "source": "spsa"}, indent=2) + "\n"
    )
    print(f"\ncandidate written to {CANDIDATE_PATH.name} (nothing installed)")

    if args.trace:
        Path(args.trace).write_text(json.dumps(trace, indent=2) + "\n")

    if args.validate:
        print(f"\nvalidating on fresh seeds, SPRT up to {args.validate} games…")
        test, wins, losses, draws = validate(
            tuned, incumbent, decks, db, args.validate,
            args.seed + 900_000, args.elo0, args.elo1,
        )
        low, high = elo_interval(test.score, max(1, test.games))
        print(f"  {test.summary()}")
        print(f"  tuned {wins}-{losses}-{draws}, "
              f"{elo_from_score(test.score):+.0f} Elo [{low:+.0f}, {high:+.0f}]")
        if test.verdict() == "accept":
            print("\n  ACCEPTED — stronger on seeds the tuning never saw.")
            print("  install with: cp analysis/weights.candidate.json "
                  "analysis/weights.json")
        else:
            print(f"\n  NOT accepted ({test.verdict()}). The candidate did not "
                  "show itself stronger on fresh seeds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
