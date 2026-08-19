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
from cards.database import load as load_db  # noqa: E402
from engine.replay import record_game  # noqa: E402
from engine.versions import provenance  # noqa: E402
from engine.setup import build_state, load_deck  # noqa: E402
from tests.fixtures.toy_game import build_toy_state  # noqa: E402

HERE = Path(__file__).resolve().parent

# Seeds chosen to cover distinct shapes: a short game, a long one, and a
# non-decisive finish, so the harness is exercised on more than one path.
SPECS = [
    ("synthetic-toy-001", 21, "Toy game, random policy, decisive finish."),
    ("synthetic-toy-002", 137, "Toy game, random policy, different branch."),
    ("synthetic-toy-003", 4242, "Toy game, random policy, long game."),
]


# A replay is only worth what it covers. Seed 11 is an ordinary-length game;
# seed 34 is the longest found in the first 120 seeds and is here on purpose,
# because a deep game exercises resolution paths a short one never reaches.
#
# These were re-recorded when the 337.4 priority fix changed which player acts
# first after a play. That is a *behaviour* change, so the previously recorded
# games stopped being legal games and could not be repaired -- unlike a
# representation change, where `repair.py` updates the hashes and keeps the
# recorded line intact. The old seed-29 game was 352 steps; seed 34 restores
# that depth rather than leaving the fixture set shallower than it was.
RIFTBOUND_SPECS = [
    ("riftbound-ogn-001", 11, "Riftbound 1v1, random policy, full game."),
    ("riftbound-ogn-002", 34, "Riftbound 1v1, random policy, long game."),
]


def build_riftbound(seed: int):
    db = load_db()
    d0, d1 = load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")
    return build_state(d0, d1, seed=seed, db=db, validate_decks=False)


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

    for replay_id, seed, description in RIFTBOUND_SPECS:
        replay = record_game(
            build_riftbound,
            RandomAgent(seed),
            seed=seed,
            replay_id=replay_id,
            description=description,
            game="riftbound",
            provenance=provenance(load_db()),
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
