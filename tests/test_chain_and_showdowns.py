"""The Chain, priority, focus, showdowns and combat (307-348, 459-466).

The project brief flagged "response windows and priority passing during
showdowns" and "exact timing of battlefield hold/conquer scoring" as the places
it expected trouble. This file is the net under both.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from engine.actions import ActivateAbility, ChooseTarget, PassPhase, PlayCard, StandardMove
from engine.chain import can_play, resolves_immediately, timing_label
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


def give(state, player, card_id):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    return instance_id


def put_unit(state, player, card_id, location=BASE_LOCATION):
    instance_id = give(state, player, card_id)
    state.players[player].hand.remove(instance_id)
    state.cards[instance_id].location = location
    state.players[player].base.append(instance_id)
    return instance_id


# --- timing: the four-state machine (307-310) -------------------------------


def test_timing_label_names_all_four_states(decks):
    state = arena(decks)
    assert state.timing() == "Neutral Open"
    state.chain.append(object())
    assert state.timing() == "Neutral Closed"
    state.chain.clear()
    state.showdown = type("S", (), {"battlefield": 0})()
    assert state.timing() == "Showdown Open"
    state.chain.append(object())
    assert state.timing() == "Showdown Closed"


def test_only_reactions_are_playable_in_a_closed_state():
    """309.1.a."""
    action_card, reaction_card, vanilla = DB["OGN-004"], DB["OGN-133"], DB["OGN-142"]
    chain = [object()]
    assert not can_play(action_card, 0, 0, chain, None)
    assert can_play(reaction_card, 0, 0, chain, None)
    assert not can_play(vanilla, 0, 0, chain, None)


def test_actions_and_reactions_are_playable_in_a_showdown():
    """308.1.a."""
    showdown = object()
    assert can_play(DB["OGN-004"], 0, 0, [], showdown)
    assert can_play(DB["OGN-133"], 0, 0, [], showdown)
    assert not can_play(DB["OGN-142"], 0, 0, [], showdown), "units are Neutral-Open only"


def test_units_and_gear_resolve_immediately():
    """337.2."""
    assert resolves_immediately(DB["OGN-142"])
    assert resolves_immediately(DB["OGN-040"])
    assert not resolves_immediately(DB["OGN-004"])


# --- the chain (327-340) ----------------------------------------------------


def test_playing_a_spell_puts_it_on_the_chain_and_closes_the_state(decks):
    """354 -- moving the card to the Chain Closes the State."""
    state = arena(decks)
    spell = give(state, state.turn_player, "OGN-004")
    state._current_player = state.turn_player
    state.apply(PlayCard(spell))

    assert len(state.chain) == 1
    assert state.timing() == "Neutral Closed"
    assert state.phase is Phase.CHAIN


def test_the_opponent_gets_priority_after_a_spell_is_finalized(decks):
    """337.4/338 -- the next player gains Priority."""
    state = arena(decks)
    caster = state.turn_player
    state.apply(PlayCard(give(state, caster, "OGN-004")))
    assert state.current_player == state.opponent(caster)
    assert state.priority == state.opponent(caster)


def test_a_spell_resolves_only_after_all_players_pass(decks):
    """339-340 -- all pass in sequence, then the newest item resolves."""
    state = arena(decks)
    caster = state.turn_player
    put_unit(state, caster, "OGN-142", bf_location(0))
    state.apply(PlayCard(give(state, caster, "OGN-004")))

    assert state.chain, "still on the chain after one player has priority"
    state.apply(PassPhase())          # opponent passes
    assert state.chain, "one pass is not enough"
    state.apply(PassPhase())          # caster passes -> resolve
    assert not state.chain


def test_the_chain_resolves_newest_first(decks):
    """340.1 -- LIFO. Gust bounces the unit Hextech Ray was aimed at."""
    state = arena(decks)
    caster = state.turn_player
    other = state.opponent(caster)
    victim = put_unit(state, other, "OGN-197", bf_location(0))  # 1 Might

    state.apply(PlayCard(give(state, caster, "OGN-009")))  # Hextech Ray, deal 3
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))
    # The opponent responds with Gust (a Reaction) while the ray is on the chain.
    gust = give(state, other, "OGN-169")
    assert PlayCard(gust) in state.legal_actions(), "Reactions are legal in a Closed state"
    state.apply(PlayCard(gust))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))

    for _ in range(8):
        if not state.chain:
            break
        if PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        elif state.phase is Phase.CHOOSING:
            state.apply(state.legal_actions()[0])

    # Gust resolved first (newest), so the unit left before the ray landed.
    assert victim in state.players[other].hand
    assert state.cards[victim].damage == 0


def test_a_reaction_can_be_played_into_a_chain_but_an_action_cannot(decks):
    state = arena(decks)
    caster = state.turn_player
    other = state.opponent(caster)
    state.apply(PlayCard(give(state, caster, "OGN-004")))

    reaction = give(state, other, "OGN-133")   # Flurry of Blades, REACTION
    action = give(state, other, "OGN-004")     # Cleave, ACTION
    legal = state.legal_actions()
    assert PlayCard(reaction) in legal
    assert PlayCard(action) not in legal


def test_standard_move_is_illegal_in_a_closed_state(decks):
    """144.1.b -- the Standard Move cannot be performed in a Closed State."""
    state = arena(decks)
    mover = state.turn_player
    unit = put_unit(state, mover, "OGN-142", BASE_LOCATION)
    assert any(isinstance(a, StandardMove) for a in state.legal_actions())

    state.apply(PlayCard(give(state, mover, "OGN-004")))
    assert not any(isinstance(a, StandardMove) for a in state.legal_actions())


def test_rune_abilities_stay_available_inside_a_chain(decks):
    """164.2 -- both rune abilities are Reactions, so they work in a Closed state."""
    state = arena(decks)
    caster = state.turn_player
    assert state.players[caster].channeled_runes, "caster needs runes on the board"
    state.apply(PlayCard(give(state, caster, "OGN-004")))
    # Priority is with the opponent; pass it back to the caster, who has runes.
    state.apply(PassPhase())
    assert state.chain, "the spell is still on the chain"
    reprs = [repr(a) for a in state.legal_actions()]
    assert any(r.startswith(("tap_energy:", "recycle_power:")) for r in reprs)


def test_an_add_ability_resolves_without_passing_priority(decks):
    """337.2 -- an ability that Adds resources resolves immediately."""
    state = arena(decks)
    player = state.turn_player
    seal = put_unit(state, player, "OGN-040", BASE_LOCATION)
    state.players[player].pool.clear()
    state._current_player = player
    state.apply(ActivateAbility(seal, 0))
    assert not state.chain
    assert state.players[player].pool.power.get("Fury", 0) == 1


# --- showdowns (341-348) ----------------------------------------------------


def contest(state, mover: int, bf_index: int = 0):
    """Move a unit onto a battlefield the mover does not control."""
    unit = put_unit(state, mover, "OGN-142", BASE_LOCATION)
    state.turn_player = mover
    state._current_player = mover
    state.apply(StandardMove(unit, bf_location(bf_index)))
    return unit


def test_moving_onto_a_battlefield_contests_it_and_opens_a_showdown(decks):
    """450 / 344.2 -- Contested, then a Non-Combat Showdown at the Cleanup."""
    state = arena(decks)
    mover = state.turn_player
    contest(state, mover)
    assert state.showdown is not None
    assert state.showdown.is_combat is False
    assert state.phase is Phase.SHOWDOWN


def test_the_contesting_player_gains_focus(decks):
    """345 -- the player who applied Contested status gains Focus."""
    state = arena(decks)
    mover = state.turn_player
    contest(state, mover)
    assert state.focus == mover
    assert state.priority == mover, "gaining Focus also grants Priority (313.2)"
    assert state.current_player == mover


def test_all_players_passing_ends_the_showdown_and_settles_control(decks):
    """347.2.a -- all pass in sequence, the Showdown ends."""
    state = arena(decks)
    mover = state.turn_player
    contest(state, mover)
    state.apply(PassPhase())
    assert state.showdown is not None, "one pass is not enough"
    assert state.focus == state.opponent(mover), "347.2.b -- Focus passes"
    state.apply(PassPhase())
    assert state.showdown is None
    assert state.battlefields[0].controller == mover
    assert not state.battlefields[0].contested


def test_opposing_units_open_a_combat_showdown(decks):
    """344.1 / 460 -- units from two players stage a Combat."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    put_unit(state, defender, "OGN-175", bf_location(0))
    contest(state, attacker)

    assert state.showdown is not None
    assert state.showdown.is_combat is True
    assert state.showdown.attacker == attacker
    assert state.focus == attacker  # 464.2.d


def test_units_at_a_combat_gain_attacker_and_defender_designations(decks):
    """464.2.c.3."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    theirs = put_unit(state, defender, "OGN-175", bf_location(0))
    mine = contest(state, attacker)

    assert state.cards[mine].is_attacker and not state.cards[mine].is_defender
    assert state.cards[theirs].is_defender and not state.cards[theirs].is_attacker


def test_combat_showdown_closes_into_the_damage_step(decks):
    """464 -> 465 -- both players pass, damage is assigned."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    put_unit(state, defender, "OGN-175", bf_location(0))  # 3 Might
    contest(state, attacker)                              # 10 Might drake

    state.apply(PassPhase())
    state.apply(PassPhase())
    assert state.showdown is None
    # The attacker has Might to assign, so the engine asks for an assignment.
    assert state.phase is Phase.COMBAT_ASSIGN or state.combat is None


def test_a_full_combat_kills_the_defender_and_conquers(decks):
    """465-466 -- lethal damage, no defenders left, battlefield Conquered."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    victim = put_unit(state, defender, "OGN-175", bf_location(0))  # 3 Might
    contest(state, attacker)                                       # 10 Might

    for _ in range(12):
        if state.is_terminal() or (state.showdown is None and state.combat is None
                                   and state.phase is Phase.MAIN):
            break
        state.apply(state.legal_actions()[0])

    assert state.cards[victim].location is None, "defender took lethal damage"
    assert state.battlefields[0].controller == attacker


def test_spells_are_playable_during_a_showdown(decks):
    """308.1.a -- Action and Reaction cards only."""
    state = arena(decks)
    mover = state.turn_player
    contest(state, mover)
    action = give(state, mover, "OGN-004")     # Cleave, ACTION
    vanilla = give(state, mover, "OGN-142")    # a unit
    legal = state.legal_actions()
    assert PlayCard(action) in legal
    assert PlayCard(vanilla) not in legal


def test_focus_does_not_pass_when_the_chain_opened_from_an_add_ability(decks):
    """346.1 -- Add abilities do not pass Focus."""
    state = arena(decks)
    mover = state.turn_player
    seal = put_unit(state, mover, "OGN-040", BASE_LOCATION)
    contest(state, mover)
    assert state.focus == mover
    state.players[mover].pool.clear()
    state.apply(ActivateAbility(seal, 0))
    assert state.focus == mover, "focus stays with the player who added resources"


# --- the window never gets stuck --------------------------------------------


def test_phase_never_disagrees_with_the_chain(decks):
    """A Closed state with an empty chain is a dead position."""
    state = build_state(decks[0], decks[1], seed=3, db=DB)
    agent = RandomAgent(3)
    steps = 0
    while not state.is_terminal() and steps < 4000:
        state.apply(agent.act(state))
        steps += 1
        if state.is_terminal():
            break
        if state.phase is Phase.CHAIN:
            assert state.chain, "CHAIN phase with an empty chain"
        if state.phase is Phase.SHOWDOWN:
            assert state.showdown is not None
        assert state.legal_actions(), "no legal actions in a non-terminal state"


@pytest.mark.parametrize("seed", range(8))
def test_games_still_finish_with_the_chain_in_place(decks, seed):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    steps = 0
    while not state.is_terminal():
        assert steps < 40_000, "game failed to terminate"
        state.apply(agent.act(state))
        steps += 1
    assert sum(state.returns()) == pytest.approx(1.0)


# --- ACCELERATE (805) and entry state (143.4) -------------------------------


def test_units_enter_the_board_exhausted(decks):
    """143.4 -- the default, which is what makes ACCELERATE worth paying."""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    unit = give(state, player, "OGN-142")   # Mountain Drake, no Accelerate
    state.apply(PlayCard(unit))
    assert state.cards[unit].exhausted


def test_accelerate_is_offered_as_a_separate_line(decks):
    """805.2 -- an optional additional cost, so both lines are legal."""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    unit = give(state, player, "OGN-010")   # Legion Rearguard, ACCELERATE
    legal = state.legal_actions()
    assert PlayCard(unit) in legal
    assert PlayCard(unit, accelerate=True) in legal


def test_paying_accelerate_makes_the_unit_enter_ready(decks):
    """805.1.a -- 'If you do, I enter ready.'"""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    unit = give(state, player, "OGN-010")
    state.apply(PlayCard(unit, accelerate=True))
    assert not state.cards[unit].exhausted


def test_accelerate_costs_one_more_energy_and_one_power(decks):
    """805.1.a -- [1][C] on top of the printed cost."""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    card = DB["OGN-010"]
    pool = state.players[player].pool
    before_energy = pool.energy
    before_power = pool.total_power()
    state.apply(PlayCard(give(state, player, "OGN-010"), accelerate=True))
    assert pool.energy == before_energy - (card.energy + 1)
    assert pool.total_power() == before_power - (card.power + 1)


def test_accelerate_is_not_offered_when_the_extra_cost_is_unaffordable(decks):
    """201-204 -- an unpayable cost makes the line illegal, not a trap."""
    state = arena(decks)
    player = state.turn_player
    state._current_player = player
    unit = give(state, player, "OGN-010")
    card = DB["OGN-010"]
    # Exactly the printed cost, nothing spare for the [1][C].
    state.players[player].pool.clear()
    state.players[player].pool.energy = card.energy
    for _ in range(card.power):
        state.players[player].pool.universal_power += 1
    legal = state.legal_actions()
    assert PlayCard(unit) in legal
    assert PlayCard(unit, accelerate=True) not in legal


def test_accelerate_power_must_match_a_domain_of_the_unit(decks):
    """805.1.a.1 -- Fury unit needs Fury power, not just any power."""
    card = DB["OGN-010"]
    assert card.domains, "this test needs a domained unit"
    assert card.accelerate_power_domains == [card.domains[0]]


def test_a_reaction_can_answer_a_reaction(decks):
    """309.1.a -- the chain can stack Reactions while resources allow."""
    state = arena(decks)
    caster = state.turn_player
    other = state.opponent(caster)
    put_unit(state, other, "OGN-142", bf_location(0))

    state.apply(PlayCard(give(state, caster, "OGN-009")))   # Hextech Ray
    if state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])
    first = give(state, other, "OGN-133")                   # Flurry, REACTION
    state.apply(PlayCard(first))
    if state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])
    second = give(state, caster, "OGN-169")                 # Gust, REACTION
    assert PlayCard(second) in state.legal_actions(), "a Reaction answers a Reaction"
    state.apply(PlayCard(second))
    assert len(state.chain) >= 3, "three items stacked on the chain"


# --- gear on the board: attachment, movement, recall (457, 718-719) ---------


def put_gear(state, player, card_id, location=BASE_LOCATION):
    instance_id = give(state, player, card_id)
    state.players[player].hand.remove(instance_id)
    state.cards[instance_id].location = location
    state.players[player].base.append(instance_id)
    return instance_id


def test_attached_gear_moves_with_its_host(decks):
    """719.3 -- a Top-Most Card and everything attached to it are at the same
    location, and 719.3.a moves them together."""
    state = arena(decks)
    player = state.turn_player
    host = put_unit(state, player, "OGN-175")
    gear = put_gear(state, player, "SFD-124")
    state.cards[gear].attached_to = host

    state.apply(StandardMove(host, bf_location(0)))
    assert state.cards[gear].location == bf_location(0)


def test_gear_can_never_be_moved_on_its_own(decks):
    """718.5.c -- Attached cards cannot be moved separately, and unattached
    gear has no Standard Move either: 144 moves units."""
    state = arena(decks)
    player = state.turn_player
    gear = put_gear(state, player, "SFD-124")
    assert not [
        a for a in state.legal_actions()
        if isinstance(a, StandardMove) and a.instance_id == gear
    ]


def test_unattached_gear_at_a_battlefield_is_recalled(decks):
    """457.1 -- an un-attached non-unit Gear at a battlefield is Recalled to
    its controller's base during the next Cleanup."""
    state = arena(decks)
    player = state.turn_player
    gear = put_gear(state, player, "SFD-124", bf_location(0))
    state._cleanup()
    assert state.cards[gear].location == BASE_LOCATION


def test_attached_gear_at_a_battlefield_stays(decks):
    """457.1 applies only to *un-attached* gear -- an Equipment on a unit at
    a battlefield is present there legitimately."""
    state = arena(decks)
    player = state.turn_player
    host = put_unit(state, player, "OGN-175", bf_location(0))
    gear = put_gear(state, player, "SFD-124", bf_location(0))
    state.cards[gear].attached_to = host
    state._cleanup()
    assert state.cards[gear].location == bf_location(0)


def test_gear_is_not_counted_as_a_unit_at_a_battlefield(decks):
    """A gear present at a battlefield must not hold it or fight for it."""
    state = arena(decks)
    player = state.turn_player
    put_gear(state, player, "SFD-124", bf_location(0))
    assert state.units_at(bf_location(0)) == []


# --- late arrivals at a combat (464.2.c.3.a) -------------------------------


def test_a_unit_arriving_mid_combat_gains_its_designation_at_cleanup(decks):
    """464.2.c.3.a -- a unit that becomes present after Attacker and Defender
    are established gains the designation during the following Cleanup."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    put_unit(state, defender, "OGN-175", bf_location(0))
    contest(state, attacker)
    assert state.combat is not None or state.showdown is not None

    latecomer = put_unit(state, attacker, "OGN-175", bf_location(0))
    assert not state.cards[latecomer].is_attacker  # not yet -- 464.2.c.3.a
    state._cleanup()
    assert state.cards[latecomer].is_attacker
    assert not state.cards[latecomer].is_defender


def test_a_late_defender_gains_the_defender_designation(decks):
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    put_unit(state, defender, "OGN-175", bf_location(0))
    contest(state, attacker)

    latecomer = put_unit(state, defender, "OGN-175", bf_location(0))
    state._cleanup()
    assert state.cards[latecomer].is_defender
    assert not state.cards[latecomer].is_attacker


def test_designations_are_dropped_when_a_unit_leaves_the_battlefield(decks):
    """323.2.c -- units elsewhere lose Attacker/Defender designations."""
    state = arena(decks)
    attacker = state.turn_player
    defender = state.opponent(attacker)
    put_unit(state, defender, "OGN-175", bf_location(0))
    mine = contest(state, attacker)
    assert state.cards[mine].is_attacker

    state.cards[mine].location = BASE_LOCATION
    state._cleanup()
    assert not state.cards[mine].is_attacker
