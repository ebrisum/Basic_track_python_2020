"""Does the learner actually learn? A test with a known answer.

    .venv/bin/python analysis/learning_check.py --iterations 300

Before scaling anything up, one question has to be settled: **does this
training loop learn at all, or does it just move numbers around?** Every
result so far has been of the form "the candidate scored 0.51, the interval
straddles even" -- which is equally consistent with a learner that works
weakly and a learner that is broken.

The problem is that there is no ground truth. Nobody knows the best weight
vector, so "did it get closer" is unanswerable.

## The fix: break something on purpose

Take weights already known to play well, **damage them in a specific way**,
and see whether training climbs back. Now there *is* a ground truth:

    healthy   the current weights, which beat random about 0.97
    damaged   the same weights with one feature deliberately wrecked
    trained   what the learner produces starting from `damaged`

and three checks with known-correct answers:

    1. damaged loses to healthy      -- the damage is real, not cosmetic
    2. trained beats damaged         -- the learner improved something
    3. trained closes the gap        -- it improved the *right* thing

Check 2 is the one that matters. If a learner cannot climb out of a hole
someone dug for it, it will certainly not find improvements nobody knows
about, and any "promising" result from it is noise.

This is a diagnostic, not a training run. It installs nothing.

Everything is measured in mirror matchups, because the decks are far louder
than the agents -- see `analysis/benchmark.mirror_field`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from analysis.benchmark import duel, interval, mirror_field  # noqa: E402
from analysis.evaluation import FEATURE_NAMES, Model, load_model  # noqa: E402
from analysis.ladder import elo_from_score  # noqa: E402
from analysis.tune import spsa  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import available_decks, load_deck  # noqa: E402


def damage(model: Model, feature: str, how: str) -> Model:
    """Wreck one feature's weight in a specific, reversible way.

    `zero` removes the feature's influence; `flip` inverts it, which is
    strictly worse than removing it -- the agent actively pursues the wrong
    thing. `flip` on `point_diff` makes an agent that avoids scoring, which
    should be catastrophic and is therefore the clearest signal.
    """
    index = FEATURE_NAMES.index(feature)
    weights = list(model.weights)
    if how == "zero":
        weights[index] = 0.0
    elif how == "flip":
        weights[index] = -weights[index]
    else:
        raise ValueError(f"unknown damage mode {how!r}")
    return Model(weights=tuple(weights), bias=model.bias)


def head_to_head(a: Model, b: Model, decks, db, games: int, seed: int):
    field = mirror_field(decks)
    wins = losses = draws = 0
    for i in range(games):
        pair = field[i % len(field)]
        _, w, l, d = duel(lambda s: GreedyAgent(s, a), lambda s: GreedyAgent(s, b),
                          1, seed + i, pair, db, start_index=i)
        wins, losses, draws = wins + w, losses + l, draws + d
    played = wins + losses + draws
    score = (wins + 0.5 * draws) / played if played else float("nan")
    low, high = interval(score, played)
    return score, (wins, losses, draws), (low, high)


def row(label, score, record, bounds) -> str:
    wins, losses, draws = record
    return (f"| {label} | {score:.3f} | {wins}-{losses}-{draws} "
            f"| {elo_from_score(score):+.0f} | {bounds[0]:.3f}–{bounds[1]:.3f} |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feature", default="point_diff",
                    help="which weight to damage")
    ap.add_argument("--how", default="flip", choices=("flip", "zero"))
    ap.add_argument("--iterations", type=int, default=300,
                    help="SPSA iterations for the recovery attempt")
    ap.add_argument("--tune-games", type=int, default=2,
                    help="games per SPSA iteration")
    ap.add_argument("--games", type=int, default=200,
                    help="games per verification match")
    ap.add_argument("--a", type=float, default=0.05,
                    help="SPSA step size. Recovery has to travel a known "
                         "distance -- flipping a weight puts the target 2|w| "
                         "away -- so too small a step reports a learning "
                         "failure that is really a step-size failure.")
    ap.add_argument("--out", default="analysis/weights.recovered.json",
                    help="where to write the weights training produced")
    ap.add_argument("--seed", type=int, default=7_000_000)
    args = ap.parse_args(argv)

    db = load_db()
    decks = [load_deck(slug) for slug in available_decks()]

    healthy = load_model()
    broken = damage(healthy, args.feature, args.how)
    print(f"damaged `{args.feature}`: {healthy.weights[FEATURE_NAMES.index(args.feature)]:+.3f}"
          f" -> {broken.weights[FEATURE_NAMES.index(args.feature)]:+.3f} ({args.how})\n")

    print(f"training from the damaged weights: {args.iterations} iterations "
          f"x {args.tune_games} games…")
    trained, _ = spsa(broken, decks, db, args.iterations, args.tune_games,
                      args.seed, a=args.a, report_every=50)
    # Keep what it produced. This is a diagnostic, but a diagnostic that
    # throws away its own result is one you cannot act on -- and a recovery
    # run can land somewhere better than where it started, which is exactly
    # the case worth keeping.
    Path(args.out).write_text(
        json.dumps({"weights": list(trained.weights), "bias": trained.bias,
                    "fitted": True, "source": "learning_check recovery",
                    "damaged": args.feature, "how": args.how,
                    "iterations": args.iterations, "seed": args.seed},
                   indent=2) + "\n"
    )
    print(f"\nrecovered weights written to {args.out}")

    print("| match | score | W-L-D | Elo | 95% interval |")
    print("| --- | --- | --- | --- | --- |")
    checks = []

    score, record, bounds = head_to_head(broken, healthy, decks, db,
                                         args.games, args.seed + 10_000)
    print(row("damaged vs healthy", score, record, bounds))
    checks.append(("the damage is real", bounds[1] < 0.5))

    score_t, record_t, bounds_t = head_to_head(trained, broken, decks, db,
                                               args.games, args.seed + 20_000)
    print(row("trained vs damaged", score_t, record_t, bounds_t))
    checks.append(("the learner improved on it", bounds_t[0] > 0.5))

    score_h, record_h, bounds_h = head_to_head(trained, healthy, decks, db,
                                               args.games, args.seed + 30_000)
    print(row("trained vs healthy", score_h, record_h, bounds_h))
    checks.append(("it closed the gap", score_h > score))

    print()
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")

    verdict = all(passed for _, passed in checks[:2])
    print(f"\n{'LEARNS' if verdict else 'DOES NOT LEARN'}: "
          + ("the loop climbed out of a hole dug on purpose, so it is "
             "capable of finding improvements at all."
             if verdict else
             "the loop could not recover from known damage, so any weak "
             "positive result from it is noise, not learning."))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
