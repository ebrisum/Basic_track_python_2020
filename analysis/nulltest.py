"""Null test: the identical model against itself must score 0.500.

    .venv/bin/python analysis/nulltest.py --games 400

Run this whenever the benchmark harness changes. If a model cannot draw with
itself, every other number this project reports is confounded by whatever
asymmetry is responsible, and no amount of careful Elo arithmetic downstream
will fix it.

`duel` swaps seats every other game and gives both agents the same shuffles,
which handles the obvious asymmetries. The subtle one it does not obviously
handle: agent A's tie-breaking RNG is seeded from the game seed while B's is
offset, so A's randomness is correlated with the shuffle. That is a reason to
measure rather than to assume.

Last run: 0.480 (192-208-0) over 400 games, 95% interval 0.431-0.529.
The interval contains 0.500, so the harness is unbiased at this resolution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from analysis.benchmark import duel, interval  # noqa: E402
from analysis.evaluation import load_model  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import available_decks, load_deck  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    db = load_db()
    slugs = available_decks()[:2]
    decks = (load_deck(slugs[0]), load_deck(slugs[1]))
    model = load_model()

    score, wins, losses, draws = duel(
        lambda s: GreedyAgent(s, model),
        lambda s: GreedyAgent(s, model),
        args.games, args.seed, decks, db,
    )
    low, high = interval(score, wins + losses + draws)
    print(f"identical model vs itself: {score:.3f} ({wins}-{losses}-{draws}) "
          f"over {args.games} games")
    print(f"95% interval {low:.3f}-{high:.3f}")
    if low > 0.5 or high < 0.5:
        print("BIASED — the interval excludes 0.500. Fix this before trusting "
              "any other measurement.")
        return 1
    print("unbiased (the interval contains 0.500)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
