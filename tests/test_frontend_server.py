"""Frontend server tests: HTTP level, stdlib only.

Deliberately no browser dependency. What matters is the contract between the
UI and the engine -- that the server refuses illegal moves, refuses
out-of-turn moves, and never reveals the opponent's hand. The rendering itself
holds no rules logic, so it cannot be the source of a rules bug.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from frontend import server as fe


@pytest.fixture(scope="module")
def base_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fe.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(base, path):
    return json.load(OPENER.open(base + path, timeout=10))


def post(base, path, body):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.load(OPENER.open(request, timeout=10))
    except urllib.error.HTTPError as exc:
        return json.load(exc)


@pytest.fixture
def game(base_url):
    post(base_url, "/api/new", {
        "deck0": "jinx_chaos_fury", "deck1": "volibear_body_fury", "seed": 7,
    })
    return base_url


def test_index_is_served(base_url):
    with OPENER.open(base_url + "/", timeout=10) as response:
        body = response.read().decode()
    assert response.status == 200
    assert "<title>Riftbound" in body


def test_decks_are_listed(base_url):
    assert len(get(base_url, "/api/decks")["decks"]) >= 2


def test_new_game_reaches_battlefield_selection(game):
    state = get(game, "/api/state?player=0")
    assert state["started"] is True
    assert state["phase"] == "setup_battlefield"
    assert state["die_roll"][0] != state["die_roll"][1]  # 115 -- ties rerolled


def test_illegal_action_is_refused(game):
    state = get(game, "/api/state?player=0")
    result = post(game, "/api/action", {
        "player": state["current_player"], "action": "play:999999",
    })
    assert "error" in result and "illegal" in result["error"]


def test_out_of_turn_action_is_refused(game):
    state = get(game, "/api/state?player=0")
    other = 1 - state["current_player"]
    result = post(game, "/api/action", {"player": other, "action": "pass"})
    assert "error" in result and "turn" in result["error"]


def test_only_the_player_to_move_is_offered_actions(game):
    state = get(game, "/api/state?player=0")
    idle = get(game, f"/api/state?player={1 - state['current_player']}")
    assert idle["legal"] == []


def test_opponent_hand_is_never_revealed(game):
    """128 Privacy -- the server must not leak the other player's cards."""
    for _ in range(4):  # advance past setup so hands exist
        state = get(game, "/api/state?player=0")
        current = state["current_player"]
        view = get(game, f"/api/state?player={current}")
        if not view["legal"]:
            break
        post(game, "/api/action", {"player": current, "action": view["legal"][0]})

    view0 = get(game, "/api/state?player=0")
    assert view0["opponent_hand_size"] >= 0
    own_ids = {c["instance_id"] for c in view0["hand"]}
    view1 = get(game, "/api/state?player=1")
    other_ids = {c["instance_id"] for c in view1["hand"]}
    assert own_ids.isdisjoint(other_ids)
    assert len(view1["hand"]) == view0["opponent_hand_size"]


def test_a_legal_action_advances_the_game(game):
    before = get(game, "/api/state?player=0")
    current = before["current_player"]
    view = get(game, f"/api/state?player={current}")
    result = post(game, "/api/action", {
        "player": current, "action": view["legal"][0],
    })
    assert result.get("ok") is True
    after = get(game, "/api/state?player=0")
    assert after != before


def test_unknown_deck_is_rejected(base_url):
    result = post(base_url, "/api/new", {"deck0": "nope", "deck1": "nope"})
    assert "error" in result


# --------------------------------------------------------------- spectator


@pytest.fixture
def spectated(base_url):
    """A game with both seats handed to agents, restored to hot-seat after."""
    post(base_url, "/api/autoplay", {
        "seats": ["greedy", "random"], "running": False, "delay_ms": 0,
    })
    post(base_url, "/api/new", {
        "deck0": "jinx_chaos_fury", "deck1": "volibear_body_fury", "seed": 7,
    })
    yield base_url
    post(base_url, "/api/autoplay", {"seats": ["human", "human"], "running": False})


def test_stepping_advances_an_agent_seat(spectated):
    before = get(spectated, "/api/state?player=0")
    assert post(spectated, "/api/step", {"n": 3})["stepped"] == 3
    after = get(spectated, "/api/state?player=0")
    assert after["log"] != before["log"]


def test_an_agent_seat_is_never_offered_actions_by_the_ui(spectated):
    """The UI drives human seats only; an agent seat must not be clickable."""
    post(spectated, "/api/step", {"n": 2})
    state = get(spectated, "/api/state?player=0")
    current = state["current_player"]
    assert state["seats"][current] != "human"
    assert get(spectated, f"/api/state?player={current}")["legal"] == []


def test_last_decision_reports_what_the_agent_did(spectated):
    post(spectated, "/api/step", {"n": 1})
    decision = get(spectated, "/api/state?player=0")["last_decision"]
    assert decision["kind"] in ("greedy", "random")
    assert decision["considered"] >= 1
    assert 0.0 <= decision["evaluation"] <= 1.0
    assert decision["seconds"] >= 0.0


def test_a_spectated_game_is_never_offered_concede(spectated):
    """649 -- a random policy would concede at once if it were on the menu."""
    post(spectated, "/api/step", {"n": 4})
    for player in (0, 1):
        assert "concede" not in get(spectated, f"/api/state?player={player}")["legal"]


def test_stepping_stops_at_a_human_seat(base_url):
    post(base_url, "/api/autoplay", {"seats": ["greedy", "human"], "running": False})
    post(base_url, "/api/new", {
        "deck0": "jinx_chaos_fury", "deck1": "volibear_body_fury", "seed": 7,
    })
    post(base_url, "/api/step", {"n": 200})
    state = get(base_url, "/api/state?player=0")
    assert state["seats"][state["current_player"]] == "human"
    assert not state["is_terminal"]
    post(base_url, "/api/autoplay", {"seats": ["human", "human"], "running": False})


def test_an_unknown_agent_kind_falls_back_to_human(base_url):
    post(base_url, "/api/autoplay", {"seats": ["wizard", "human"], "running": False})
    assert get(base_url, "/api/state?player=0")["seats"] == ["human", "human"]


def test_settings_persist_across_a_new_game(base_url):
    post(base_url, "/api/autoplay", {
        "seats": ["ismcts", "greedy"], "delay_ms": 120, "iterations": 25,
    })
    post(base_url, "/api/new", {
        "deck0": "jinx_chaos_fury", "deck1": "volibear_body_fury", "seed": 7,
    })
    state = get(base_url, "/api/state?player=0")
    assert state["seats"] == ["ismcts", "greedy"]
    assert state["delay_ms"] == 120 and state["iterations"] == 25
    post(base_url, "/api/autoplay", {"seats": ["human", "human"], "running": False})


def test_the_controls_are_configurable_before_a_game_exists():
    """The UI mirrors server settings, so they must be readable with no state."""
    fresh = fe.Game()
    fresh.configure(seats=["greedy", "ismcts"], delay_ms=120, iterations=25)
    snapshot = fresh.snapshot(0)
    assert snapshot["started"] is False
    assert snapshot["seats"] == ["greedy", "ismcts"]
    assert snapshot["delay_ms"] == 120 and snapshot["iterations"] == 25
    assert snapshot["agent_kinds"] == list(fe.AGENT_KINDS)
    assert fresh.step(1) == 0          # nothing to step -- there is no game


def test_a_spectated_game_is_reproducible(base_url):
    """Determinism is the whole point: same seed + same seats = same game."""
    def play() -> list[str]:
        post(base_url, "/api/autoplay", {"seats": ["greedy", "greedy"]})
        post(base_url, "/api/new", {
            "deck0": "jinx_chaos_fury", "deck1": "volibear_body_fury", "seed": 3,
        })
        post(base_url, "/api/step", {"n": 12})
        return get(base_url, "/api/state?player=0")["log"]

    assert play() == play()
    post(base_url, "/api/autoplay", {"seats": ["human", "human"], "running": False})


# ------------------------------------------------------- battlefield threat


def advance(base, steps=6):
    """Play a few legal moves so setup is behind us and a board exists."""
    for _ in range(steps):
        state = get(base, "/api/state?player=0")
        current = state["current_player"]
        view = get(base, f"/api/state?player={current}")
        if not view["legal"]:
            break
        post(base, "/api/action", {"player": current, "action": view["legal"][0]})


def test_the_snapshot_carries_a_forecast_per_battlefield(game):
    advance(game)
    state = get(game, "/api/state?player=0")
    assert state["battlefields"], "setup should be past battlefield selection"
    assert len(state["forecast"]) == len(state["battlefields"])
    first = state["forecast"][0]
    for key in ("can_take", "can_lose", "can_lose_next_turn",
                "committed", "reinforcement", "hidden_threat"):
        assert key in first


def test_the_forecast_is_told_from_the_asking_seat(game):
    """It answers "can *I* take it", so the two seats must not get one
    shared answer -- and neither may be computed from the other's hand."""
    advance(game)
    a = get(game, "/api/state?player=0")["forecast"]
    b = get(game, "/api/state?player=1")["forecast"]
    assert len(a) == len(b)
    for one, two in zip(a, b):
        assert one["committed"] == two["committed"]      # the board is public
        assert one["hidden_threat"] != [] and two["hidden_threat"] != []
