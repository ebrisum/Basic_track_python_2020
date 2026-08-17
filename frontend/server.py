"""Local play server: stdlib HTTP only, no framework, no new dependencies.

    .venv/bin/python -m frontend.server            # then open http://127.0.0.1:8000
    .venv/bin/python -m frontend.server --port 9000 --seed 42

The engine is the single source of truth. This server holds one game in
memory, renders `state.observation(player)` as JSON, and accepts actions by
their `repr()` -- resolved against `legal_actions()` exactly the way the replay
runner does, so the UI can never submit a move the rules do not allow.

Hot-seat: both players share one screen. `?player=N` selects whose hand is
shown; the server refuses to reveal the other player's hand (128 Privacy).
"""

from __future__ import annotations

import argparse
import json
import random
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cards.database import load as load_db
from engine.setup import DeckError, available_decks, build_state, load_deck
from engine.state import Phase, RiftboundState

STATIC = Path(__file__).resolve().parent / "static"


class Game:
    """One in-memory game, guarded by a lock (the server is threaded)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: RiftboundState | None = None
        self.deck_names: tuple[str, str] = ("", "")
        self.db = load_db()

    def new(self, deck0: str, deck1: str, seed: int | None) -> None:
        seed = random.randrange(1 << 30) if seed is None else seed
        d0, d1 = load_deck(deck0), load_deck(deck1)
        with self._lock:
            state = build_state(d0, d1, seed=seed, db=self.db)
            state.allow_concede = True  # interactive play (649)
            self.state = state
            self.deck_names = (d0.name, d1.name)

    def snapshot(self, player: int) -> dict:
        with self._lock:
            state = self.state
            if state is None:
                return {"started": False, "decks": available_decks()}
            observation = state.observation(player)
            payload = asdict(observation)
            payload.update(
                {
                    "started": True,
                    "seed": state.seed,
                    "die_roll": list(state.die_roll),
                    "first_player": state.first_player,
                    "deck_names": list(self.deck_names),
                    "is_terminal": state.is_terminal(),
                    "battlefield_choices": [
                        [
                            {"card_id": cid, "name": self.db[cid].name}
                            for cid in choices
                        ]
                        for choices in state.battlefield_choices
                    ],
                    "legal": (
                        [repr(a) for a in state.legal_actions()]
                        if state.current_player == player
                        else []
                    ),
                    "log": state.log[-40:],
                }
            )
            return payload

    def act(self, player: int, action_repr: str) -> dict:
        with self._lock:
            state = self.state
            if state is None:
                return {"error": "no game in progress"}
            if state.current_player != player:
                return {"error": f"it is player {state.current_player}'s turn"}
            legal = state.legal_actions()
            matches = [a for a in legal if repr(a) == action_repr]
            if not matches:
                return {"error": f"illegal action: {action_repr}"}
            state.apply(matches[0])
            return {"ok": True}


GAME = Game()


class Handler(BaseHTTPRequestHandler):
    server_version = "riftbound-sim"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # ------------------------------------------------------------------ util

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict[str, str]:
        if "?" not in self.path:
            return {}
        raw = self.path.split("?", 1)[1]
        out = {}
        for part in raw.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                out[key] = value
        return out

    # ------------------------------------------------------------------ HTTP

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif route == "/api/decks":
            self._send({"decks": available_decks()})
        elif route == "/api/state":
            player = int(self._query().get("player", "0"))
            self._send(GAME.snapshot(0 if player not in (0, 1) else player))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send({"error": "bad JSON"}, 400)
            return

        if route == "/api/new":
            decks = available_decks()
            if len(decks) < 2:
                self._send({"error": "need at least two decks in decks/"}, 400)
                return
            deck0 = body.get("deck0") or decks[0]
            deck1 = body.get("deck1") or decks[1 % len(decks)]
            seed = body.get("seed")
            try:
                GAME.new(deck0, deck1, int(seed) if seed not in (None, "") else None)
            except (DeckError, FileNotFoundError) as exc:
                self._send({"error": str(exc)}, 400)
                return
            self._send({"ok": True})
        elif route == "/api/action":
            player = int(body.get("player", 0))
            result = GAME.act(player, body.get("action", ""))
            self._send(result, 200 if "error" not in result else 400)
        else:
            self.send_error(404)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--deck0", default=None)
    parser.add_argument("--deck1", default=None)
    args = parser.parse_args(argv)

    decks = available_decks()
    if not decks:
        print("no decks found -- run `python decks/build_decks.py` first")
        return 1
    if args.deck0 or args.deck1 or args.seed is not None:
        GAME.new(args.deck0 or decks[0], args.deck1 or decks[1 % len(decks)], args.seed)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Riftbound play server on http://{args.host}:{args.port}")
    print(f"decks available: {', '.join(decks)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
