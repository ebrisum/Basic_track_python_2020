#!/usr/bin/env python3
"""Start a local Riftbound game. One command, no installation.

    python3 play.py

Then open http://127.0.0.1:8000 -- or let this open it for you.

The engine is pure standard library, so there is nothing to install and no
virtual environment to make. (Tests need pytest; playing does not.) This
script builds the starter decks if they are missing, starts the local server,
and opens a browser at it.

    python3 play.py --watch                 # two agents play, you watch
    python3 play.py --seat1 ismcts          # you are P0, the search is P1
    python3 play.py --port 9000 --no-open
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 10)


def ensure_decks() -> bool:
    """Build the starter decks the first time, so a fresh clone just runs."""
    from engine.setup import available_decks

    if available_decks():
        return True
    print("no decks yet — building the starter decks...")
    result = subprocess.run(
        [sys.executable, str(ROOT / "decks" / "build_decks.py")],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print("could not build decks; see the error above")
        return False
    return bool(available_decks())


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, "
              f"this is {sys.version.split()[0]}")
        return 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seat0", default="human")
    parser.add_argument("--seat1", default="human")
    parser.add_argument("--watch", action="store_true",
                        help="hand both seats to agents and start playing")
    parser.add_argument("--delay-ms", type=int, default=600)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser")
    args = parser.parse_args()

    if not ensure_decks():
        return 1

    from frontend import server

    url = f"http://{args.host}:{args.port}"
    seat0, seat1 = args.seat0, args.seat1
    if args.watch and seat0 == seat1 == "human":
        seat0, seat1 = "greedy", "ismcts"

    if not args.no_open:
        # The server blocks, so open the page from a timer once it is up.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"\n  Riftbound → {url}")
    print("  hot-seat: both players share the screen; 'view as' switches hands")
    print("  Ctrl-C to stop\n")

    argv = [
        "--host", args.host, "--port", str(args.port),
        "--seat0", seat0, "--seat1", seat1,
        "--delay-ms", str(args.delay_ms), "--iterations", str(args.iterations),
    ]
    if args.watch:
        argv.append("--watch")
    if args.seed is not None:
        argv += ["--seed", str(args.seed)]
    return server.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
