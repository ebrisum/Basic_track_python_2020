"""811.1.d -- what a card played from Hidden is allowed to choose.

No shipped card exercises this yet: 34 cards have HIDDEN, exactly one is
scripted, and its play effect targets itself. So these tests build synthetic
scripts. That is deliberate -- the restriction is machinery the interpreter
must already carry correctly when the first hidden spell with a target lands,
and a rule tested only by the cards that happen to be scripted today is a rule
tested by accident.

The Riftbound FAQ's Temporal Breach and Rebuttal entries are the worked
examples this file mirrors.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.dsl import Ability, CardScript, Deal, Kill, Selector, TriggerKind
from cards.primitives import candidates, restriction_binds
from engine.actions import ChooseTarget, HideCard, PlayCard
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()


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


def put_unit(state, player, location):
    card_id = next(c.card_id for c in DB.cards.values() if c.type == "unit")
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- the exception, as a static property of the selector (811.1.d.2) --------


def test_the_restriction_binds_an_ordinary_selector():
    """"Kill a unit" can be satisfied at a battlefield, so Hidden narrows it."""
    assert restriction_binds(Selector(type="unit")) is True
    assert restriction_binds(Selector(type="unit", location="battlefield")) is True


def test_the_restriction_lifts_when_the_card_demands_a_base():
    """811.1.d.2 -- "unless the ability explicitly restricts targeting in a way
    that makes this impossible". A selector that must find its target in a base
    can never find it at a battlefield, so it chooses freely."""
    assert restriction_binds(Selector(type="unit", location="base")) is False


# --- candidates() narrows to the battlefield --------------------------------


def test_candidates_are_narrowed_to_the_hidden_battlefield(decks):
    state = arena(decks)
    here = put_unit(state, 0, bf_location(0))
    there = put_unit(state, 0, bf_location(1))
    home = put_unit(state, 0, BASE_LOCATION)
    selector = Selector(type="unit", controller="friendly")

    free = candidates(state, selector, 0, None)
    assert {here, there, home} <= set(free)

    bound = candidates(state, selector, 0, None, bf_location(0))
    assert here in bound
    assert there not in bound and home not in bound


def test_a_base_only_selector_ignores_the_hidden_battlefield(decks):
    """The 811.1.d.2 exception, end to end through candidates()."""
    state = arena(decks)
    put_unit(state, 0, bf_location(0))
    home = put_unit(state, 0, BASE_LOCATION)
    selector = Selector(type="unit", controller="friendly", location="base")

    bound = candidates(state, selector, 0, None, bf_location(0))
    assert home in bound, "a base-only target is chosen freely (811.1.d.2)"


# --- end to end: a hidden spell with a real target --------------------------


@pytest.fixture
def hidden_bolt(monkeypatch):
    """Script a real HIDDEN spell as "Deal 3 to a unit" and hand back its id."""
    from cards import scripts

    card_id = next(
        c.card_id for c in DB.cards.values()
        if c.type == "spell" and c.has_hidden
    )
    script = CardScript(
        card_id=card_id,
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(Deal(amount=3,
                              selector=Selector(scope="choose", type="unit")),),
            ),
        ),
    )
    scripts.registry.cache_clear()
    monkeypatch.setattr(scripts, "ALL_SCRIPTS", scripts.ALL_SCRIPTS + (script,))
    yield card_id
    scripts.registry.cache_clear()


def hide_at(state, player, card_id, index):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    state.players[player].pool.universal_power += 1
    state.battlefields[index].controller = player
    state.apply(HideCard(instance_id, index))
    state.turn_number += 1        # 811.1.b -- playable from the next turn
    return instance_id


def test_a_hidden_spell_cannot_reach_another_battlefield(decks, hidden_bolt):
    """811.1.d.2, and the FAQ's Temporal Breach ruling: the target must be
    chosen from among options at the battlefield it was hidden at."""
    state = arena(decks)
    player = state.turn_player
    here = put_unit(state, player, bf_location(0))
    there = put_unit(state, player, bf_location(1))
    home = put_unit(state, player, BASE_LOCATION)

    card = hide_at(state, player, hidden_bolt, 0)
    state._current_player = player
    state.apply(PlayCard(card))
    while state.phase is Phase.CHAIN:
        state.apply(state.legal_actions()[-1])   # both players pass

    offered = {a.instance_id for a in state.legal_actions()
               if isinstance(a, ChooseTarget)}
    if offered:
        assert offered == {here}
    else:
        # Only one legal target, so the engine resolved it without asking.
        assert state.cards[here].damage == 3
    assert state.cards[there].damage == 0
    assert state.cards[home].damage == 0


def test_the_same_spell_played_from_hand_targets_freely(decks, hidden_bolt):
    """811.3 -- "may be played for its cost as normal ... with no restrictions
    on targeting"."""
    state = arena(decks)
    player = state.turn_player
    here = put_unit(state, player, bf_location(0))
    there = put_unit(state, player, bf_location(1))

    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=hidden_bolt, owner=player,
        controller=player,
    )
    state.players[player].hand.append(instance_id)
    state._current_player = player
    state.apply(PlayCard(instance_id))
    while state.phase is Phase.CHAIN:
        state.apply(state.legal_actions()[-1])   # both players pass

    offered = {a.instance_id for a in state.legal_actions()
               if isinstance(a, ChooseTarget)}
    assert {here, there} <= offered, "played from hand, targeting is unrestricted"


def test_a_hidden_spell_with_no_target_there_cannot_be_played_from_hidden(
    decks, hidden_bolt
):
    """811.1.d -- "A card cannot be played from Hidden if it is a spell with no
    valid targets under these restrictions"."""
    state = arena(decks)
    player = state.turn_player
    put_unit(state, player, bf_location(1))       # a unit, but not here
    put_unit(state, player, BASE_LOCATION)

    card = hide_at(state, player, hidden_bolt, 0)
    state._current_player = player
    assert PlayCard(card) not in state.legal_actions()


def test_that_bar_lifts_as_soon_as_a_unit_arrives(decks, hidden_bolt):
    """The same position, one unit later: the play becomes legal again."""
    state = arena(decks)
    player = state.turn_player
    card = hide_at(state, player, hidden_bolt, 0)
    state._current_player = player
    assert PlayCard(card) not in state.legal_actions()

    put_unit(state, player, bf_location(0))
    assert PlayCard(card) in state.legal_actions()


def test_a_hidden_permanent_is_never_barred_for_want_of_a_target(decks):
    """811.1.d gates *spells*. A hidden permanent is played to that battlefield
    (811.1.d.1) whether or not its play effect finds anything."""
    from cards import scripts

    card_id = next(
        c.card_id for c in DB.cards.values() if c.type == "unit" and c.has_hidden
    )
    script = CardScript(
        card_id=card_id,
        abilities=(
            Ability(
                kind=TriggerKind.ON_PLAY,
                effects=(Kill(selector=Selector(scope="choose", type="unit",
                                                controller="enemy")),),
            ),
        ),
    )
    scripts.registry.cache_clear()
    original = scripts.ALL_SCRIPTS
    scripts.ALL_SCRIPTS = original + (script,)
    try:
        scripts.registry.cache_clear()
        state = arena(decks)
        player = state.turn_player
        card = hide_at(state, player, card_id, 0)
        state._current_player = player
        assert PlayCard(card) in state.legal_actions()
    finally:
        scripts.ALL_SCRIPTS = original
        scripts.registry.cache_clear()


# --- the chain holds more than one card at a time ---------------------------


def test_a_card_played_in_response_does_not_disturb_the_hidden_one(decks,
                                                                   hidden_bolt):
    """Regression. "Played from Hidden" used to be a single slot on the state,
    so any card played in response overwrote it: the hidden spell then resolved
    with no 811.1.d.2 restriction, and a hidden *permanent* landed in the base
    instead of at its battlefield (811.1.d.1). It belongs to the chain item."""
    state = arena(decks)
    player = state.turn_player
    other = state.opponent(player)
    here = put_unit(state, player, bf_location(0))
    there = put_unit(state, player, bf_location(1))

    card = hide_at(state, player, hidden_bolt, 0)
    state._current_player = player
    state.apply(PlayCard(card))

    # The opponent responds with an ordinary card while the hidden spell waits.
    responses = [a for a in state.legal_actions() if isinstance(a, PlayCard)]
    assert responses, "the opponent has a response available"
    state.apply(responses[0])
    while state.phase is Phase.CHAIN:
        passes = [a for a in state.legal_actions()
                  if type(a).__name__ == "PassPhase"]
        state.apply(passes[0])

    offered = {a.instance_id for a in state.legal_actions()
               if isinstance(a, ChooseTarget)}
    reached = offered or {i for i in (here, there) if state.cards[i].damage}
    assert there not in reached, (
        "the response overwrote the hidden spell's battlefield restriction"
    )
