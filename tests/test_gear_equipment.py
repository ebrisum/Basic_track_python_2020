"""Equipment: the Equip keyword, Might Bonuses, and what gear can be equipped.

150.1 -- only Gear with the Equipment tag are Equipment. Everything else in
the 111-card gear pool is base gear that cannot be attached at all, which is
why `equipment_profile` returns None for it rather than an empty profile.

818.1.c.2 -- "Equip [Cost]" is functionally short for
"[Cost]: Attach this gear to a unit you control." So Equip is derived from
printed text into an ordinary activated Ability; no card needs a script.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cards.database import load as load_db
from cards.dsl import Attach, PayEnergy, PayPower, TriggerKind
from cards.gear import equip_ability, equipment_profile

DB = load_db()


@dataclass(frozen=True)
class FakeCard:
    """A card face, for spellings whose set is not in the playable DB.

    Most UNL/VEN cards are absent from `cards.json`'s playable slice (RQ-1,
    RQ-11), but their printed Equip grammar still has to parse -- it is the
    spelling that differs by set, not the rule.
    """

    rules_text: str
    type: str = "gear"
    keywords: tuple = ("Equipment",)
    might: int | None = None
    card_id: str = "FAKE-001"


# --------------------------------------------------------- the Equipment tag


def test_only_tagged_gear_is_equipment():
    """150.1 -- Iron Ballista is gear, but not Equipment, so it cannot equip."""
    assert equipment_profile(DB["OGN-017"]) is None      # Iron Ballista
    assert equipment_profile(DB["SFD-161"]) is not None  # B.F. Sword


def test_non_equipment_gear_gets_no_equip_ability():
    assert equip_ability(DB["OGN-040"]) is None          # Seal of Rage


def test_most_gear_is_not_equipment():
    """The user's point, measured: most gear cannot be equipped."""
    gear = [c for c in DB.cards.values() if c.type == "gear"]
    equipment = [c for c in gear if equipment_profile(c) is not None]
    assert len(gear) > 2 * len(equipment)


# --------------------------------------------------------------- equip costs


@pytest.mark.parametrize(
    "card_id,energy,domains",
    [
        ("SFD-161", 0, ("Order",)),           # [EQUIP Order]
        ("SFD-009", 0, ("Fury",)),            # [EQUIP Fury]
        ("SFD-030", 1, ("Fury",)),            # [EQUIP 1, Fury]
        ("SFD-059", 1, ("Calm",)),            # [EQUIP1, Calm] -- no space
        ("SFD-118", 1, ("Body",)),            # [EQUIP 1 Body] -- no comma
    ],
)
def test_equip_costs_parse_across_every_printed_spelling(card_id, energy, domains):
    """The `[EQUIP ...]` marker is printed six different ways; the reminder
    text `(<cost>: Attach this to a unit you control.)` is not."""
    profile = equipment_profile(DB[card_id])
    assert profile.equip_cost == (energy, domains)


@pytest.mark.parametrize(
    "text,energy,domains",
    [
        # VEN-073 Jagged Cutlass -- no brackets at all.
        ("Equip 1 body rune (1 body rune: Attach this to a unit you control.)",
         0, ("Body",)),
        # UNL-019 Blighted Battleaxe -- energy and power spelled out.
        ("[Equip] 1 energy and 1 fury rune "
         "(1 energy and 1 fury rune: Attach this to a unit you control.)",
         1, ("Fury",)),
        # VEN-137 Shady Spectacles.
        ("[Equip] 1 energy and 1 order rune "
         "(1 energy and 1 order rune: Attach this to a unit you control.)",
         1, ("Order",)),
    ],
)
def test_later_sets_spell_the_cost_out_in_words(text, energy, domains):
    """"1 calm rune" is one power, not one energy plus one power (163.2)."""
    assert equipment_profile(FakeCard(text)).equip_cost == (energy, domains)


def test_a_non_resource_equip_cost_is_reported_not_guessed():
    """Blade of the Ruined King is "Order, Kill a friendly unit" -- the
    reminder text says only "Pay the cost:", so the cost is not derivable."""
    profile = equipment_profile(DB["SFD-178"])
    assert profile.equip_cost is None
    assert "Pay the cost" in profile.cost_note
    assert equip_ability(DB["SFD-178"]) is None


def test_every_equipment_either_parses_or_says_why():
    for card in DB.cards.values():
        profile = equipment_profile(card)
        if profile is None:
            continue
        assert profile.equip_cost is not None or profile.cost_note, card.card_id


# -------------------------------------------------------------- might bonus


@pytest.mark.parametrize(
    "card_id,bonus",
    [
        ("SFD-161", 3),   # "+3 Might"
        ("SFD-009", 0),   # "Might +0" -- after an ASSAULT 2 reminder saying +2
        ("SFD-064", 2),   # "Might +2" -- after a SHIELD 2 reminder saying +2
        ("SFD-108", 1),   # "+1 Might" -- glued to a reminder that also says +1
        ("SFD-042", 1),   # "+1 Might." -- a sentence above says "+2 Might"
        ("SFD-178", 4),   # "+4 Might"
    ],
)
def test_might_bonus_is_the_last_signed_token_outside_reminders(card_id, bonus):
    """137.1 -- the Might Bonus prints in the lower-right corner, so it is the
    last one on the card. Reminder text quotes other +N Might values."""
    assert equipment_profile(DB[card_id]).might_bonus == bonus


def test_absent_might_bonus_is_none_not_zero():
    """Eye of the Herald prints no bonus. "1 Might Recruit unit token" is not
    a bonus -- it is unsigned, and it describes a token."""
    assert equipment_profile(DB["SFD-153"]).might_bonus is None


def test_might_field_is_not_the_might_bonus():
    """Guards a tempting shortcut: the data's `might` disagrees with the
    printed bonus on most Equipment, so it must never be used for it."""
    disagree = 0
    for card in DB.cards.values():
        profile = equipment_profile(card)
        if profile is None or profile.might_bonus is None:
            continue
        if card.might != profile.might_bonus:
            disagree += 1
    assert disagree >= 10


# ------------------------------------------------------- the derived ability


def test_equip_is_a_derived_activated_ability():
    """818.1.c.2 -- no per-card Python; the ability is built from the text."""
    ability = equip_ability(DB["SFD-030"])       # Skyfall of Areion, [1, Fury]
    assert ability.kind is TriggerKind.ACTIVATED
    assert PayEnergy(1) in ability.costs
    assert PayPower("Fury", 1) in ability.costs
    assert any(isinstance(e, Attach) for e in ability.effects)


def test_equip_targets_only_friendly_units():
    """818.1.c.2 -- "a unit you control"."""
    effect = next(e for e in equip_ability(DB["SFD-161"]).effects
                  if isinstance(e, Attach))
    assert effect.selector.controller == "friendly"
    assert effect.selector.type == "unit"


def test_quick_draw_is_detected():
    """819 -- Quick-Draw is present on gear with Equip abilities."""
    assert equipment_profile(DB["SFD-022"]).quick_draw is True   # Long Sword
    assert equipment_profile(DB["SFD-161"]).quick_draw is False  # B.F. Sword
