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
