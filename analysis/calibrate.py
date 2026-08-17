"""Measure whether the evaluation heuristic predicts winning.

    .venv/bin/python analysis/calibrate.py --games 200

A shaped score is worth exactly as much as its correlation with the real
outcome. This plays self-play games, records `evaluate()` at every position
along with who eventually won, and reports:

  * **Brier score** -- mean squared error of the predicted win probability.
    0.25 is what you get by always saying 0.5, so anything at or above 0.25 is
    worthless. Lower is better.
  * **Accuracy** -- how often the favoured player actually won.
  * **Calibration table** -- of the positions the model called 70%, how many
    were won? A model can be accurate and still badly calibrated.

Broken out by game stage, because a heuristic that is only right once the game
is already decided has told you nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.random_agent import RandomAgent  # noqa: E402
from analysis.evaluation import Model, evaluate, load_model  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402

MAX_STEPS = 40_000


def collect(games: int, base_seed: int, model: Model, deck_names: tuple[str, str],
            stride: int = 3) -> list[tuple[float, int, float]]:
    """Play games and return (predicted_p0_win, actual_p0_win, progress) rows."""
    db = load_db()
    d0, d1 = load_deck(deck_names[0]), load_deck(deck_names[1])
    rows: list[tuple[float, int, float]] = []

    for i in range(games):
        seed = base_seed + i
        state = build_state(d0, d1, seed=seed, db=db, validate_decks=False)
        agents = (RandomAgent(seed), RandomAgent(seed + 7919))
        trace: list[float] = []
        steps = 0
        while not state.is_terminal():
            if steps >= MAX_STEPS:
                break
            if steps % stride == 0:
                trace.append(evaluate(state, 0, model))
            state.apply(agents[state.current_player].act(state))
            steps += 1
        if not state.is_terminal():
            continue
        outcome = state.returns()[0]
        for index, prediction in enumerate(trace):
            progress = index / max(1, len(trace) - 1)
            rows.append((prediction, outcome, progress))
    return rows


def report(rows: list[tuple[float, int, float]]) -> str:
    if not rows:
        return "no data"
    lines: list[str] = []

    def stats(subset):
        brier = sum((p - o) ** 2 for p, o, _ in subset) / len(subset)
        decided = [(p, o) for p, o, _ in subset if o != 0.5]
        acc = (
            sum(1 for p, o in decided if (p > 0.5) == (o > 0.5)) / len(decided)
            if decided else float("nan")
        )
        return brier, acc, len(subset)

    brier, acc, n = stats(rows)
    lines += [
        f"positions scored : {n}",
        f"Brier score      : {brier:.4f}   (0.2500 = always guessing 0.5; lower is better)",
        f"accuracy         : {acc:.3f}",
        "",
        "By game stage:",
        "| stage | positions | Brier | accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for label, lo, hi in (("early", 0.0, 0.33), ("mid", 0.33, 0.66), ("late", 0.66, 1.01)):
        subset = [r for r in rows if lo <= r[2] < hi]
        if not subset:
            continue
        b, a, count = stats(subset)
        lines.append(f"| {label} | {count} | {b:.4f} | {a:.3f} |")

    lines += ["", "Calibration (predicted vs actual win rate):",
              "| predicted | positions | actual |", "| --- | --- | --- |"]
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for prediction, outcome, _ in rows:
        buckets[min(9, int(prediction * 10))].append((prediction, outcome))
    for bucket in sorted(buckets):
        items = buckets[bucket]
        actual = sum(o for _, o in items) / len(items)
        lines.append(f"| {bucket/10:.1f}–{bucket/10+0.1:.1f} | {len(items)} | {actual:.3f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--deck0", default="jinx_chaos_fury")
    ap.add_argument("--deck1", default="volibear_body_fury")
    args = ap.parse_args(argv)

    model = load_model()
    print(f"model: {'fitted' if model.fitted else 'hand-set prior'}"
          f"{f' on {model.trained_on_games} games' if model.fitted else ''}\n")
    rows = collect(args.games, args.seed, model, (args.deck0, args.deck1))
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
