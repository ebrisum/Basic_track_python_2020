"""Does the model pilot *this deck* against *that deck* better than before?

    .venv/bin/python analysis/matchup.py --deck jinx_chaos_fury \
        --against volibear_body_fury --games 200

This is the question the project actually exists to answer. "Is agent A
stronger than agent B" is a means to it; what a deckbuilder wants to know is
whether the model has learned to *play their deck* better in a matchup they
care about.

## Why the answer needs pairing

The decks are not balanced -- with identical agents on both sides,
volibear_body_fury beats jinx_chaos_fury about 85-15. So a raw win rate for
"candidate piloting jinx" is mostly a statement about jinx, not about the
candidate, and comparing two agents' raw rates buries a 0.02 skill difference
under a 0.85 deck difference plus shuffle luck.

The fix is the one engine testing has always used: play the same position
from both sides. Every seed here is played **twice** --

    game 2k:      candidate pilots X,  reference pilots Y
    game 2k + 1:  reference pilots X,  candidate pilots Y

-- with the *same* seed, so both games have identical decks, identical
shuffles and identical opening hands. The deck edge and the shuffle luck are
common to the pair and cancel; what is left between the two results is which
agent played the position better.

The report is therefore per *piloted deck*: "candidate scores 0.23 piloting
jinx against volibear, where the reference scores 0.15" is a real, readable
improvement at piloting jinx, even though jinx loses the matchup either way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.greedy_agent import GreedyAgent  # noqa: E402
from agents.ismcts import ISMCTSAgent  # noqa: E402
from agents.random_agent import RandomAgent  # noqa: E402
from analysis.benchmark import MAX_STEPS, interval  # noqa: E402
from analysis.evaluation import Model, load_model  # noqa: E402
from analysis.ladder import elo_from_score  # noqa: E402
from cards.database import load as load_db  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402


def play(make_seat0, make_seat1, deck0, deck1, seed, db) -> float | None:
    """One game. Returns seat 0's result, or None if it never finished."""
    state = build_state(deck0, deck1, seed=seed, db=db, validate_decks=False)
    agents = (make_seat0(seed), make_seat1(seed + 5000))
    steps = 0
    while not state.is_terminal():
        if steps >= MAX_STEPS:
            return None
        state.apply(agents[state.current_player].act(state))
        steps += 1
    return state.returns()[0]


def piloting(make_candidate, make_reference, piloted, opposing, games, base_seed, db):
    """How each agent does piloting `piloted` against `opposing`.

    Every seed is played twice with the pilots swapped, so the two results
    describe the *same* game played by different hands.
    """
    rows = {"candidate": [0, 0, 0], "reference": [0, 0, 0]}   # W, L, D

    def record(who, result):
        bucket = rows[who]
        bucket[0 if result > 0.5 else 1 if result < 0.5 else 2] += 1

    for k in range(games):
        seed = base_seed + k
        # Candidate pilots `piloted` (seat 0); reference pilots `opposing`.
        first = play(make_candidate, make_reference, piloted, opposing, seed, db)
        # Same seed, pilots swapped: reference now pilots `piloted`.
        second = play(make_reference, make_candidate, piloted, opposing, seed, db)
        if first is not None:
            record("candidate", first)
        if second is not None:
            record("reference", second)
    return rows


def summarise(name, record) -> str:
    wins, losses, draws = record
    played = wins + losses + draws
    if not played:
        return f"| {name} | — | 0-0-0 | — | — |"
    score = (wins + 0.5 * draws) / played
    low, high = interval(score, played)
    return (f"| {name} | {score:.3f} | {wins}-{losses}-{draws} "
            f"| {elo_from_score(score):+.0f} | {low:.3f}–{high:.3f} |")


def make_agent(kind: str, model: Model, iterations: int):
    if kind == "random":
        return lambda seed: RandomAgent(seed)
    if kind == "ismcts":
        return lambda seed: ISMCTSAgent(seed, iterations=iterations, model=model)
    return lambda seed: GreedyAgent(seed, model)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deck", required=True, help="the deck being piloted")
    ap.add_argument("--against", required=True, help="the opposing deck")
    ap.add_argument("--games", type=int, default=150,
                    help="seed pairs; each is played twice, pilots swapped")
    ap.add_argument("--seed", type=int, default=610_000)
    ap.add_argument("--candidate", default=None,
                    help="weights JSON for the candidate (default: installed)")
    ap.add_argument("--reference", default=None,
                    help="weights JSON for the reference (default: hand-set prior)")
    ap.add_argument("--agent", default="greedy",
                    choices=("greedy", "ismcts", "random"))
    ap.add_argument("--iterations", type=int, default=60)
    args = ap.parse_args(argv)

    db = load_db()
    piloted, opposing = load_deck(args.deck), load_deck(args.against)

    def read(path, default):
        if path is None:
            return default
        raw = json.loads(Path(path).read_text())
        return Model(weights=tuple(raw["weights"]), bias=raw.get("bias", 0.0),
                     fitted=True)

    candidate = read(args.candidate, load_model())
    reference = read(args.reference, Model())     # hand-set prior

    rows = piloting(
        make_agent(args.agent, candidate, args.iterations),
        make_agent(args.agent, reference, args.iterations),
        piloted, opposing, args.games, args.seed, db,
    )

    print(f"piloting **{args.deck}** against **{args.against}**")
    print(f"{args.games} seed pairs, each played twice with the pilots swapped "
          f"(same shuffles both ways)\n")
    print("| pilot | score | W-L-D | Elo | 95% interval |")
    print("| --- | --- | --- | --- | --- |")
    print(summarise("candidate", rows["candidate"]))
    print(summarise("reference", rows["reference"]))

    cw, cl, cd = rows["candidate"]
    rw, rl, rd = rows["reference"]
    cn, rn = cw + cl + cd, rw + rl + rd
    if cn and rn:
        delta = (cw + 0.5 * cd) / cn - (rw + 0.5 * rd) / rn
        print(f"\ncandidate pilots this deck **{delta:+.3f}** better "
              f"({elo_from_score(0.5 + delta / 2) * 2:+.0f} Elo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
