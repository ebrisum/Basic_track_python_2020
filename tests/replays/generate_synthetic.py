"""Mint the synthetic replays checked into this directory.

    .venv/bin/python tests/replays/generate_synthetic.py

Run this only when the replay *format* changes on purpose. Regenerating to
make a failing replay pass defeats the entire point of the harness -- a
divergence means the engine changed, and that change is what needs
justifying.

Real Rift Atlas games are dropped in here as-is; they are never regenerated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.random_agent import RandomAgent  # noqa: E402
from engine.replay import record_game  # noqa: E402
from tests.fixtures.toy_game import build_toy_state  # noqa: E402

HERE = Path(__file__).resolve().parent

# Seeds chosen to cover distinct shapes: a short game, a long one, and a
# non-decisive finish, so the harness is exercised on more than one path.
SPECS = [
    ("synthetic-toy-001", 21, "Toy game, random policy, decisive finish."),
    ("synthetic-toy-002", 137, "Toy game, random policy, different branch."),
    ("synthetic-toy-003", 4242, "Toy game, random policy, long game."),
]


def main() -> int:
    for replay_id, seed, description in SPECS:
        replay = record_game(
            build_toy_state,
            RandomAgent(seed),
            seed=seed,
            replay_id=replay_id,
            description=description,
            game="toy",
        )
        replay.source = "synthetic"
        path = HERE / f"{replay_id}.json"
        replay.save(path)
        print(
            f"{path.name}: {len(replay.steps)} steps, "
            f"returns={replay.final_returns}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
