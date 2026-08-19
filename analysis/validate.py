"""The large-scale simulator validation run.

    .venv/bin/python -m analysis.validate --games 10000

`TCG_AI_BUILD.md` section 10 asks for at least 10,000 automated matches
verified against four failure modes, and section 39 makes passing that run an
acceptance criterion. This is that run, and it reports exactly those four
things plus the section 11 performance instrumentation.

The four failure modes, and how each is detected:

* **crashes** -- any exception escaping a game.
* **impossible states** -- `engine.invariants.check` over a sampled subset of
  games. Checking every action of every game is ~3x slower; `--check-every`
  sets the sampling stride and `--deep` checks after every action of the
  sampled games rather than only at the end. The end-of-game check alone is
  what caught a stranded facedown card in a 299-step game.
* **unresolved games** -- a game that hits the action cap without terminating.
* **illegal actions** -- an empty legal-action set in a non-terminal state, or
  an agent returning an action outside `legal_actions()`. This must stay 0
  (section 29).

Agents alternate seats by game so neither policy is measured only on the
play. Seeds are the game index, so the whole run is reproducible.
"""

from __future__ import annotations

import argparse
import time
import traceback

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.invariants import check
from engine.setup import build_state, load_deck
from engine.versions import provenance

ACTION_CAP = 3000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=10000)
    parser.add_argument("--check-every", type=int, default=20,
                        help="check invariants on 1 game in N")
    parser.add_argument("--deep", action="store_true",
                        help="on checked games, check after every action")
    parser.add_argument("--decks", nargs=2,
                        default=["jinx_chaos_fury", "volibear_body_fury"])
    parser.add_argument("--progress", type=int, default=1000)
    args = parser.parse_args(argv)

    db = load_db()
    d0, d1 = load_deck(args.decks[0]), load_deck(args.decks[1])

    # BUILD.md 37 -- a run's numbers mean nothing without knowing what
    # produced them. Printed first so it lands in any captured log.
    stamp = provenance(db)
    print("provenance")
    for key in sorted(stamp):
        print(f"  {key:28s} {stamp[key]}")
    print(f"  decks                        {args.decks[0]} vs {args.decks[1]}")
    print(flush=True)

    crashes = impossible = unresolved = illegal = 0
    decisions = branch = turns = 0
    first_problem: list[str] = []
    started = time.time()

    for index in range(args.games):
        seed = index
        checking = index % args.check_every == 0
        try:
            state = build_state(d0, d1, seed=seed, db=db)
            # Alternate seats so neither policy is measured only on the play.
            agents = ([RandomAgent(seed), GreedyAgent(seed + 1)] if index % 2
                      else [GreedyAgent(seed), RandomAgent(seed + 1)])
            steps = 0
            while not state.is_terminal() and steps < ACTION_CAP:
                legal = state.legal_actions()
                if not legal:
                    illegal += 1
                    break
                action = agents[state.current_player].act(state)
                if action not in legal:
                    illegal += 1
                    break
                branch += len(legal)
                decisions += 1
                state.apply(action)
                steps += 1
                if checking and args.deep:
                    problems = check(state)
                    if problems:
                        impossible += 1
                        first_problem = first_problem or problems
                        break
            if steps >= ACTION_CAP and not state.is_terminal():
                unresolved += 1
            turns += state.turn_number
            if checking and not args.deep:
                problems = check(state)
                if problems:
                    impossible += 1
                    first_problem = first_problem or problems
        except Exception:                                  # noqa: BLE001
            crashes += 1
            if crashes <= 3:
                traceback.print_exc()
        if args.progress and (index + 1) % args.progress == 0:
            elapsed = time.time() - started
            print(f"  {index + 1}/{args.games}  "
                  f"{(index + 1) / elapsed * 60:.0f} games/min", flush=True)

    elapsed = time.time() - started
    checked = args.games // args.check_every
    print()
    print(f"games            {args.games}")
    print(f"wall             {elapsed:.1f}s  ->  "
          f"{args.games / elapsed * 60:.0f} games/min")
    print(f"decisions        {decisions}  ->  {decisions / elapsed:.0f}/s")
    print(f"avg legal/dec    {branch / max(decisions, 1):.1f}")
    print(f"avg turns/game   {turns / args.games:.1f}")
    print()
    print(f"crashes          {crashes}")
    print(f"impossible       {impossible}   "
          f"(invariants on {checked} games"
          f"{', every action' if args.deep else ', final state'})")
    print(f"unresolved       {unresolved}   (cap {ACTION_CAP} actions)")
    print(f"illegal          {illegal}")
    if first_problem:
        print()
        print("first problem:")
        for line in first_problem[:5]:
            print(f"  {line}")
    return 1 if (crashes or impossible or unresolved or illegal) else 0


if __name__ == "__main__":
    raise SystemExit(main())
