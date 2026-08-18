"""Repair a recorded replay after a *justified* engine change.

    .venv/bin/python tests/replays/repair.py --dry-run
    .venv/bin/python tests/replays/repair.py --apply

Regenerating a replay to make it pass destroys the only signal the harness
produces. But there is a narrower case that is not that: the engine changed
for a reason, the recorded actions are all still legal, the game still ends
the same way, and only some stored hashes no longer match.

This tool handles exactly that case and refuses everything else. It replays
the recorded actions, and:

* if any action is no longer legal, it stops and says so -- the recorded game
  is not a legal game any more, which is a real regression to investigate;
* if the final returns changed, it stops -- the outcome moved, which is not a
  representation change;
* otherwise it reports which steps' hashes moved and, with `--apply`, updates
  only those.

The alternative -- regenerating from the seed -- produces a *different* game.
That is how a 352-step recorded game became a 290-step one when the rune-pool
fix (167) changed which actions were legal, discarding the longer fixture for
nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cards.database import load as load_db  # noqa: E402
from engine.replay import Replay, state_hash  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402

HERE = Path(__file__).resolve().parent


def inspect(path: Path, db):
    raw = json.loads(path.read_text())
    replay = Replay.from_dict(raw)
    if replay.game != "riftbound":
        return None, "not a riftbound replay"

    d0, d1 = load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")
    state = build_state(d0, d1, seed=replay.seed, db=db, validate_decks=False)

    moved: list[tuple[int, str, str]] = []
    if replay.initial_state_hash is not None:
        actual = state_hash(state)
        if actual != replay.initial_state_hash:
            moved.append((-1, replay.initial_state_hash, actual))

    for index, step in enumerate(replay.steps):
        legal = {repr(a): a for a in state.legal_actions()}
        if step.action not in legal:
            return None, (f"step {index}: {step.action} is no longer legal -- "
                          f"the recorded game is not a legal game any more")
        state.apply(legal[step.action])
        actual = state_hash(state)
        if actual != step.expected_state_hash:
            moved.append((index, step.expected_state_hash, actual))

    if not state.is_terminal():
        return None, "the recorded game no longer finishes"
    if tuple(state.returns()) != tuple(replay.final_returns):
        return None, (f"outcome changed: {replay.final_returns} -> "
                      f"{state.returns()}; that is not a representation change")
    return (raw, moved), None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    db = load_db()
    total = 0
    for path in sorted(HERE.glob("riftbound-*.json")):
        result, problem = inspect(path, db)
        if problem:
            print(f"{path.name}: REFUSED — {problem}")
            return 1
        raw, moved = result
        print(f"{path.name}: {len(raw['steps'])} steps, "
              f"{len(moved)} hash(es) moved")
        for index, was, now in moved[:5]:
            where = "initial" if index < 0 else f"step {index}"
            print(f"    {where}: {was[:16]} -> {now[:16]}")
        if args.apply and moved:
            for index, _, now in moved:
                if index < 0:
                    raw["initial_state_hash"] = now
                else:
                    raw["steps"][index]["expected_state_hash"] = now
            path.write_text(json.dumps(raw, indent=2) + "\n")
            total += len(moved)
    if args.apply:
        print(f"\nupdated {total} hash(es); actions and outcomes untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
