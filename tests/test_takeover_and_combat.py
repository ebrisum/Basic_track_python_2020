"""Taking a battlefield off the player who holds it, and the Might/damage
mechanics that decide whether the attempt works.

A battlefield is never permanently owned. 450 lets a unit move onto one the
opponent controls, which Contests it; from there either a Non-Combat Showdown
(348.2.a) or a Combat (466.5) hands Control to whoever is left standing.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import AssignDamageTo, PassPhase, StandardMove
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


def put_unit(state, player, card_id, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player,
        location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


def settle(state, limit=40):
    """Pass and assign until the board stops asking for decisions."""
    for _ in range(limit):
        if state.is_terminal():
            return
        if state.phase is Phase.COMBAT_ASSIGN:
            damage = [a for a in state.legal_actions() if isinstance(a, AssignDamageTo)]
            if damage:
                state.apply(damage[0])
                continue
        if PassPhase() in state.legal_actions():
            state.apply(PassPhase())
            continue
        return


# --- taking a held battlefield ---------------------------------------------


def test_an_undefended_battlefield_changes_hands(decks):
    """348.2.a -- the holder left nothing behind, so walking in takes it."""
    state = arena(decks)
    taker = state.turn_player
    holder = state.opponent(taker)
    bf = state.battlefields[0]
    bf.controller = holder

    mine = put_unit(state, taker, "OGN-175")
    state.apply(StandardMove(mine, bf_location(0)))
    assert bf.contested and bf.contested_by == taker   # 450
    settle(state)
    assert bf.controller == taker


def test_taking_a_battlefield_scores_a_conquer(decks):
    """466.5.d / 469.1 -- establishing Control is a Conquer."""
    state = arena(decks)
    taker = state.turn_player
    state.battlefields[0].controller = state.opponent(taker)
    before = state.players[taker].points

    mine = put_unit(state, taker, "OGN-175")
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)
    assert state.players[taker].points > before


def test_a_defended_battlefield_must_be_fought_for(decks):
    """344.1 -- units from two players stage a Combat, not a free takeover."""
    state = arena(decks)
    taker = state.turn_player
    holder = state.opponent(taker)
    bf = state.battlefields[0]
    bf.controller = holder
    put_unit(state, holder, "OGN-175", bf_location(0))

    mine = put_unit(state, taker, "OGN-175")
    state.apply(StandardMove(mine, bf_location(0)))
    assert state.showdown is not None and state.showdown.is_combat


def test_a_bigger_attacker_kills_the_garrison_and_takes_over(decks):
    """466.5 -- only the attacker is left standing, so Control changes."""
    state = arena(decks)
    taker = state.turn_player
    holder = state.opponent(taker)
    bf = state.battlefields[0]
    bf.controller = holder
    theirs = put_unit(state, holder, "OGN-175", bf_location(0))       # 3 Might

    mine = put_unit(state, taker, "OGN-142")                          # 10 Might
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)

    assert state.cards[theirs].location is None    # killed
    assert bf.controller == taker


def test_the_holder_keeps_the_battlefield_when_the_attack_bounces(decks):
    """466.1.a.2 -- both sides still standing, so attackers are Recalled and
    Control does not move."""
    state = arena(decks)
    taker = state.turn_player
    holder = state.opponent(taker)
    bf = state.battlefields[0]
    bf.controller = holder
    put_unit(state, holder, "OGN-142", bf_location(0))                # 10 Might

    mine = put_unit(state, taker, "OGN-175")                          # 3 Might
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)

    assert bf.controller == holder
    assert state.cards[mine].location in (BASE_LOCATION, None)


def test_a_defender_who_wins_an_uncontrolled_battlefield_takes_it(decks):
    """466.5.e -- "This does not have to be the player that applied
    Contested." A defender left standing establishes Control too."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    bf = state.battlefields[0]
    assert bf.controller is None

    put_unit(state, defender, "OGN-142", bf_location(0))              # 10 Might
    mine = put_unit(state, attacker, "OGN-175")                       # 3 Might
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)

    assert state.cards[mine].location is None                         # died
    assert bf.controller == defender


def test_a_mutual_wipe_leaves_the_battlefield_uncontrolled(decks):
    """466.5.b -- no units remaining here, so the Battlefield becomes
    Uncontrolled rather than staying with its previous holder."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    bf = state.battlefields[0]
    bf.controller = defender
    put_unit(state, defender, "OGN-175", bf_location(0))              # 3 Might
    mine = put_unit(state, attacker, "OGN-175")                       # 3 Might
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)

    assert not state.units_at(bf_location(0))
    assert bf.controller is None


# --- Might and damage ------------------------------------------------------


def test_damage_below_might_is_not_lethal(decks):
    """142.4.a -- lethal damage is nonzero damage >= Might."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")                              # 10 Might
    state.cards[unit].damage = state.might_of(state.cards[unit]) - 1
    state._resolve_lethal_damage()
    assert state.cards[unit].location == BASE_LOCATION


def test_damage_equal_to_might_is_lethal(decks):
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142")
    state.cards[unit].damage = state.might_of(state.cards[unit])
    state._resolve_lethal_damage()
    assert state.cards[unit].location is None


def test_combat_heals_every_unit(decks):
    """466.1.a.1 -- the Combat Cleanup inserts "Heal all Units", so damage
    that did not kill does not carry to the next combat."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    survivor = put_unit(state, defender, "OGN-142", bf_location(0))   # 10 Might
    mine = put_unit(state, attacker, "OGN-175")                       # 3 Might
    state.apply(StandardMove(mine, bf_location(0)))
    settle(state)
    assert state.cards[survivor].damage == 0


def test_assault_raises_might_only_while_attacking(decks):
    """807 -- +X Might while I'm an attacker."""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-021A" if "OGN-021A" in DB.cards else "OGN-142")
    ref = state.cards[unit]
    base = state.might_of(ref)
    ref.is_attacker = True
    assert state.might_of(ref) == base + DB[ref.card_id].assault


def test_shield_raises_might_only_while_defending(decks):
    """814.1.c -- "While I am a defender, I have +X [M]"."""
    shielded = next(
        (c for c in DB.cards.values() if c.type == "unit" and c.shield), None
    )
    assert shielded is not None, "no unit with Shield in the database"
    state = arena(decks)
    unit = put_unit(state, 0, shielded.card_id)
    ref = state.cards[unit]
    base = state.might_of(ref)

    ref.is_defender = True
    assert state.might_of(ref) == base + shielded.shield
    ref.is_defender = False
    ref.is_attacker = True
    assert state.might_of(ref) == base + shielded.assault


def test_shield_can_save_a_defender_from_a_lethal_attack(decks):
    """The point of Shield: it changes who dies, not just a displayed number."""
    shielded = next(
        (c for c in DB.cards.values()
         if c.type == "unit" and c.shield and c.might > 0), None
    )
    assert shielded is not None
    state = arena(decks)
    unit = put_unit(state, 0, shielded.card_id)
    ref = state.cards[unit]
    ref.is_defender = True
    ref.damage = shielded.might          # lethal without Shield
    state._resolve_lethal_damage()
    assert state.cards[unit].location == BASE_LOCATION


# --- 323.6 / 190.4.c: control needs a garrison --------------------------------


def test_control_is_lost_when_the_last_unit_leaves(decks):
    """323.6 (cleanup step 4) and 190.4.c -- "If a player has no Units at a
    Battlefield and the turn is in an Open state, they lose Control of that
    Battlefield in the following cleanup unless there is a Combat or Showdown
    ongoing there."

    190.4.a states the same from the other side: a player maintains control
    "for as long as they have Units at that Battlefield". The engine granted
    control permanently once taken, which made a battlefield free to hold and
    scored a Hold point every turn from an empty field.
    """
    state = arena(decks)
    holder = state.turn_player
    unit = put_unit(state, holder, "OGN-142", bf_location(0))
    state.battlefields[0].controller = holder

    state._cleanup()
    assert state.battlefields[0].controller == holder, "a garrison keeps control"

    state.cards[unit].location = BASE_LOCATION
    state._cleanup()
    assert state.battlefields[0].controller is None


def test_control_survives_while_a_combat_is_running_there(decks):
    """190.4.b -- "While a Combat or Showdown is ongoing at a Battlefield,
    Control of that Battlefield cannot change until instructed by steps of the
    Combat or Showdown"."""
    from engine.chain import Showdown

    state = arena(decks)
    holder = state.turn_player
    state.battlefields[0].controller = holder     # no units there at all
    state.showdown = Showdown(battlefield=0, attacker=state.opponent(holder),
                              defender=holder, is_combat=True)
    state.combat = state.showdown

    state._cleanup()
    assert state.battlefields[0].controller == holder

    state.showdown = state.combat = None
    state._cleanup()
    assert state.battlefields[0].controller is None


def test_control_survives_a_closed_state(decks):
    """323.6 and 190.4.c both say "if the turn is in an Open State". A chain
    on the stack is a Closed State, so control is not checked yet -- which is
    what lets a Reaction put a unit back before control is lost."""
    from engine.chain import ChainItem

    state = arena(decks)
    holder = state.turn_player
    state.battlefields[0].controller = holder
    state.chain.append(
        ChainItem(kind="card", instance_id=max(state.cards), controller=holder,
                  pending=False)
    )

    state._cleanup()
    assert state.battlefields[0].controller == holder, "a Closed state defers step 4"


def test_an_empty_battlefield_scores_no_hold(decks):
    """469.2 -- Hold requires maintaining Control. With 323.6 enforced, an
    undefended battlefield is not controlled by the Beginning Phase, so it
    cannot be Held. This is the rule that makes garrisoning a real cost."""
    state = arena(decks)
    holder = state.turn_player
    state.battlefields[0].controller = holder
    state.players[holder].points = 0

    state._cleanup()                 # 323.6 takes it away first
    state._phase_beginning()

    assert state.players[holder].points == 0
