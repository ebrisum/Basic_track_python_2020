"""One test per scripted card, asserting its effect on a constructed state.

The brief: "No card is done without a unit test asserting its effect on a
constructed state." These build a real game, force the card into hand with the
resources to play it, then check what the printed text says should happen.
"""

from __future__ import annotations

import pytest

from cards.database import load as load_db
from cards.dsl import TriggerKind
from cards.scripts import registry, script_for
from engine.actions import ActivateAbility, ChooseTarget, PassPhase, PlayCard
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION

DB = load_db()


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    """A state in Main Phase with both players' pools filled."""
    from agents.random_agent import RandomAgent

    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    for player in state.players:
        player.pool.energy = 20
        player.pool.universal_power = 20
    return state


def give(state, player: int, card_id: str) -> int:
    """Mint a copy of `card_id` into `player`'s hand and return its id."""
    from engine.zones import CardRef

    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player, controller=player
    )
    state.players[player].hand.append(instance_id)
    return instance_id


def put_unit(state, player: int, card_id: str, location: str = BASE_LOCATION) -> int:
    instance_id = give(state, player, card_id)
    state.players[player].hand.remove(instance_id)
    ref = state.cards[instance_id]
    ref.location = location
    state.players[player].base.append(instance_id)
    return instance_id


def play(state, instance_id: int) -> None:
    """Play a card and let it resolve.

    Spells now go on the Chain (354) and only resolve once every player has
    passed priority in sequence (339-340), so these tests pass priority for
    both players rather than assuming immediate resolution. Choices raised
    mid-resolution are left for the caller to answer.
    """
    controller = state.cards[instance_id].controller
    state._current_player = controller
    state.turn_player = controller
    state.apply(PlayCard(instance_id))
    pass_until_resolved(state)


def pass_until_resolved(state, limit: int = 12) -> None:
    """Pass priority until the chain drains or a choice is requested."""
    for _ in range(limit):
        if state.phase is Phase.CHOOSING or not state.chain:
            return
        if PassPhase() in state.legal_actions():
            state.apply(PassPhase())
        else:
            return


# --- registry hygiene -------------------------------------------------------


def test_every_script_targets_a_real_card():
    for card_id in registry():
        assert card_id in DB, f"{card_id} is not in cards.json"


def test_every_starter_deck_card_has_a_script_entry(decks):
    import json

    for slug in ("jinx_chaos_fury", "volibear_body_fury"):
        deck = json.loads((__import__("pathlib").Path(f"decks/{slug}.json")).read_text())
        for card_id in set(deck["main"]) | {deck["champion"]}:
            assert script_for(card_id) is not None, (
                f"{card_id} ({DB[card_id].name}) is in a starter deck with no "
                f"script entry -- add one, even if empty, so coverage is explicit"
            )


def test_incomplete_scripts_explain_themselves():
    for script in registry().values():
        if not script.complete:
            assert script.note, f"{script.card_id} is incomplete but says nothing"


# --- units ------------------------------------------------------------------


def test_teemo_buffs_itself_on_play(decks):
    """OGN-197: When you play me, give me +3 Might this turn."""
    state = arena(decks)
    instance_id = give(state, 0, "OGN-197")
    play(state, instance_id)
    ref = state.cards[instance_id]
    assert state.might_of(ref) == DB["OGN-197"].might + 3


def test_teemo_buff_expires_at_end_of_turn(decks):
    """426/701 -- a 'this turn' buff does not persist."""
    state = arena(decks)
    instance_id = give(state, 0, "OGN-197")
    play(state, instance_id)
    state._phase_ending()
    assert state.might_of(state.cards[instance_id]) == DB["OGN-197"].might


def test_chemtech_enforcer_discards_on_play(decks):
    """OGN-003: When you play me, discard 1."""
    state = arena(decks)
    state.players[0].hand = state.players[0].hand[:1]  # single card -> no choice
    instance_id = give(state, 0, "OGN-003")
    before = len(state.players[0].hand)
    trash_before = len(state.players[0].trash)
    play(state, instance_id)
    assert len(state.players[0].hand) == before - 2  # the card played + discarded
    assert len(state.players[0].trash) == trash_before + 1


def test_vi_activated_ability_recycles_and_buffs(decks):
    """OGN-036: Recycle 1 from your trash: Give me +1 Might this turn."""
    state = arena(decks)
    instance_id = put_unit(state, 0, "OGN-036")
    state.players[0].trash.append(give(state, 0, "OGN-142"))
    state.players[0].hand.pop()
    trash_before = len(state.players[0].trash)

    state._current_player = 0
    state.turn_player = 0
    state.apply(ActivateAbility(instance_id, 0))
    pass_until_resolved(state)

    assert len(state.players[0].trash) == trash_before - 1
    assert state.might_of(state.cards[instance_id]) == DB["OGN-036"].might + 1


def test_vi_ability_is_illegal_with_an_empty_trash(decks):
    """416.3 -- a cost that cannot be completed makes the ability illegal."""
    state = arena(decks)
    instance_id = put_unit(state, 0, "OGN-036")
    state.players[0].trash.clear()
    state._current_player = 0
    state.turn_player = 0
    assert ActivateAbility(instance_id, 0) not in state.legal_actions()


# --- spells -----------------------------------------------------------------


def test_hextech_ray_deals_3_to_a_chosen_unit(decks):
    """OGN-009: Deal 3 to a unit at a battlefield."""
    state = arena(decks)
    victim = put_unit(state, 1, "OGN-142", bf_location(0))  # 10 Might, survives
    spell = give(state, 0, "OGN-009")
    play(state, spell)
    assert state.cards[victim].damage == 3


def test_damage_at_or_above_might_kills_in_the_cleanup(decks):
    """142.4.a / 323.3a -- 3 damage on a 3-Might unit is lethal."""
    state = arena(decks)
    victim = put_unit(state, 1, "OGN-175", bf_location(0))  # Might 3
    play(state, give(state, 0, "OGN-009"))
    assert state.cards[victim].location is None
    assert victim in state.players[1].trash


def test_hextech_ray_asks_when_several_units_are_available(decks):
    """Both are Mountain Drakes (10 Might) so 3 damage is not lethal and the
    damage stays visible on the chosen one."""
    state = arena(decks)
    a = put_unit(state, 1, "OGN-142", bf_location(0))
    b = put_unit(state, 1, "OGN-142", bf_location(0))
    play(state, give(state, 0, "OGN-009"))

    assert state.phase is Phase.CHOOSING
    options = {act.instance_id for act in state.legal_actions()}
    assert options == {a, b}
    state.apply(ChooseTarget(b))
    assert state.cards[b].damage == 3
    assert state.cards[a].damage == 0
    assert state.phase is not Phase.CHOOSING


def test_hextech_ray_cannot_hit_units_at_a_base(decks):
    """The selector is 'at a battlefield' (location filter)."""
    state = arena(decks)
    safe = put_unit(state, 1, "OGN-142", BASE_LOCATION)
    play(state, give(state, 0, "OGN-009"))
    assert state.cards[safe].damage == 0


def test_flurry_of_blades_hits_every_unit_at_battlefields(decks):
    """OGN-133: Deal 1 to all units at battlefields."""
    state = arena(decks)
    mine = put_unit(state, 0, "OGN-142", bf_location(0))
    theirs = put_unit(state, 1, "OGN-175", bf_location(1))
    home = put_unit(state, 1, "OGN-142", BASE_LOCATION)
    play(state, give(state, 0, "OGN-133"))
    assert state.cards[mine].damage == 1
    assert state.cards[theirs].damage == 1
    assert state.cards[home].damage == 0, "units at a base are untouched"


def test_cleave_grants_assault_3_which_only_counts_when_attacking(decks):
    """OGN-004: Give a unit ASSAULT 3 this turn. (807)"""
    state = arena(decks)
    unit = put_unit(state, 0, "OGN-142", bf_location(0))
    play(state, give(state, 0, "OGN-004"))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(unit))
    pass_until_resolved(state)

    ref = state.cards[unit]
    base = DB["OGN-142"].might
    assert state.might_of(ref) == base
    ref.is_attacker = True
    assert state.might_of(ref) == base + 3


def test_gust_returns_a_small_unit_to_hand(decks):
    """OGN-169: Return a unit at a battlefield with 3 Might or less."""
    state = arena(decks)
    small = put_unit(state, 1, "OGN-197", bf_location(0))  # Might 1
    play(state, give(state, 0, "OGN-169"))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(small))
    pass_until_resolved(state)
    assert state.cards[small].location is None
    assert small in state.players[1].hand


def test_gust_cannot_return_a_big_unit(decks):
    """The '3 Might or less' filter must exclude Mountain Drake (10)."""
    state = arena(decks)
    big = put_unit(state, 1, "OGN-142", bf_location(0))
    play(state, give(state, 0, "OGN-169"))
    assert state.cards[big].location == bf_location(0)
    assert big not in state.players[1].hand


def test_confront_draws_and_sets_units_enter_ready(decks):
    """OGN-129: Units you play this turn enter ready. Draw 1."""
    state = arena(decks)
    before = len(state.players[0].hand)
    play(state, give(state, 0, "OGN-129"))
    # +1 minted into hand, -1 played, +1 drawn by the spell.
    assert len(state.players[0].hand) == before + 1
    assert 0 in state.units_enter_ready


def test_stacked_deck_keeps_one_and_recycles_the_rest(decks):
    """OGN-183: Look at the top 3, put 1 into hand, recycle the rest."""
    state = arena(decks)
    deck_before = len(state.players[0].main_deck)
    top3 = list(state.players[0].main_deck[:3])
    play(state, give(state, 0, "OGN-183"))
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(top3[1]))
    pass_until_resolved(state)
    assert top3[1] in state.players[0].hand
    assert len(state.players[0].main_deck) == deck_before - 1
    for other in (top3[0], top3[2]):
        assert other in state.players[0].main_deck[-3:], "recycled to the bottom"


def test_acceptable_losses_makes_each_player_kill_a_gear(decks):
    """OGN-179: Each player kills one of their gear (411.1)."""
    state = arena(decks)
    mine = put_unit(state, 0, "OGN-040", BASE_LOCATION)
    theirs = put_unit(state, 1, "OGN-163", BASE_LOCATION)
    play(state, give(state, 0, "OGN-179"))
    while state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])
    pass_until_resolved(state)
    assert state.cards[mine].location is None
    assert state.cards[theirs].location is None


# --- gear -------------------------------------------------------------------


@pytest.mark.parametrize(
    "card_id,domain",
    [
        ("OGN-040", "Fury"), ("SFD-222", "Fury"),
        ("OGN-163", "Body"), ("SFD-231", "Body"),
        ("OGN-204", "Chaos"), ("SFD-234", "Chaos"),
    ],
)
def test_seal_cycle_adds_power_of_its_domain(decks, card_id, domain):
    """429 Add -- 'Exhaust: REACTION - ADD <domain>'."""
    state = arena(decks)
    instance_id = put_unit(state, 0, card_id, BASE_LOCATION)
    state.players[0].pool.clear()
    state._current_player = 0
    state.turn_player = 0
    state.apply(ActivateAbility(instance_id, 0))
    assert state.players[0].pool.power.get(domain, 0) == 1
    assert state.cards[instance_id].exhausted, "exhausting is the cost"


def test_an_exhausted_seal_cannot_be_activated_again(decks):
    state = arena(decks)
    instance_id = put_unit(state, 0, "OGN-040", BASE_LOCATION)
    state._current_player = 0
    state.turn_player = 0
    state.apply(ActivateAbility(instance_id, 0))
    assert ActivateAbility(instance_id, 0) not in state.legal_actions()


def equip(state, gear: int, host: int) -> None:
    """Play a gear, then pay its Equip cost to attach it (818.1).

    Playing Equipment does *not* attach it -- Equip is a separate activated
    ability with its own cost. Only Quick-Draw attaches on play (819.1.d).
    """
    play(state, gear)
    equip_action = next(
        a for a in state.legal_actions()
        if isinstance(a, ActivateAbility) and a.instance_id == gear
    )
    state.apply(equip_action)
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(host))
    pass_until_resolved(state)


def test_playing_equipment_does_not_attach_it(decks):
    """818.1 -- Equip is an activated ability with a cost, not a play effect.

    This file used to script the attach as ON_PLAY, so Warmog's Armor
    equipped itself for free the moment it was played.
    """
    state = arena(decks)
    put_unit(state, 0, "OGN-142", BASE_LOCATION)
    gear = give(state, 0, "SFD-108")
    play(state, gear)
    assert state.cards[gear].attached_to is None


def test_warmogs_armor_attaches_when_its_equip_cost_is_paid(decks):
    """SFD-108: [EQUIP Body] (Body: Attach this to a unit you control.)"""
    state = arena(decks)
    host = put_unit(state, 0, "OGN-142", BASE_LOCATION)
    gear = give(state, 0, "SFD-108")
    equip(state, gear, host)
    assert state.cards[gear].attached_to == host


def test_equip_costs_are_actually_charged(decks):
    """818.1.b -- the cost is paid to attach. Body power buys Warmog's."""
    state = arena(decks)
    host = put_unit(state, 0, "OGN-142", BASE_LOCATION)
    gear = give(state, 0, "SFD-108")
    play(state, gear)
    before = state.players[0].pool.total_power()
    equip_action = next(
        a for a in state.legal_actions()
        if isinstance(a, ActivateAbility) and a.instance_id == gear
    )
    state.apply(equip_action)
    assert state.players[0].pool.total_power() == before - 1


def test_attached_gear_adds_its_might_bonus_to_the_host(decks):
    """718.4 / 137.3 -- the *Might Bonus*, not the card's `might` field."""
    from cards.gear import equipment_profile

    state = arena(decks)
    host = put_unit(state, 0, "OGN-197", BASE_LOCATION)
    base = state.might_of(state.cards[host])
    gear = give(state, 0, "SFD-124")
    equip(state, gear, host)
    bonus = equipment_profile(DB["SFD-124"]).might_bonus
    assert state.might_of(state.cards[host]) == base + bonus


def test_gear_detaches_when_its_host_dies(decks):
    state = arena(decks)
    host = put_unit(state, 0, "OGN-197", BASE_LOCATION)
    gear = give(state, 0, "SFD-108")
    play(state, gear)
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(host))
    pass_until_resolved(state)
    state._kill(state.cards[host])
    assert state.cards[gear].attached_to is None


def test_warmogs_buffs_permanently_on_conquer(decks):
    """SFD-108: When I conquer, buff me. The buff must survive the turn."""
    state = arena(decks)
    host = put_unit(state, 0, "OGN-142", BASE_LOCATION)
    gear = give(state, 0, "SFD-108")
    play(state, gear)
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(host))
    pass_until_resolved(state)

    bf = state.battlefields[0]
    bf.scored_by.clear()
    state._score(0, bf, "Conquer")
    while state.phase is Phase.CHOOSING:
        state.apply(state.legal_actions()[0])
    assert state.cards[gear].might_permanent == 1
    state._phase_ending()
    assert state.cards[gear].might_permanent == 1, "permanent buffs survive"


# --- vanilla ----------------------------------------------------------------


@pytest.mark.parametrize("card_id", ["OGN-142", "OGN-175"])
def test_vanilla_units_have_no_abilities(card_id):
    script = script_for(card_id)
    assert script is not None and script.abilities == ()
    assert DB[card_id].rules_text.strip() == ""


def test_a_detached_gear_waits_at_the_battlefield_until_cleanup(decks):
    """719.5 -- attached cards Detach "remaining in their current zones";
    457.1 is what sends them home, at the *next* Cleanup, not immediately."""
    state = arena(decks)
    host = put_unit(state, 0, "OGN-197", bf_location(0))
    gear = give(state, 0, "SFD-124")
    equip(state, gear, host)
    assert state.cards[gear].location == bf_location(0)

    state._kill(state.cards[host])
    assert state.cards[gear].attached_to is None
    assert state.cards[gear].location == bf_location(0)   # still there
    state._cleanup()
    assert state.cards[gear].location == BASE_LOCATION    # 457.1


def test_rune_prison_stuns_a_unit(decks):
    """OGN-050: "Stun a unit." (423) -- and the reminder text spells out why
    it matters: "It doesn't deal combat damage this turn."."""
    state = arena(decks)
    victim = put_unit(state, 1, "OGN-175", BASE_LOCATION)
    spell = give(state, 0, "OGN-050")
    play(state, spell)
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))
    pass_until_resolved(state)
    assert state.cards[victim].stunned is True


def test_solari_shieldbearer_stuns_when_played(decks):
    """OGN-051: "When you play me, stun a unit."."""
    state = arena(decks)
    victim = put_unit(state, 1, "OGN-175", BASE_LOCATION)
    unit = give(state, 0, "OGN-051")
    play(state, unit)
    if state.phase is Phase.CHOOSING:
        state.apply(ChooseTarget(victim))
    pass_until_resolved(state)
    assert state.cards[victim].stunned is True


def test_a_stunned_unit_deals_no_combat_damage(decks):
    """423.1.b end to end: the stunned unit still stands, but contributes
    nothing to its side's combat damage."""
    from engine.state import bf_location

    state = arena(decks)
    location = bf_location(0)
    mine = put_unit(state, 0, "OGN-142", location)
    assert state.combat_might(0, location) > 0
    state.stun(state.cards[mine])
    assert state.combat_might(0, location) == 0
    assert state.cards[mine].location == location    # still there
