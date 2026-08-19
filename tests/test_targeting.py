"""Targets are declared when a spell goes on the chain (355.7-355.10).

355.8: "In order to put a spell or ability on the chain, valid choices must be
made for all targets." The engine used to defer the choice to resolution,
which cost four rules at once (RQ-19):

* a spell could never be **fizzled** by removing its target (359.3.e.5);
* **Deflect** (809) could not be charged, since the tax is priced off a target
  that did not exist yet;
* opponents **responded blind**, not knowing what a spell would hit;
* **"when you choose me"** triggers (383.4.b.3) could not fire on time.

The FAQ's targeting page lists the only cases that are *not* targets, and
those still resolve late: choices in non-public zones (355.10.a), effects with
no real choice (355.10.d), and sets chosen by other players (355.10.e).
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import ChooseTarget, PassPhase, PlayCard
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
RAY = "OGN-009"        # Hextech Ray -- "Deal 3 to a unit"
GUST = "OGN-169"       # Gust -- REACTION, returns a unit to hand


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


def put_unit(state, player, card_id="OGN-142", location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def drain_chain(state):
    guard = 0
    while state.chain and guard < 40:
        guard += 1
        if state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            break


# --- 355.8: the choice happens as the spell is played -----------------------


def test_a_target_is_chosen_before_the_spell_reaches_the_chain(decks):
    """355.8 -- "valid choices must be made for all targets" in order to put
    the spell on the chain. So the choice is offered at play time."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    put_unit(state, foe, location=bf_location(0))
    put_unit(state, foe, location=bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))

    assert state.phase is Phase.CHOOSING, "the caster is asked immediately"
    assert state.current_player == caster


def test_the_opponent_only_responds_once_the_target_is_known(decks):
    """The point of declaring early: a Reaction is priced by what it answers.
    The opponent must not be asked to respond to an untargeted spell."""
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    first = put_unit(state, foe, location=bf_location(0))
    put_unit(state, foe, location=bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))
    state.apply(ChooseTarget(first))

    assert state.phase is Phase.CHAIN
    item = state.chain[-1]
    assert item.targets, "the chain item carries its declared target"
    assert first in [i for ids in item.targets.values() for i in ids]


def test_a_single_legal_target_needs_no_question(decks):
    """355.10.d.2 -- it is still a target when only one choice exists, but
    there is no decision to offer, so the engine does not stop for one."""
    state = arena(decks)
    caster = state.turn_player
    only = put_unit(state, state.opponent(caster), location=bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))

    assert state.phase is Phase.CHAIN
    assert only in [i for ids in state.chain[-1].targets.values() for i in ids]


# --- 359.3.e.5: an illegal target is unaffected ------------------------------


def test_a_spell_fizzles_when_its_target_leaves(decks):
    """359.3.e.5 -- "If any of the spell's targets are no longer legal, those
    game objects ... are unaffected by the spell as it resolves."

    This is the play the engine could not represent at all: answering removal
    by moving the target out of reach. The spell used to simply pick something
    else at resolution.
    """
    state = arena(decks)
    caster = state.turn_player
    foe = state.opponent(caster)
    victim = put_unit(state, foe, location=bf_location(0))
    bystander = put_unit(state, foe, location=bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))

    # In response, the victim's controller takes it off the board.
    state.cards[victim].location = None
    state.players[foe].base.remove(victim)
    state.players[foe].hand.append(victim)

    drain_chain(state)

    assert state.cards[victim].damage == 0, "the illegal target was affected"
    assert state.cards[bystander].damage == 0, (
        "the spell re-picked a new target instead of fizzling"
    )


def test_a_spell_still_resolves_around_an_illegal_target(decks):
    """359.3.e.5 -- "the spell still resolves"; only the instructions tied to
    the illegal target are skipped. Nothing should crash or hang."""
    state = arena(decks)
    caster = state.turn_player
    victim = put_unit(state, state.opponent(caster), location=bf_location(0))

    state.apply(PlayCard(give(state, caster, RAY)))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))
    state.cards[victim].location = None

    drain_chain(state)

    assert not state.chain
    assert state.phase in (Phase.MAIN, Phase.CHAIN, Phase.SHOWDOWN, Phase.GAME_OVER)


# --- 355.8 as a legality gate ------------------------------------------------


def test_a_spell_with_no_legal_target_cannot_be_played(decks):
    """355.8 -- without a valid choice for every target, the spell cannot go
    on the chain at all, so it is not a legal action."""
    state = arena(decks)
    caster = state.turn_player
    ray = give(state, caster, RAY)

    assert not any(
        isinstance(a, PlayCard) and a.instance_id == ray
        for a in state.legal_actions()
    ), "a targeted spell with nothing to hit is not playable"


def test_it_becomes_playable_once_a_target_exists(decks):
    state = arena(decks)
    caster = state.turn_player
    ray = give(state, caster, RAY)
    put_unit(state, state.opponent(caster), location=bf_location(0))

    assert PlayCard(ray) in state.legal_actions()


# --- what still resolves late ------------------------------------------------


def test_a_discard_declares_no_target(decks):
    """355.10.a -- a card in a non-public zone is not a target. 422 Discard is
    a Limited Action the discarding player performs as the spell resolves, so
    it must not be pulled forward into the play step.

    `Discard` reaches the hand without a selector at all, which is what keeps
    it out of `targets_of`."""
    from cards.dsl import Ability, Discard, TriggerKind, Who
    from cards.primitives import targets_of

    ability = Ability(kind=TriggerKind.ON_RESOLVE,
                      effects=(Discard(count=1, who=Who.YOU),))
    assert targets_of((ability,)) == []


def test_an_all_scoped_effect_declares_no_target(decks):
    """355.10.d -- "There is no real choice involved". An effect that applies
    to everything matching targets nothing."""
    from cards.dsl import Ability, Kill, Selector, TriggerKind
    from cards.primitives import targets_of

    ability = Ability(kind=TriggerKind.ON_RESOLVE,
                      effects=(Kill(selector=Selector(scope="all", type="unit")),))
    assert targets_of((ability,)) == []


def test_an_each_player_effect_declares_no_target(decks):
    """355.10.e -- "part of a set chosen wholly or partly by other players".
    The FAQ's example is "Each player kills a unit they control"."""
    from cards.dsl import Ability, Kill, Selector, TriggerKind
    from cards.primitives import targets_of

    ability = Ability(
        kind=TriggerKind.ON_RESOLVE,
        effects=(Kill(selector=Selector(scope="choose", type="gear",
                                        each_player=True)),),
    )
    assert targets_of((ability,)) == []


def test_an_ordinary_choose_selector_does_declare_a_target(decks):
    """The counterweight: 355.7's default is that a chosen object *is* a
    target, and the exceptions above must not swallow it."""
    from cards.dsl import Ability, Kill, Selector, TriggerKind
    from cards.primitives import targets_of

    ability = Ability(kind=TriggerKind.ON_RESOLVE,
                      effects=(Kill(selector=Selector(scope="choose", type="unit")),))
    assert len(targets_of((ability,))) == 1
