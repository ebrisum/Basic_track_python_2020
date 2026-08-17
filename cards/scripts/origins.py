"""Hand-written scripts for the cards in the Milestone 1 starter decks.

Each entry is *data* built from the DSL primitives -- no per-card Python. The
`text` on each ability is the printed wording it implements, so a test failure
names the card text rather than a class.

Keywords that the engine implements structurally (Assault, Tank, Backline,
Ganking) are not repeated here; they are parsed from printed text by
`cards/keywords.py`. Only non-keyword text needs a script.
"""

from __future__ import annotations

from cards.dsl import (
    SELF,
    Ability,
    AddPower,
    Attach,
    Buff,
    CardScript,
    Deal,
    Discard,
    DiscardCost,
    Draw,
    Duration,
    ExhaustSelf,
    GrantKeyword,
    Kill,
    LookAtTop,
    RecycleFromTrash,
    ReturnToHand,
    Selector,
    TriggerKind,
    UnitsEnterReady,
    Who,
)

UNIT_AT_BF = Selector(scope="choose", type="unit", location="battlefield")
ALL_UNITS_AT_BF = Selector(scope="all", type="unit", location="battlefield")
FRIENDLY_UNIT = Selector(scope="choose", type="unit", controller="friendly")

SCRIPTS: tuple[CardScript, ...] = (
    # --- units -------------------------------------------------------------
    CardScript(
        card_id="OGN-003",  # Chemtech Enforcer
        abilities=(
            Ability(
                kind=TriggerKind.ON_PLAY,
                effects=(Discard(1, Who.YOU),),
                text="When you play me, discard 1.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-197",  # Teemo - Scout
        abilities=(
            Ability(
                kind=TriggerKind.ON_PLAY,
                effects=(Buff(3, Duration.THIS_TURN, SELF),),
                text="When you play me, give me +3 Might this turn.",
            ),
        ),
        complete=False,
        note="HIDDEN (811) is parsed but not implemented.",
    ),
    CardScript(
        card_id="OGN-036",  # Vi - Destructive
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(RecycleFromTrash(1),),
                effects=(Buff(1, Duration.THIS_TURN, SELF),),
                text="Recycle 1 from your trash: Give me +1 Might this turn.",
            ),
        ),
    ),
    # Vanilla units -- no text, so nothing to script. Listed so the registry
    # can assert every deck card is accounted for.
    CardScript(card_id="OGN-142"),  # Mountain Drake
    CardScript(card_id="OGN-175"),  # Shipyard Skulker
    CardScript(
        card_id="OGN-010",  # Legion Rearguard
        abilities=(),
        complete=False,
        note="ACCELERATE (805) is an alternative additional cost; not implemented.",
    ),
    CardScript(
        card_id="OGN-013",  # Pouty Poro
        abilities=(),
        complete=False,
        note="DEFLECT (809) taxes opponents' selection; not implemented.",
    ),
    # --- spells ------------------------------------------------------------
    CardScript(
        card_id="OGN-004",  # Cleave
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(
                    GrantKeyword("Assault", 3, Duration.THIS_TURN, Selector(type="unit")),
                ),
                text="Give a unit ASSAULT 3 this turn.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-009",  # Hextech Ray
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(Deal(3, UNIT_AT_BF),),
                text="Deal 3 to a unit at a battlefield.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-133",  # Flurry of Blades
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(Deal(1, ALL_UNITS_AT_BF),),
                text="Deal 1 to all units at battlefields.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-169",  # Gust
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(
                    ReturnToHand(
                        Selector(
                            scope="choose", type="unit",
                            location="battlefield", max_might=3,
                        )
                    ),
                ),
                text="Return a unit at a battlefield with 3 Might or less to its owner's hand.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-129",  # Confront
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(UnitsEnterReady(), Draw(1, Who.YOU)),
                text="Units you play this turn enter ready. Draw 1.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-179",  # Acceptable Losses
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(
                    Kill(Selector(scope="choose", type="gear", each_player=True)),
                ),
                text="Each player kills one of their gear.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-183",  # Stacked Deck
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(LookAtTop(3, take=1, who=Who.YOU),),
                text="Look at the top 3 cards of your Main Deck. Put 1 into your hand and recycle the rest.",
            ),
        ),
    ),
    CardScript(
        card_id="SFD-122",  # Called Shot
        abilities=(
            Ability(
                kind=TriggerKind.ON_RESOLVE,
                effects=(LookAtTop(2, take=1, who=Who.YOU),),
                text="Look at the top 2 cards of your Main Deck...",
            ),
        ),
        complete=False,
        note="REPEAT Chaos (820) optional additional cost is not implemented.",
    ),
    # --- gear: the rune-seal cycle (429 Add) -------------------------------
    CardScript(
        card_id="OGN-040",  # Seal of Rage
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Fury", 1),),
                text="Exhaust: REACTION - ADD 1 Fury.",
            ),
        ),
    ),
    CardScript(
        card_id="SFD-222",  # Seal of Rage (Overnumbered)
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Fury", 1),),
                text="Exhaust: REACTION - ADD 1 Fury.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-163",  # Seal of Strength
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Body", 1),),
                text="Exhaust: REACTION - ADD body.",
            ),
        ),
    ),
    CardScript(
        card_id="SFD-231",  # Seal of Strength (Overnumbered)
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Body", 1),),
                text="Exhaust: REACTION - ADD body.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-204",  # Seal of Discord
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Chaos", 1),),
                text="Exhaust: REACTION - ADD chaos.",
            ),
        ),
    ),
    CardScript(
        card_id="SFD-234",  # Seal of Discord (Overnumbered)
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(ExhaustSelf(),),
                effects=(AddPower("Chaos", 1),),
                text="Exhaust: REACTION - ADD chaos.",
            ),
        ),
    ),
    # --- gear: equipment ---------------------------------------------------
    CardScript(
        card_id="SFD-108",  # Warmog's Armor
        abilities=(
            Ability(
                kind=TriggerKind.ON_PLAY,
                effects=(Attach(),),
                text="[EQUIP Body] Attach this to a unit you control.",
            ),
            Ability(
                kind=TriggerKind.ON_CONQUER,
                effects=(Buff(1, Duration.PERMANENT, SELF),),
                text="When I conquer, buff me.",
            ),
        ),
    ),
    CardScript(
        card_id="SFD-124",  # Doran's Ring
        abilities=(
            Ability(
                kind=TriggerKind.ON_PLAY,
                effects=(Attach(),),
                text="[EQUIP Chaos] Attach this to a unit you control.",
            ),
            Ability(
                kind=TriggerKind.ON_CONQUER,
                effects=(Discard(1, Who.YOU), Draw(1, Who.YOU)),
                text="When I conquer, discard 1, then draw 1.",
            ),
        ),
    ),
    CardScript(
        card_id="OGN-023",  # Unlicensed Armory
        abilities=(
            Ability(
                kind=TriggerKind.ACTIVATED,
                costs=(DiscardCost(1), ExhaustSelf()),
                effects=(GrantKeyword("Resilient", 1, Duration.THIS_TURN, FRIENDLY_UNIT),),
                text="Discard 1, Exhaust: Choose a friendly unit...",
            ),
        ),
        complete=False,
        note=(
            "The delayed replacement -- 'the next time it dies this turn, pay 1 "
            "Fury to recall it exhausted instead' -- needs replacement effects "
            "(367) and delayed abilities (389). The grant is a marker only."
        ),
    ),
)
