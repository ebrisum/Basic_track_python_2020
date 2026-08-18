"""Rate a model against the frozen gauntlet -- the "did it improve" number.

    .venv/bin/python analysis/rate.py                    # rate the installed model
    .venv/bin/python analysis/rate.py --generation 3     # rate one league member
    .venv/bin/python analysis/rate.py --table            # rate every generation

Every generation plays the *same* fixed opponents, so the ratings are
comparable down the whole column. That is the property a "beat the previous
generation" gate does not have: strength is not transitive, so a chain of
wins over immediate predecessors can still end up weaker than where it began.

The random agent anchors the scale at a fixed 0, because it is the one
opponent that cannot drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from agents.random_agent import RandomAgent  # noqa: E402
from analysis.benchmark import duel  # noqa: E402
from analysis.evaluation import Model, load_model  # noqa: E402
from analysis.ladder import League, elo_from_score, elo_interval  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import load_deck  # noqa: E402


def make(model: Model | None):
    """An agent factory for a gauntlet entry (None means the random anchor)."""
    if model is None:
        return lambda seed: RandomAgent(seed)
    return lambda seed: GreedyAgent(seed, model)


def rate(model: Model, league: League, games: int, base_seed: int, decks, db,
         label: str = "model") -> dict:
    """Play `model` against every gauntlet entry; return per-opponent results."""
    rows = []
    for name, opponent in league.gauntlet():
        score, wins, losses, draws = duel(
            make(model), make(opponent), games, base_seed, decks, db
        )
        low, high = elo_interval(score, games)
        rows.append({
            "opponent": name,
            "score": score,
            "record": [wins, losses, draws],
            "elo": elo_from_score(score),
            "interval": [low, high],
        })
    return {"label": label, "rows": rows}


def render(result: dict) -> str:
    lines = [
        f"### {result['label']}",
        "",
        "| opponent | score | W-L-D | Elo | 95% interval |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        wins, losses, draws = row["record"]
        lines.append(
            f"| {row['opponent']} | {row['score']:.3f} | {wins}-{losses}-{draws} "
            f"| {row['elo']:+.0f} | {row['interval'][0]:+.0f} to {row['interval'][1]:+.0f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=60, help="games per opponent")
    ap.add_argument("--seed", type=int, default=90_000)
    ap.add_argument("--generation", type=int, default=None,
                    help="rate a league member instead of the installed model")
    ap.add_argument("--table", action="store_true",
                    help="rate every generation in the league")
    ap.add_argument("--deck0", default="jinx_chaos_fury")
    ap.add_argument("--deck1", default="volibear_body_fury")
    args = ap.parse_args(argv)

    db = load_db()
    decks = (load_deck(args.deck0), load_deck(args.deck1))
    league = League()

    if not league.generations():
        print("league is empty -- nothing to rate against but the random anchor.")
        print("run analysis/self_play_loop.py first, or seed it with:")
        print("    .venv/bin/python -c \"from analysis.ladder import League; "
              "from analysis.evaluation import load_model; "
              "League().add(0, load_model())\"")

    targets = []
    if args.table:
        targets = [(f"gen{g}", league.get(g)) for g in league.generations()]
    elif args.generation is not None:
        targets = [(f"gen{args.generation}", league.get(args.generation))]
    else:
        targets = [("installed model", load_model())]

    for label, model in targets:
        print(render(rate(model, league, args.games, args.seed, decks, db, label)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
