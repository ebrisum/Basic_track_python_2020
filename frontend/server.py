"""Local play server: stdlib HTTP only, no framework, no new dependencies.

    .venv/bin/python -m frontend.server            # then open http://127.0.0.1:8000
    .venv/bin/python -m frontend.server --port 9000 --seed 42

The engine is the single source of truth. This server holds one game in
memory, renders `state.observation(player)` as JSON, and accepts actions by
their `repr()` -- resolved against `legal_actions()` exactly the way the replay
runner does, so the UI can never submit a move the rules do not allow.

Hot-seat: both players share one screen. `?player=N` selects whose hand is
shown; the server refuses to reveal the other player's hand (128 Privacy).

Spectator mode: attach an agent to either seat and watch it play. A daemon
thread steps the game while `running` is set, so the same UI that a human
plays through also shows an agent playing -- which is the point, since a
divergence between what the agent does and what the board shows would mean
the UI is lying. Each decision reports the agent's own view of the position
(`evaluation`), so the search can be watched changing its mind.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agents.greedy_agent import GreedyAgent
from agents.ismcts import ISMCTSAgent
from agents.random_agent import RandomAgent
from analysis.evaluation import evaluate, load_model
from cards.database import load as load_db
from cards.gear import equipment_profile
from cards.scripts import activated_abilities
from engine.setup import DeckError, available_decks, build_state, load_deck
from engine.state import Phase, RiftboundState
from engine.threat import forecast_all

# Agents a seat can be handed to. "human" means the UI drives that seat.
AGENT_KINDS: tuple[str, ...] = ("human", "random", "greedy", "ismcts")


def make_agent(kind: str, seed: int, iterations: int = 60):
    if kind == "random":
        return RandomAgent(seed)
    if kind == "greedy":
        return GreedyAgent(seed, load_model())
    if kind == "ismcts":
        return ISMCTSAgent(seed, iterations=iterations, model=load_model())
    return None

STATIC = Path(__file__).resolve().parent / "static"


class Game:
    """One in-memory game, guarded by a lock (the server is threaded)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: RiftboundState | None = None
        self.deck_names: tuple[str, str] = ("", "")
        self.db = load_db()
        # Spectator mode.
        self.seats: list[str] = ["human", "human"]
        self.running = False
        self.delay_ms = 400
        self.iterations = 60
        self._agents: list[object | None] = [None, None]
        self.last_decision: dict | None = None
        self._stepper = threading.Thread(target=self._run_loop, daemon=True)
        self._stepper.start()

    def new(self, deck0: str, deck1: str, seed: int | None) -> None:
        seed = random.randrange(1 << 30) if seed is None else seed
        d0, d1 = load_deck(deck0), load_deck(deck1)
        with self._lock:
            state = build_state(d0, d1, seed=seed, db=self.db)
            # 649 -- conceding is offered to a human, never to an agent, or a
            # spectated game ends the moment a random policy picks it.
            state.allow_concede = all(s == "human" for s in self.seats)
            self.state = state
            self.deck_names = (d0.name, d1.name)
            self.last_decision = None
            self._agents = [
                make_agent(kind, seed + 1000 * i, self.iterations)
                for i, kind in enumerate(self.seats)
            ]

    def configure(self, seats, running=None, delay_ms=None, iterations=None) -> None:
        """Attach agents to seats and set the playback speed."""
        with self._lock:
            if seats:
                self.seats = [s if s in AGENT_KINDS else "human" for s in seats]
            if delay_ms is not None:
                self.delay_ms = max(0, min(5000, int(delay_ms)))
            if iterations is not None:
                self.iterations = max(1, min(2000, int(iterations)))
            seed = self.state.seed if self.state else 0
            self._agents = [
                make_agent(kind, seed + 1000 * i, self.iterations)
                for i, kind in enumerate(self.seats)
            ]
            if self.state is not None:
                self.state.allow_concede = all(s == "human" for s in self.seats)
            if running is not None:
                self.running = bool(running)

    def step(self, count: int = 1) -> int:
        """Advance up to `count` agent decisions. Returns how many were taken."""
        taken = 0
        for _ in range(count):
            if not self._step_once():
                break
            taken += 1
        return taken

    def _step_once(self) -> bool:
        with self._lock:
            state = self.state
            if state is None or state.is_terminal():
                return False
            seat = state.current_player
            agent = self._agents[seat]
            if agent is None:          # a human owns this seat
                return False
            legal = state.legal_actions()
            if not legal:
                return False
            started = time.perf_counter()
            action = agent.act(state)
            elapsed = time.perf_counter() - started
            state.apply(action)
            self.last_decision = {
                "seat": seat,
                "kind": self.seats[seat],
                "action": repr(action),
                "considered": len(legal),
                "seconds": round(elapsed, 3),
                # The agent's own read of the position it just created.
                "evaluation": round(evaluate(state, seat), 4)
                if not state.is_terminal() else state.returns()[seat],
            }
            return True

    def _run_loop(self) -> None:
        """Daemon: steps the game while `running`, at `delay_ms` per decision."""
        while True:
            if self.running and self._step_once():
                time.sleep(self.delay_ms / 1000.0)
            else:
                if self.running:
                    # Nothing to do -- a human seat is to move, or the game is
                    # over. Stop rather than spin.
                    self.running = False
                time.sleep(0.05)

    def snapshot(self, player: int) -> dict:
        with self._lock:
            state = self.state
            if state is None:
                # The spectator controls are configurable before a game
                # starts, so the settings ship even with no state.
                return {
                    "started": False,
                    "decks": available_decks(),
                    "seats": list(self.seats),
                    "running": self.running,
                    "delay_ms": self.delay_ms,
                    "iterations": self.iterations,
                    "agent_kinds": list(AGENT_KINDS),
                    "last_decision": None,
                }
            observation = state.observation(player)
            payload = asdict(observation)
            self._annotate_cards(payload)
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
                        and self.seats[state.current_player] == "human"
                        else []
                    ),
                    # What each battlefield is worth trying for, and what is
                    # at risk. Public board facts are exact; the opponent's
                    # hand is bounded by their Domain Identity (103.1.b).
                    "forecast": [asdict(f) for f in forecast_all(state, player)],
                    "seats": list(self.seats),
                    "running": self.running,
                    "delay_ms": self.delay_ms,
                    "iterations": self.iterations,
                    "agent_kinds": list(AGENT_KINDS),
                    "last_decision": self.last_decision,
                    "evaluation": (
                        round(evaluate(state, player), 4)
                        if not state.is_terminal() else state.returns()[player]
                    ),
                    "log": state.log[-40:],
                }
            )
            return payload

    def _annotate_cards(self, payload: dict) -> None:
        """Add card-face facts the UI needs but the observation should not carry.

        `is_equipment`, the Might Bonus and per-ability labels are all
        derivable from the card database, which this server already holds.
        Putting them on the observation instead would widen the frozen
        agent-facing interface and rewrite every replay hash -- the harness
        caught exactly that -- for information no agent needs.
        """
        def walk(node):
            """Yield every card view, however deeply the payload nests it.

            Zones vary in shape -- `board` is a tuple, `trash` a tuple per
            player, `legend` a single view -- so this recurses rather than
            listing them and silently missing one.
            """
            if isinstance(node, dict):
                if "card_id" in node:
                    yield node
                    return
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    yield from walk(value)

        for view in walk(payload):
                card = self.db.get(view.get("card_id"))
                if card is None:
                    continue
                profile = equipment_profile(card)
                view["is_equipment"] = profile is not None
                view["might_bonus"] = profile.might_bonus if profile else None
                view["ability_labels"] = [
                    a.text or "activate" for a in activated_abilities(card)
                ]

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
        elif route == "/api/autoplay":
            GAME.configure(
                seats=body.get("seats"),
                running=body.get("running"),
                delay_ms=body.get("delay_ms"),
                iterations=body.get("iterations"),
            )
            self._send({"ok": True})
        elif route == "/api/step":
            taken = GAME.step(int(body.get("n", 1)))
            self._send({"ok": True, "stepped": taken})
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
    parser.add_argument("--seat0", default="human", choices=AGENT_KINDS)
    parser.add_argument("--seat1", default="human", choices=AGENT_KINDS)
    parser.add_argument("--watch", action="store_true",
                        help="start playing immediately (spectator mode)")
    parser.add_argument("--delay-ms", type=int, default=400)
    parser.add_argument("--iterations", type=int, default=60,
                        help="ISMCTS search budget per decision")
    args = parser.parse_args(argv)

    decks = available_decks()
    if not decks:
        print("no decks found -- run `python decks/build_decks.py` first")
        return 1
    GAME.configure(seats=[args.seat0, args.seat1], delay_ms=args.delay_ms,
                   iterations=args.iterations)
    if args.deck0 or args.deck1 or args.seed is not None or args.watch:
        GAME.new(args.deck0 or decks[0], args.deck1 or decks[1 % len(decks)], args.seed)
    if args.watch:
        GAME.configure(seats=None, running=True)
        print(f"spectating: P0={args.seat0} vs P1={args.seat1}")

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
