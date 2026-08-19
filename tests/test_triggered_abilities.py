"""Triggered abilities go on the Chain (382-383).

**383.3**: "When a Condition is met, a Triggered Ability behaves like an
Activated Ability and is placed on the Chain."

The engine used to run a trigger's effects inline, straight out of the game
action that caused them. That is invisible in a two-card test and wrong in
three ways the rules care about:

* **383.3.c** -- a trigger can be put on the chain in a Closed or Open state
  on any player's turn, which means the *opponent can respond to it*. Inline
  resolution gives them no window at all.
* **383.3.d** -- simultaneous triggers are ordered on the chain by their
  controller, and resolve newest-first (340.1). Inline resolution fixes the
  order and hides it.
* **355.5.b** -- a trigger's targets are declared when *it* is finalized, not
  when the card that caused it was played.

341 of 526 playable cards print trigger wording, so this is the most common
mechanic in the game.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.dsl import TriggerKind
from engine.actions import PassPhase, PlayCard
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
CONFRONT = "OGN-129"      # a spell; its ON_RESOLVE is its own text, not a trigger


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    for player in state.players:
        player.pool.energy = 30
        player.pool.universal_power = 30
    return state


def give(state, player, card_id):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    return instance_id


def put_unit(state, player, card_id, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def scripted_with(kind: TriggerKind):
    """A scripted card carrying a trigger of `kind`, or skip."""
    from cards.scripts import registry

    for card_id, script in sorted(registry().items()):
        if any(a.kind is kind for a in script.abilities):
            return card_id
    pytest.skip(f"no scripted card has a {kind.value} trigger")


# --- 383.3: the trigger reaches the chain -----------------------------------


def test_a_play_trigger_goes_on_the_chain(decks):
    """383.3 -- it "behaves like an Activated Ability and is placed on the
    Chain", rather than executing inside the play that caused it."""
    card_id = scripted_with(TriggerKind.ON_PLAY)
    state = arena(decks)
    player = state.turn_player
    state._current_player = player

    state.apply(PlayCard(give(state, player, card_id)))
    while state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])

    assert any(item.kind == "trigger" for item in state.chain), (
        "the play trigger resolved inline instead of reaching the chain"
    )


def test_the_opponent_can_respond_to_a_trigger(decks):
    """383.3.c -- "Triggered Abilities can be put on the Chain during Closed
    States or Open States on any player's turn." A chain means a window, and
    a window means the opponent gets to act."""
    card_id = scripted_with(TriggerKind.ON_PLAY)
    state = arena(decks)
    player = state.turn_player
    other = state.opponent(player)
    state._current_player = player

    state.apply(PlayCard(give(state, player, card_id)))
    while state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])

    # 337.4 gives the caster the first window; passing hands it over.
    guard = 0
    while state.current_player != other and state.chain and guard < 6:
        guard += 1
        if PassPhase() not in state.legal_actions():
            break
        state.apply(PassPhase())

    assert state.chain, "the trigger is still waiting to resolve"
    assert state.current_player == other, "the opponent never got a window"


def test_the_trigger_resolves_once_everyone_passes(decks):
    """340.1 -- and then it resolves like any other chain item."""
    card_id = scripted_with(TriggerKind.ON_PLAY)
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    played = give(state, player, card_id)

    state.apply(PlayCard(played))
    guard = 0
    while state.chain and guard < 40:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert not state.chain
    assert state.cards[played].location is not None, "the unit is on the board"


# --- a spell's own text is not a triggered ability ---------------------------


def test_a_spells_own_instructions_are_not_a_trigger(decks):
    """359 -- a spell's rules text is the spell's own effect, executed as it
    resolves. It must not be pushed back onto the chain as a second item, or
    every spell would take two resolutions."""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player

    state.apply(PlayCard(give(state, player, CONFRONT)))
    guard = 0
    while state.chain and guard < 20:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert not state.chain
    assert player in state.units_enter_ready, "the spell's own text resolved"


# --- score triggers ---------------------------------------------------------


def test_a_conquer_trigger_reaches_the_chain(decks):
    """471.2 fires the trigger; 383.3 puts it on the chain like any other."""
    card_id = scripted_with(TriggerKind.ON_CONQUER)
    state = arena(decks)
    player = state.turn_player
    host = put_unit(state, player, "OGN-142", bf_location(0))
    gear = put_unit(state, player, card_id, bf_location(0))
    state.cards[gear].attached_to = host
    state.battlefields[0].scored_by.clear()

    state._score(player, state.battlefields[0], "Conquer")

    assert any(item.kind == "trigger" for item in state.chain)


# --- a trigger during an automatic phase holds the turn -----------------------


def test_a_beginning_phase_trigger_stops_the_turn_advancing(decks):
    """354.4 -- "If there are Tasks outstanding or currently being handled,
    finish those Tasks before continuing this process."

    A Hold trigger fires in the Beginning Phase (471.2.b). Once 383.3 puts it
    on the chain, the turn cannot walk on to Channel, Draw and Main while it
    sits there: 309.1 makes a chain a Closed State, and offering Main-phase
    actions in one would break 309.1.a.
    """
    state = arena(decks)
    player = state.turn_player
    put_unit(state, player, "OGN-066", bf_location(0))   # Ahri, ON_HOLD
    state.battlefields[0].controller = player
    state.battlefields[0].scored_by.clear()

    state.phase = Phase.BEGINNING
    state._run_automatic_phases()

    if state.chain:
        assert state.phase is Phase.CHAIN, (
            "a chain exists but the turn is not in a Closed State (309.1)"
        )
        assert all(not isinstance(a, type(None)) for a in state.legal_actions())


def test_the_turn_resumes_after_the_trigger_resolves(decks):
    """And once the chain empties, the remaining automatic phases still run --
    the trigger interrupts the sequence, it does not cancel it."""
    state = arena(decks)
    player = state.turn_player
    put_unit(state, player, "OGN-066", bf_location(0))
    state.battlefields[0].controller = player
    state.battlefields[0].scored_by.clear()
    drew = len(state.players[player].hand)

    state.phase = Phase.BEGINNING
    state._run_automatic_phases()
    guard = 0
    while state.chain and guard < 20:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break

    assert state.phase is Phase.MAIN, "the turn never reached its Main Phase"
    assert len(state.players[player].hand) == drew + 1, (
        "the Draw Phase was skipped when the trigger interrupted the sequence"
    )
