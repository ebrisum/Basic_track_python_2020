"""The large-scale simulator validation run.

    .venv/bin/python -m analysis.validate --games 10000 --check-every 1 --deep

`TCG_AI_BUILD.md` section 10 asks for at least 10,000 automated matches
verified against four failure modes, and section 39 makes passing that run an
acceptance criterion. This is that run, and it reports exactly those four
things plus the section 11 performance instrumentation.

The four failure modes, and how each is detected:

* **crashes** -- any exception escaping a game.
* **impossible states** -- `engine.invariants.check`. `--check-every` sets the
  sampling stride and `--deep` checks after every action of the sampled games
  rather than only at the end. `--check-every 1 --deep` checks everything and
  costs 8%, measured: 200 paired games, 42,394 decisions either way, 107
  games/min shallow against 98 full-depth. An earlier version of this
  docstring guessed "~3x slower" and the guess was wrong by a factor of 30,
  which is why the flags default to sampling and the acceptance run no longer
  does. The end-of-game check alone is what caught a stranded facedown card in
  a 299-step game.
* **unresolved games** -- a game that hits the action cap without terminating.
* **illegal actions** -- an empty legal-action set in a non-terminal state, or
  an agent returning an action outside `legal_actions()`. This must stay 0
  (section 29).

Agents alternate seats by game so neither policy is measured only on the
play. Seeds are the game index, so the whole run is reproducible.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.invariants import check
from engine.setup import build_state, load_deck
from engine.versions import provenance

ACTION_CAP = 3000


@dataclass(frozen=True)
class _Job:
    seed: int
    checking: bool
    deep: bool
    decks: tuple[str, str]


@dataclass
class _Outcome:
    decisions: int = 0
    branch: int = 0
    turns: int = 0
    crashes: int = 0
    impossible: int = 0
    unresolved: int = 0
    illegal: int = 0
    problem: list[str] = field(default_factory=list)
    traceback: str = ""


# One database per process. `cards.database.load` re-parses the card JSON on
# every call and a worker plays hundreds of games, so loading it per game would
# cost more than the parallelism saves.
_DB = None
_DECKS: dict[tuple[str, str], tuple] = {}


def _fixtures(decks: tuple[str, str]):
    global _DB
    if _DB is None:
        _DB = load_db()
    if decks not in _DECKS:
        _DECKS[decks] = (load_deck(decks[0]), load_deck(decks[1]))
    return _DB, _DECKS[decks]


def _run_one(job: _Job) -> _Outcome:
    """Play one game and report what happened. Runs in a worker process."""
    out = _Outcome()
    db, (d0, d1) = _fixtures(job.decks)
    try:
        state = build_state(d0, d1, seed=job.seed, db=db)
        # Alternate seats so neither policy is measured only on the play.
        agents = ([RandomAgent(job.seed), GreedyAgent(job.seed + 1)]
                  if job.seed % 2
                  else [GreedyAgent(job.seed), RandomAgent(job.seed + 1)])
        steps = 0
        while not state.is_terminal() and steps < ACTION_CAP:
            legal = state.legal_actions()
            if not legal:
                out.illegal += 1
                break
            action = agents[state.current_player].act(state)
            if action not in legal:
                out.illegal += 1
                break
            out.branch += len(legal)
            out.decisions += 1
            state.apply(action)
            steps += 1
            if job.checking and job.deep:
                problems = check(state)
                if problems:
                    out.impossible += 1
                    out.problem = out.problem or problems
                    break
        if steps >= ACTION_CAP and not state.is_terminal():
            out.unresolved += 1
        out.turns += state.turn_number
        if job.checking and not job.deep:
            problems = check(state)
            if problems:
                out.impossible += 1
                out.problem = out.problem or problems
    except Exception:                                      # noqa: BLE001
        out.crashes += 1
        out.traceback = traceback.format_exc()
    return out


def _consume(results, args, started):
    """Drain the results, printing the progress line as they arrive."""
    collected = []
    for index, outcome in enumerate(results):
        collected.append(outcome)
        if args.progress and (index + 1) % args.progress == 0:
            elapsed = time.time() - started
            print(f"  {index + 1}/{args.games}  "
                  f"{(index + 1) / elapsed * 60:.0f} games/min", flush=True)
    return collected


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
    parser.add_argument("--workers", type=int, default=1,
                        help="run games in N processes (BUILD.md section 13). "
                             "Results are identical: a game depends only on "
                             "its seed and every counter is a sum")
    parser.add_argument("--metrics", default=None,
                        help="write the run's numbers and provenance to a "
                             "JSON file (BUILD.md section 29)")
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

    work = [
        _Job(seed=index, checking=index % args.check_every == 0,
             deep=bool(args.deep), decks=(args.decks[0], args.decks[1]))
        for index in range(args.games)
    ]

    if args.workers > 1:
        # BUILD.md 13. Safe to parallelise for one reason: a game's outcome
        # depends only on its seed, so the games are independent and every
        # counter below is a sum, which does not care what order it is summed
        # in. `imap` keeps results in submission order anyway, so the progress
        # line still counts up.
        import multiprocessing

        # "spawn" rather than the platform default. Forking a process that has
        # threads is deprecated in 3.12 and unsafe in general, and pytest's
        # runner has them -- the warning fires on every parallel test. Spawn
        # costs each worker a fresh import and one card-database parse, which
        # is a second of startup against runs measured in minutes.
        context = multiprocessing.get_context("spawn")
        with context.Pool(args.workers) as pool:
            results = pool.imap(_run_one, work, chunksize=8)
            outcomes = _consume(results, args, started)
    else:
        outcomes = _consume((_run_one(job) for job in work), args, started)

    for outcome in outcomes:
        crashes += outcome.crashes
        impossible += outcome.impossible
        unresolved += outcome.unresolved
        illegal += outcome.illegal
        decisions += outcome.decisions
        branch += outcome.branch
        turns += outcome.turns
        if outcome.problem and not first_problem:
            first_problem = outcome.problem
        if outcome.traceback and crashes <= 3:
            print(outcome.traceback)

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

    if args.metrics:
        # BUILD.md 29 -- a run whose numbers exist only in a terminal cannot be
        # compared to the next run. The stamp goes in the same file as the
        # metrics on purpose: separating them is how a number outlives the
        # engine that produced it.
        record = {
            "provenance": stamp,
            "decks": list(args.decks),
            "settings": {
                "games": args.games,
                "check_every": args.check_every,
                "deep": bool(args.deep),
                "action_cap": ACTION_CAP,
            },
            "metrics": {
                "games": args.games,
                "decisions": decisions,
                "wall_seconds": round(elapsed, 1),
                "games_per_minute": round(args.games / elapsed * 60, 1),
                "decisions_per_second": round(decisions / elapsed, 1),
                "mean_branching_factor": round(branch / max(decisions, 1), 2),
                "mean_turns_per_game": round(turns / args.games, 2),
                "states_checked_games": checked,
            },
            "failures": {
                "crashes": crashes,
                "impossible": impossible,
                "unresolved": unresolved,
                "illegal": illegal,
                "first_problem": first_problem[:5],
            },
        }
        path = Path(args.metrics)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print()
        print(f"metrics written to {path}")

    return 1 if (crashes or impossible or unresolved or illegal) else 0


if __name__ == "__main__":
    raise SystemExit(main())
