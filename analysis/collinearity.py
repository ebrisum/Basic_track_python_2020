"""Are the evaluation's features actually independent?

    .venv/bin/python analysis/collinearity.py --games 8

Two features that move together are one feature and a wasted parameter. Worse,
they create a **flat direction** in the weight landscape: any increase in one
can be traded for a decrease in the other with almost no effect on play. A
learner in a flat direction wanders -- the gradient is noise, every step is as
good as every other, and the weights it lands on are not interpretable.

That is not a hypothetical here. Measured over 348 sampled positions:

    hand_diff       answer_risk         r = +0.986
    point_diff      point_progress      r = +0.953
    unit_count_diff might_diff          r = +0.921
    battlefield_diff victory_pressure   r = +0.898

Thirteen features, perhaps six independent directions. It explains three
things that were otherwise puzzling:

* SPSA from the hand-set prior found nothing in two separate runs -- much of
  the gradient it was sampling lay along directions that do not change play.
* A run started from deliberately damaged weights **never repaired the
  damage**: it left `point_diff` at -0.924 and compensated by boosting
  `rune_diff` 9x and `hand_diff` 5x. With correlated features available, the
  search routes around a broken one instead of fixing it.
* Learned weights are hard to read. A weight is only interpretable as "how
  much this feature matters" when the feature is not standing in for four
  others.

The fix is to decorrelate before tuning harder: drop a duplicate, or replace
it with the part of it the others do not already explain.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from analysis.evaluation import FEATURE_NAMES, features, load_model  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import available_decks, build_state, load_deck  # noqa: E402


def sample(games: int, stride: int, seed: int, db, decks) -> list[list[float]]:
    model = load_model()
    rows: list[list[float]] = []
    for index in range(games):
        d0 = decks[index % len(decks)]
        d1 = decks[(index + 1) % len(decks)]
        state = build_state(d0, d1, seed=seed + index, db=db, validate_decks=False)
        agents = (GreedyAgent(seed + index, model), GreedyAgent(seed + index + 1, model))
        steps = 0
        while not state.is_terminal() and steps < 600:
            if steps % stride == 0:
                values = features(state, 0)
                rows.append([values[name] for name in FEATURE_NAMES])
            state.apply(agents[state.current_player].act(state))
            steps += 1
    return rows


def correlation(rows, i: int, j: int) -> float:
    xs = [r[i] for r in rows]
    ys = [r[j] for r in rows]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = (
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    ) ** 0.5
    return numerator / denominator if denominator else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=808_000)
    args = ap.parse_args(argv)

    db = load_db()
    decks = [load_deck(slug) for slug in available_decks()]
    rows = sample(args.games, args.stride, args.seed, db, decks)
    if len(rows) < 30:
        print("not enough sampled positions")
        return 1

    pairs = []
    for i in range(len(FEATURE_NAMES)):
        for j in range(i + 1, len(FEATURE_NAMES)):
            r = correlation(rows, i, j)
            if abs(r) >= args.threshold:
                pairs.append((abs(r), r, FEATURE_NAMES[i], FEATURE_NAMES[j]))
    pairs.sort(reverse=True)

    print(f"{len(rows)} sampled positions, {len(FEATURE_NAMES)} features\n")
    print(f"| feature | feature | r |")
    print("| --- | --- | --- |")
    for _, r, a, b in pairs:
        print(f"| {a} | {b} | {r:+.3f} |")
    print(f"\n{len(pairs)} pair(s) at |r| >= {args.threshold}.")
    if pairs and pairs[0][0] > 0.95:
        print(f"\n`{pairs[0][2]}` and `{pairs[0][3]}` are effectively the same "
              f"feature (r = {pairs[0][1]:+.3f}) -- one of them is a wasted "
              f"parameter and a flat direction for the learner to wander in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
