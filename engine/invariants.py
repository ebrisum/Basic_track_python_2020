"""Things that must be true of a Riftbound state, always.

Before a model can be trained to play this game, the game has to *be* this
game. A rules engine can be wrong in ways no single test notices -- a card
that is in two zones at once, a designation that outlives its combat, a deck
that quietly gains a card -- and a model trained on top of that learns
whatever the bug rewards.

Spot-checking cannot find those. What finds them is asserting the structural
truths after **every action of every game**, so a violation is caught in the
position that created it rather than inferred from a strange win rate ten
thousand games later.

Each check cites the rule it comes from. `check()` returns a list of
violations rather than raising, so a caller can report all of them at once,
and so a fuzz run can keep going and collect more than the first.

This is deliberately not part of `apply()`: the engine should not pay for it
on every move of every search. `tests/test_invariants.py` runs it over whole
games, and `--check` on the batch runner turns it on for a long run.
"""

from __future__ import annotations

from engine.zones import BASE_LOCATION

MAIN_DECK_TYPES = frozenset({"unit", "spell", "gear"})


def _zones_of(state, player: int) -> dict[str, list[int]]:
    """Every list a card instance can legitimately be sitting in."""
    zones = state.players[player]
    return {
        "hand": zones.hand,
        "main_deck": zones.main_deck,
        "rune_deck": zones.rune_deck,
        "trash": zones.trash,
        "banishment": zones.banishment,
        "champion_zone": zones.champion_zone,
        "base": zones.base,
        "channeled_runes": zones.channeled_runes,
    }


def check(state) -> list[str]:
    """Every invariant violation in `state`, as readable strings."""
    problems: list[str] = []
    say = problems.append
    # 323.1 -- a cleanup that wins the game stops at step 1, so the later
    # cleanup steps legitimately do not run in a finished position.
    finished = getattr(state.phase, "value", state.phase) == "game_over"


    # --- 107-108: a card is in exactly one zone -----------------------------
    for player in (0, 1):
        zones = _zones_of(state, player)
        # `channeled_runes` is the subset of runes on the board; a rune is in
        # it *or* in the rune deck, never both, and never in `base` as well.
        seen: dict[int, str] = {}
        for name, contents in zones.items():
            for instance_id in contents:
                if instance_id in seen:
                    say(f"P{player}: card {instance_id} is in both "
                        f"{seen[instance_id]} and {name} (107-108)")
                seen[instance_id] = name
                if instance_id not in state.cards:
                    say(f"P{player}: {name} holds unknown instance {instance_id}")

    # --- a card's location must agree with the zone holding it --------------
    for ref in state.cards.values():
        zones = _zones_of(state, ref.controller)
        on_board = set(zones["base"]) | set(zones["channeled_runes"])
        if ref.location is not None and ref.instance_id not in on_board:
            say(f"card {ref.instance_id} has location {ref.location!r} but is "
                f"in no board zone of its controller P{ref.controller} (107.1.c)")
        if ref.location is None and ref.instance_id in on_board:
            say(f"card {ref.instance_id} is in a board zone of P{ref.controller} "
                f"but has no location (107.1.c)")

    # --- 716-719 Attachment -------------------------------------------------
    for ref in state.cards.values():
        if ref.attached_to is None:
            continue
        if ref.attached_to not in state.cards:
            say(f"card {ref.instance_id} is attached to unknown "
                f"{ref.attached_to} (718)")
            continue
        host = state.cards[ref.attached_to]
        if ref.location is None:
            say(f"card {ref.instance_id} is off the board but still attached "
                f"to {ref.attached_to} (718.5)")
        elif host.location is None:
            say(f"card {ref.instance_id} is attached to {ref.attached_to}, "
                f"which is not on the board (719.5)")
        elif host.location != ref.location:
            say(f"card {ref.instance_id} at {ref.location!r} is attached to "
                f"{ref.attached_to} at {host.location!r}; a Top-Most Card and "
                f"its attachments share a location (719.3)")
        if ref.attached_to == ref.instance_id:
            say(f"card {ref.instance_id} is attached to itself (718.5.d)")

    # --- 464 / 466.7.a: designations belong to a live combat ----------------
    fight = state.combat if state.combat is not None else state.showdown
    if fight is not None and not getattr(fight, "is_combat", True):
        fight = None
    location = f"bf:{fight.battlefield}" if fight is not None else None
    for ref in state.cards.values():
        if not (ref.is_attacker or ref.is_defender):
            continue
        if ref.is_attacker and ref.is_defender:
            say(f"card {ref.instance_id} is both attacker and defender (464.2.c)")
        if fight is None:
            say(f"card {ref.instance_id} carries a designation with no combat "
                f"in progress (466.7.a)")
        elif ref.location != location:
            say(f"card {ref.instance_id} carries a designation at "
                f"{ref.location!r}, away from the combat at {location!r} "
                f"(323.2.c)")

    # --- 179-187 Tokens -----------------------------------------------------
    for ref in state.cards.values():
        if not ref.is_token:
            continue
        if ref.location is None:
            say(f"token {ref.instance_id} exists off the board; 186.1 says it "
                f"ceases to exist instead")
        for player in (0, 1):
            zones = _zones_of(state, player)
            for name in ("hand", "trash", "main_deck", "rune_deck",
                         "banishment", "champion_zone"):
                if ref.instance_id in zones[name]:
                    say(f"token {ref.instance_id} is in {name}; tokens cannot "
                        f"exist in a non-board zone (186)")

    # --- 421 / 811 Hidden cards ---------------------------------------------
    facedown: dict[int, list[int]] = {}
    for ref in state.cards.values():
        if ref.hidden_at is None:
            continue
        facedown.setdefault(ref.hidden_at, []).append(ref.instance_id)
        if ref.location is not None:
            say(f"card {ref.instance_id} is hidden and also has location "
                f"{ref.location!r}; a facedown card is not a permanent (421.1)")
        if not (0 <= ref.hidden_at < len(state.battlefields)):
            say(f"card {ref.instance_id} is hidden at battlefield "
                f"{ref.hidden_at}, which does not exist")
        elif (state.battlefields[ref.hidden_at].controller != ref.controller
              and not finished):
            # 107.3.c -- a card may occupy a Facedown Zone only while its
            # controller also controls the associated battlefield; 107.3.d and
            # 323.7 remove it at the next Cleanup when that stops being true.
            # Cleanup runs to fixpoint inside `apply`, so between actions this
            # must never be observable.
            #
            # Except once the game is over. Removal is cleanup *step 5* (323.7)
            # and winning is *step 1* (323.1): the winning cleanup stops at
            # step 1 and the later steps never run. A stranded facedown card in
            # a finished game is the rules working, not a leak -- found by this
            # check firing on the last action of a 299-step game.
            say(f"card {ref.instance_id} is hidden at battlefield "
                f"{ref.hidden_at}, which P{ref.controller} does not control "
                f"(107.3.c / 323.7)")
    for index, hidden in facedown.items():
        if len(hidden) > 1:
            say(f"battlefield {index} has {len(hidden)} facedown cards; 811.1.b "
                f"allows one")

    # --- 423 Stun -----------------------------------------------------------
    for ref in state.cards.values():
        if not ref.stunned:
            continue
        if ref.location is None:
            say(f"card {ref.instance_id} is stunned but not on the board (423.1)")
        elif state.db[ref.card_id].type != "unit":
            say(f"card {ref.instance_id} is stunned but is not a unit (423.1)")

    # --- 190 Control --------------------------------------------------------
    for bf in state.battlefields:
        if bf.controller not in (None, 0, 1):
            say(f"battlefield {bf.index} has controller {bf.controller!r} (190.2)")
        if bf.contested and bf.contested_by not in (0, 1):
            say(f"battlefield {bf.index} is Contested by "
                f"{bf.contested_by!r} (190.3.a)")
        if not bf.contested and bf.contested_by is not None:
            say(f"battlefield {bf.index} is not Contested but records a "
                f"contester (190.3.b)")

    # --- 163 resources are never negative -----------------------------------
    for player in (0, 1):
        pool = state.players[player].pool
        if pool.energy < 0 or pool.universal_power < 0:
            say(f"P{player} has a negative pool (163)")
        for domain, count in pool.power.items():
            if count < 0:
                say(f"P{player} has {count} {domain} power (163.2)")

    # --- 194 scoring --------------------------------------------------------
    for player in (0, 1):
        if state.players[player].points < 0:
            say(f"P{player} has negative points (194)")

    # --- 327-340 the Chain --------------------------------------------------
    for item in state.chain:
        if item.instance_id not in state.cards:
            say(f"chain item references unknown instance {item.instance_id} (329)")
        if item.controller not in (0, 1):
            say(f"chain item has controller {item.controller!r} (329)")
    if state.priority is not None and state.priority not in (0, 1):
        say(f"priority is {state.priority!r} (311)")
    if state.focus is not None and state.focus not in (0, 1):
        say(f"focus is {state.focus!r} (313)")

    # --- card conservation: nothing is created or destroyed -----------------
    for player in (0, 1):
        zones = _zones_of(state, player)
        located = sum(
            1
            for contents in zones.values()
            for instance_id in contents
            if not state.cards[instance_id].is_token
        )
        # 439 -- tokens are created during play, so they are not part of the
        # dealt card count and must be excluded from both sides of it.
        owned = sum(1 for ref in state.cards.values()
                    if ref.owner == player and not ref.is_token)
        # A card whose controller is the opponent sits in *their* zones, so
        # counting by owner needs the ones on loan added back.
        on_loan = sum(
            1 for ref in state.cards.values()
            if ref.owner == player and ref.controller != player and not ref.is_token
        )
        borrowed = sum(
            1 for ref in state.cards.values()
            if ref.controller == player and ref.owner != player and not ref.is_token
        )
        expected = owned - on_loan + borrowed
        in_chain = sum(1 for item in state.chain
                       if state.cards[item.instance_id].controller == player
                       and item.instance_id not in
                       {i for c in zones.values() for i in c})
        # 421.1 -- a facedown card is at a battlefield, so it is still in the
        # game; it is simply in no zone *list*, being neither hand nor a
        # permanent in the base.
        hidden_count = sum(
            1 for ref in state.cards.values()
            if ref.hidden_at is not None and ref.controller == player
            and not ref.is_token
        )
        legend = state.players[player].legend
        extra = 1 if legend is not None and legend not in {
            i for c in zones.values() for i in c} else 0
        if located + in_chain + extra + hidden_count != expected:
            say(f"P{player} accounts for {located + in_chain + extra + hidden_count} cards but "
                f"controls {expected}: a card is lost or duplicated (107)")

    return problems


def assert_ok(state, context: str = "") -> None:
    """Raise on the first violation, naming it."""
    problems = check(state)
    if problems:
        where = f" after {context}" if context else ""
        raise AssertionError(
            f"{len(problems)} state invariant(s) violated{where}:\n  "
            + "\n  ".join(problems)
        )
