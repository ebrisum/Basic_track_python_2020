"""Head-to-head agent benchmark -- does the scoring actually win games?

    .venv/bin/python analysis/benchmark.py --games 60

Calibration says whether the heuristic *predicts* winning. This says whether
using it *causes* winning, which is the only question that matters. A model can
be well calibrated and still useless to play by.

Seats are swapped every other game so a first-player advantage cannot be
mistaken for agent strength, and both agents in a pairing get the same game
seed, so they face the same shuffles.

Reported with a normal-approximation 95% interval, because a 55% win rate over
40 games is not evidence of anything.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from agents.ismcts import ISMCTSAgent  # noqa: E402
from agents.random_agent import RandomAgent  # noqa: E402
from analysis.evaluation import Model, load_model  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402

MAX_STEPS = 40_000


def mirror_field(decks):
    """Each deck against itself.

    Measuring one agent against another across *different* decks measures the
    decks far more loudly than the agents: with identical agents on both
    sides, volibear_body_fury beats jinx_chaos_fury about 85-15. That deck
    edge swamps the 0.52-0.55 an agent improvement is worth, so a cross-deck
    test needs enormous samples to see anything.

    In a mirror both sides play the same deck against the same deck, so the
    deck cancels exactly and what is left is how the two agents play. It is
    the standard trick for the same reason engine testing fixes the opening
    book: remove the variance you are not trying to measure.
    """
    return [(deck, deck) for deck in decks]


def duel(make_a, make_b, games: int, base_seed: int, decks, db,
         start_index: int = 0, paired: bool = False) -> tuple[float, int, int, int]:
    """Play `games`, alternating seats. Returns (score_a, wins, losses, draws).

    `paired` plays each seed twice with the sides swapped, so deck and
    shuffle luck cancel within the pair. Use it whenever the games are
    consumed as one measurement -- an SPSA gradient step, say -- rather than
    accumulated independently.

    `start_index` offsets the seat rotation. A sequential test calls this one
    game at a time, and with the rotation keyed only on the loop index every
    such call would seat agent A first -- turning any first-player advantage
    into apparent agent strength. Callers that drive the games themselves pass
    their own index here.
    """
    d0, d1 = decks
    wins = losses = draws = 0
    for i in range(games):
        if paired:
            # Consecutive games share a seed and swap sides, so the pair is
            # the *same* game played by different hands: identical decks,
            # identical shuffles, identical opening hands. Deck and shuffle
            # luck are common to the pair and cancel, leaving the difference
            # in play. Without it the two games are unrelated and their luck
            # simply adds noise.
            seed = base_seed + i // 2
            a_seat = i % 2
        else:
            seed = base_seed + i
            a_seat = (start_index + i) % 2   # swap seats every other game
        state = build_state(d0, d1, seed=seed, db=db, validate_decks=False)
        agents = [None, None]
        agents[a_seat] = make_a(seed)
        agents[1 - a_seat] = make_b(seed + 5000)
        steps = 0
        while not state.is_terminal():
            if steps >= MAX_STEPS:
                break
            state.apply(agents[state.current_player].act(state))
            steps += 1
        if not state.is_terminal():
            continue
        result = state.returns()[a_seat]
        if result > 0.5:
            wins += 1
        elif result < 0.5:
            losses += 1
        else:
            draws += 1
    played = wins + losses + draws
    score = (wins + 0.5 * draws) / played if played else float("nan")
    return score, wins, losses, draws


def interval(score: float, n: int) -> tuple[float, float]:
    """Normal-approximation 95% interval on the win rate."""
    if n == 0:
        return (float("nan"), float("nan"))
    half = 1.96 * math.sqrt(max(score * (1 - score), 1e-9) / n)
    return (max(0.0, score - half), min(1.0, score + half))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--seed", type=int, default=500)
    ap.add_argument("--deck0", default="jinx_chaos_fury")
    ap.add_argument("--deck1", default="volibear_body_fury")
    ap.add_argument("--ismcts", type=int, default=0,
                    help="also benchmark ISMCTS with this many iterations")
    args = ap.parse_args(argv)

    db = load_db()
    decks = (load_deck(args.deck0), load_deck(args.deck1))
    prior = Model()                 # hand-set weights
    fitted = load_model()           # whatever is in weights.json

    pairings = [
        ("random          vs random", lambda s: RandomAgent(s), lambda s: RandomAgent(s)),
        ("greedy(prior)   vs random",
         lambda s: GreedyAgent(s, prior), lambda s: RandomAgent(s)),
    ]
    if fitted.fitted:
        pairings.append(("greedy(fitted)  vs random",
                         lambda s: GreedyAgent(s, fitted), lambda s: RandomAgent(s)))
        pairings.append(("greedy(fitted)  vs greedy(prior)",
                         lambda s: GreedyAgent(s, fitted), lambda s: GreedyAgent(s, prior)))

    if args.ismcts:
        pairings = [
            (f"ismcts({args.ismcts})   vs random",
             lambda s: ISMCTSAgent(s, iterations=args.ismcts, model=prior),
             lambda s: RandomAgent(s)),
            (f"ismcts({args.ismcts})   vs greedy",
             lambda s: ISMCTSAgent(s, iterations=args.ismcts, model=prior),
             lambda s: GreedyAgent(s, prior)),
        ]

    print(f"{args.games} games per pairing, seats swapped, shared seeds\n")
    print("| pairing | score | W-L-D | 95% interval |")
    print("| --- | --- | --- | --- |")
    for label, make_a, make_b in pairings:
        score, w, l, d = duel(make_a, make_b, args.games, args.seed, decks, db)
        lo, hi = interval(score, w + l + d)
        print(f"| {label} | {score:.3f} | {w}-{l}-{d} | {lo:.3f}–{hi:.3f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
