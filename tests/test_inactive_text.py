"""Inactive text (718.2, 720-725).

An Equipment card carries two different kinds of text, and which one is live
depends on whether the card is attached:

* **718.2** -- "While in this state [Attached], the card's printed Rules Text
  is Inactive." 721.2: Inactive abilities "do not trigger, do not apply, and
  cannot be activated."
* **724** -- "Effect Text is Inactive unless the card with the Effect Text is
  Attached." 718.3: while attached, it is appended to the Top-Most Card.

So the Equip ability works only while the gear is loose, and the boxed effect
works only while it is worn. The engine had both live at all times, which let
an equipped gear re-equip itself off its host at will, and let a gear sitting
in a base fire its worn-only trigger.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import ActivateAbility
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import CardRef

DB = load_db()


def settle(state, limit: int = 40) -> None:
    """Let anything the last action put on the Chain resolve.

    383.3 makes a triggered ability a chain item, so a score or a play no
    longer executes its trigger inline: the trigger has to be passed down the
    chain like anything else.
    """
    from engine.actions import PassPhase
    from engine.state import Phase

    for _ in range(limit):
        if not state.chain:
            return
        if state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])
        elif PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            return
WARMOGS = "SFD-108"      # [EQUIP Body]; "When I conquer, buff me."


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


def put(state, player, card_id, location):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def equip_actions(state, gear):
    return [a for a in state.legal_actions()
            if isinstance(a, ActivateAbility) and a.instance_id == gear]


# --- 718.2 / 721.2: the Equip ability goes quiet once worn -------------------


def test_an_unattached_equipment_offers_its_equip_ability(decks):
    """723 -- "Rules Text is never Inactive by default"."""
    state = arena(decks)
    player = state.turn_player
    put(state, player, "OGN-142", bf_location(0))
    gear = put(state, player, WARMOGS, bf_location(0))
    state._current_player = player

    assert equip_actions(state, gear), "a loose Equipment can be equipped"


def test_an_attached_equipment_cannot_be_equipped_again(decks):
    """718.2 -- an attached card's printed Rules Text is Inactive, and 721.2
    says Inactive abilities "cannot be activated".

    Without this an equipped gear could re-target itself onto any other unit
    for its Equip cost, every turn, from wherever it was.
    """
    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    put(state, player, "OGN-142", bf_location(0))     # a second host to move to
    gear = put(state, player, WARMOGS, bf_location(0))
    state._current_player = player
    assert equip_actions(state, gear)

    state.cards[gear].attached_to = host
    assert not equip_actions(state, gear)


# --- 724: the worn effect goes quiet once loose -----------------------------


def test_an_unattached_equipment_does_not_fire_its_effect_text(decks):
    """724 -- "Effect Text is Inactive unless the card ... is Attached."

    Warmog's Armor sitting in a base fired "When I conquer, buff me" and put a
    buff counter on itself -- a state 702 forbids outright, since buffs go on
    Units.
    """
    state = arena(decks)
    player = state.turn_player
    gear = put(state, player, WARMOGS, bf_location(0))
    state.battlefields[0].scored_by.clear()

    state._score(player, state.battlefields[0], "Conquer")
    settle(state)

    assert state.cards[gear].buffs == 0, "a loose Equipment's worn text fired"


def test_an_attached_equipment_does_fire_its_effect_text(decks):
    """718.3 -- while attached, the Effect Text is appended to the Top-Most
    Card's rules text. The counterweight: 724 must not silence it always."""
    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    gear = put(state, player, WARMOGS, bf_location(0))
    state.cards[gear].attached_to = host
    state.battlefields[0].scored_by.clear()

    state._score(player, state.battlefields[0], "Conquer")
    settle(state)

    assert state.cards[host].buffs == 1


# --- 722: Inactive text is still *present* ----------------------------------


def test_inactive_text_still_carries_its_keywords(decks):
    """722.1 -- "Cards with Inactive text still have keywords for the sake of
    Game Effects that want to reference or see if a card has a keyword."

    722.2 adds that a spell reading "Destroy a gear with [Temporary]" can
    still choose one whose text is Inactive. So going quiet must not change
    what the card *is*.
    """
    from cards.gear import equipment_profile

    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    gear = put(state, player, WARMOGS, bf_location(0))
    card = DB[state.cards[gear].card_id]

    state.cards[gear].attached_to = host
    assert equipment_profile(card) is not None, "still an Equipment while worn"
    assert card.keywords == DB[WARMOGS].keywords


def test_a_worn_equipment_still_lends_its_might_bonus(decks):
    """718.4 -- the Might Bonus is not an ability and does not go Inactive."""
    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    gear = put(state, player, WARMOGS, bf_location(0))

    before = state.might_of(state.cards[host])
    state.cards[gear].attached_to = host
    assert state.might_of(state.cards[host]) > before


# --- 719.3.a: attachments travel with the host, on every path ---------------


def test_attachments_follow_a_host_recalled_from_combat(decks):
    """719.3 -- "A Top-Most Card and all cards Attached to it are at the same
    location", and 719.3.a moves them together.

    The Standard Move path carried attachments; the combat recall at
    466.1.a.2 did not, so an attacker sent home at the end of an unresolved
    combat left its Equipment standing on the battlefield. Attached cards
    cannot move on their own (718.5.c), so it was stranded there.

    Exactly the shape of the earlier 719.5 bug -- a rule enforced in one code
    path out of several -- which is the argument for routing every location
    change through one place.
    """
    from engine.state import BASE_LOCATION

    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    gear = put(state, player, WARMOGS, bf_location(0))
    state.cards[gear].attached_to = host

    state.move_to(state.cards[host], BASE_LOCATION)

    assert state.cards[host].location == BASE_LOCATION
    assert state.cards[gear].location == BASE_LOCATION, (
        "the Equipment was left behind at the battlefield"
    )


def test_moving_a_host_does_not_drag_an_unattached_gear(decks):
    """The counterweight: only *attached* cards travel."""
    from engine.state import BASE_LOCATION

    state = arena(decks)
    player = state.turn_player
    host = put(state, player, "OGN-142", bf_location(0))
    loose = put(state, player, WARMOGS, bf_location(0))

    state.move_to(state.cards[host], BASE_LOCATION)

    assert state.cards[loose].location == bf_location(0)
