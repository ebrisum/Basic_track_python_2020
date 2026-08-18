"""Verified hill climbing: change one weight, prove it helped, keep it.

    .venv/bin/python analysis/climb.py --sweeps 2 --screen 60

SPSA perturbs all thirteen weights at once and reads a gradient out of a
single two-game result. That is the right trade when games are cheap and
parameters are many -- Stockfish tunes this way over tens of thousands of
games. Here games cost about half a second each and there are thirteen
parameters, so the trade runs the other way: with a budget of a thousand
games, thirteen simultaneous perturbations put roughly seventy games behind
each parameter, all of it filtered through a one-bit-per-iteration signal.

Coordinate ascent spends the same budget differently. It changes **one**
weight, plays a paired mirror match against the current best, and keeps the
change only if it wins. Every game measures the thing being decided, so the
signal per game is far larger -- and every accepted step has been tested,
which is what makes the result trustworthy rather than hopeful.

## Two stages, because a screen is not a proof

A cheap screen (default 60 paired games) proposes and filters. It will let
some noise through -- that is what "cheap" buys. So the *whole* sweep is then
confirmed against the weights it started from, with SPRT, on seeds the screen
never saw. If the confirmation fails, nothing is installed and the run says
so. A screen that accepts noise is fine; a screen that installs noise is not.

Everything is measured in paired mirror matches: same deck both sides, same
seed replayed from both sides. The decks are far louder than the agents here
and the shuffles are louder still, and neither is the thing being measured.
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
from analysis.benchmark import duel, mirror_field  # noqa: E402
from analysis.evaluation import (  # noqa: E402
    CANDIDATE_PATH,
    FEATURE_NAMES,
    Model,
    load_model,
)
from analysis.ladder import SPRT, elo_from_score, elo_interval  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import available_decks, load_deck  # noqa: E402


def match(a: Model, b: Model, field, db, games: int, seed: int) -> float:
    """Paired mirror score for `a` against `b`."""
    wins = losses = draws = 0
    for i in range(0, games, 2):
        pair = field[(i // 2) % len(field)]
        _, w, l, d = duel(lambda s: GreedyAgent(s, a), lambda s: GreedyAgent(s, b),
                          2, seed + i, pair, db, paired=True)
        wins, losses, draws = wins + w, losses + l, draws + d
    played = wins + losses + draws
    return (wins + 0.5 * draws) / played if played else 0.5


def confirm(a: Model, b: Model, field, db, cap: int, seed: int,
            elo0: float, elo1: float):
    """SPRT `a` against `b` on fresh seeds."""
    test = SPRT(elo0=elo0, elo1=elo1)
    wins = losses = draws = 0
    for i in range(0, cap, 2):
        pair = field[(i // 2) % len(field)]
        score, w, l, d = duel(lambda s: GreedyAgent(s, a), lambda s: GreedyAgent(s, b),
                              2, seed + i, pair, db, paired=True)
        if w + l + d == 0:
            continue
        wins, losses, draws = wins + w, losses + l, draws + d
        test.record(score)
        if test.verdict() != "continue":
            break
    return test, (wins, losses, draws)


def nudged(model: Model, index: int, factor: float) -> Model:
    weights = list(model.weights)
    base = weights[index]
    # A weight sitting at zero cannot be scaled off it, so give it a floor to
    # move from -- otherwise a feature the prior ignored can never be found.
    weights[index] = base * factor if abs(base) > 1e-6 else (factor - 1.0) * 0.1
    return Model(weights=tuple(weights), bias=model.bias)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sweeps", type=int, default=2)
    ap.add_argument("--screen", type=int, default=60,
                    help="paired games per proposal (rounded down to even)")
    ap.add_argument("--accept", type=float, default=0.56,
                    help="screen threshold; deliberately loose, the "
                         "confirmation is what decides")
    ap.add_argument("--step", type=float, default=0.4,
                    help="relative size of each proposed change")
    ap.add_argument("--confirm", type=int, default=400,
                    help="max games for the final SPRT; 0 to skip")
    ap.add_argument("--elo0", type=float, default=0.0)
    ap.add_argument("--elo1", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=5_500_000)
    ap.add_argument("--start", default=None,
                    help="weights JSON to start from (default: installed)")
    args = ap.parse_args(argv)

    db = load_db()
    decks = [load_deck(slug) for slug in available_decks()]
    field = mirror_field(decks)

    if args.start:
        raw = json.loads(Path(args.start).read_text())
        start = Model(weights=tuple(raw["weights"]), bias=raw.get("bias", 0.0))
    else:
        start = load_model()

    best = start
    rng = random.Random(args.seed)
    seed = args.seed
    started = time.perf_counter()
    accepted: list[str] = []

    for sweep in range(args.sweeps):
        order = list(range(len(FEATURE_NAMES)))
        rng.shuffle(order)               # no feature gets a permanent head start
        print(f"\n=== sweep {sweep + 1}/{args.sweeps} ===")
        for index in order:
            name = FEATURE_NAMES[index]
            for factor in (1 + args.step, 1 - args.step):
                trial = nudged(best, index, factor)
                seed += args.screen + 2
                score = match(trial, best, field, db, args.screen, seed)
                mark = ""
                if score >= args.accept:
                    best = trial
                    change = f"{name} x{factor:.1f}"
                    accepted.append(change)
                    mark = "  <-- kept"
                print(f"  {name:20} x{factor:.1f}  {score:.3f}{mark}"
                      f"   [{(time.perf_counter() - started) / 60:.1f} min]")
                if mark:
                    break                # move on; re-test this weight next sweep

    print(f"\n{len(accepted)} change(s) survived the screen: "
          + (", ".join(accepted) if accepted else "none"))
    if not accepted:
        print("nothing to confirm.")
        return 1

    print("\nfinal weights:")
    for name, before, after in zip(FEATURE_NAMES, start.weights, best.weights):
        flag = "   <-- moved" if abs(after - before) > 1e-9 else ""
        print(f"  {name:20} {before:+.3f} -> {after:+.3f}{flag}")

    CANDIDATE_PATH.write_text(
        json.dumps({"weights": list(best.weights), "bias": best.bias,
                    "fitted": True, "source": "climb"}, indent=2) + "\n"
    )
    print(f"\ncandidate written to {CANDIDATE_PATH.name} (nothing installed)")

    if args.confirm:
        print(f"\nconfirming against the starting weights on fresh seeds, "
              f"SPRT up to {args.confirm} games…")
        test, record = confirm(best, start, field, db, args.confirm,
                               args.seed + 4_000_000, args.elo0, args.elo1)
        low, high = elo_interval(test.score, max(1, test.games))
        wins, losses, draws = record
        print(f"  {test.summary()}")
        print(f"  climbed {wins}-{losses}-{draws}, "
              f"{elo_from_score(test.score):+.0f} Elo [{low:+.0f}, {high:+.0f}]")
        if test.verdict() == "accept":
            print("\n  CONFIRMED — stronger on seeds the screen never saw.")
            return 0
        print(f"\n  NOT confirmed ({test.verdict()}): the screen's accepts did "
              "not hold up. Nothing installed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
