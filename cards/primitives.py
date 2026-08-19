"""The DSL interpreter: executes `cards.dsl` effects against a game state.

One function per primitive, dispatched from `execute`. An effect that needs a
player decision returns a `ChoiceRequest` instead of acting; the engine parks
it, offers the options through `legal_actions()`, and calls back with the
choice. That keeps every decision inside the frozen agent-facing interface.

This module knows the rules of each Game Action but nothing about specific
cards; `cards/scripts/` supplies the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from cards.dsl import (
    AddEnergy,
    AddPower,
    Attach,
    Double,
    EquipToMe,
    ModifyMight,
    Prevent,
    PlaceBuff,
    SpendBuff,
    Swap,
    ChoiceRequest,
    Deal,
    Discard,
    Draw,
    Duration,
    Effect,
    Banish,
    Channel,
    Counter,
    CreateToken,
    Detach,
    Heal,
    Recycle,
    Reveal,
    Exhaust,
    Stun,
    GainPoints,
    GrantKeyword,
    Kill,
    LookAtTop,
    Ready,
    ReturnToHand,
    Selector,
    UnitsEnterReady,
    Who,
)


@dataclass
class EffectContext:
    """Who is resolving what, and on behalf of which card."""

    controller: int
    source: int | None = None  # instance id of the card producing the effect
    # Filled in when a ChoiceRequest is answered, so the effect can resume.
    chosen: tuple[int, ...] = ()
    # Remaining repeats for multi-pick effects (e.g. "discard 2").
    remaining: int = 0
    payload: dict = field(default_factory=dict)
    # 811.1.d.2 -- when the source was played from Hidden, the battlefield it
    # was hidden at, as a location string ("bf:0"). Targets must come from
    # there. `None` for every ordinary play, which is the common case.
    restrict_location: str | None = None
    # 355.8 -- set when this effect's targets were declared as the item was
    # played. It distinguishes "no target was ever chosen" from "every
    # declared target has since become illegal", which 359.3.e.5 resolves
    # very differently: the second leaves the instruction unfollowed rather
    # than letting the effect pick something new.
    targets_declared: bool = False

    def copy(self) -> "EffectContext":
        # `chosen` is a tuple; only `payload` is mutable.
        return replace(self, payload=dict(self.payload))


def _resolve_player(who: Who, controller: int) -> list[int]:
    if who is Who.YOU:
        return [controller]
    if who is Who.OPPONENT:
        return [1 - controller]
    return [controller, 1 - controller]


def is_target(selector) -> bool:
    """355.7-355.10 -- whether choosing by this selector declares a *target*.

    355.7 makes anything a spell chooses a target "unless indicated otherwise
    by the rules in this section". The FAQ's targeting page enumerates the
    exceptions; two of them are expressible in this DSL and both are excluded
    here:

    * **355.10.d** "there is no real choice involved" -- `scope="all"` applies
      to everything matching, and `scope="self"` picks nothing. Note 355.10.d.2:
      a selector with exactly one legal option *is* still a target; that is a
      fact about the board, not about the selector.
    * **355.10.e** "part of a set chosen wholly or partly by other players" --
      `each_player`, whose worked example in the FAQ is literally "Each player
      kills a unit they control".

    The remaining exceptions need no test here. 355.10.a (a non-public zone) is
    excluded because `Discard` and `LookAtTop` reach the hand and deck without
    a selector at all. 355.10.c (trigger conditions and costs) is excluded
    because costs are `Cost` objects, not effects. 355.10.f ("must") has no
    DSL representation, and no scripted card uses the wording.
    """
    return (selector is not None
            and selector.scope == "choose"
            and not selector.each_player)


def targets_of(abilities) -> list[tuple[int, int, Selector]]:
    """Every targeted selector in `abilities`, as (ability, effect, selector).

    The indices address the effect within the ability list they came from, so
    a stored choice can be matched back to the effect it belongs to when the
    item resolves. Both indices are needed: one ability can hold several
    targeted effects.
    """
    found = []
    for ability_index, ability in enumerate(abilities):
        for effect_index, effect in enumerate(ability.effects):
            selector = getattr(effect, "selector", None)
            if is_target(selector):
                found.append((ability_index, effect_index, selector))
    return found


def restriction_binds(selector: Selector) -> bool:
    """811.1.d.2 -- whether Hidden's "choose from among options at that
    battlefield" restriction applies to this selector.

    The exception is "unless the ability explicitly restricts targeting in a
    way that makes this impossible" -- a *static* property of the selector, not
    of what happens to be on the board. 811.1.d already covers the empty-board
    case separately: a hidden spell with no valid target under the restriction
    simply cannot be played from Hidden.

    The only selector the DSL can currently express that is impossible to
    satisfy at a battlefield is one restricted to a base. Riftbound's own
    example of the exception -- Tideturner's "a unit you control at *another*
    location" -- needs a relative-location constraint the DSL does not have;
    no scripted card uses one, and RQ-14 records that.
    """
    return selector.location != "base"


def matches(state, ref, selector: Selector, controller: int, source: int | None) -> bool:
    """Whether one card instance satisfies a selector."""
    card = state.db[ref.card_id]

    if selector.type != "any" and card.type != selector.type:
        return False
    if selector.controller == "friendly" and ref.controller != controller:
        return False
    if selector.controller == "enemy" and ref.controller == controller:
        return False

    if selector.location == "battlefield":
        if not (ref.location or "").startswith("bf:"):
            return False
    elif selector.location == "base":
        if ref.location != "base":
            return False
    elif ref.location is None:
        return False  # not on the board at all

    if selector.max_might is not None and state.might_of(ref) > selector.max_might:
        return False
    return True


def candidates(state, selector: Selector, controller: int, source: int | None,
               restrict_location: str | None = None) -> list[int]:
    """All instance ids a selector matches, in canonical order.

    `restrict_location` is 811.1.d.2's Hidden restriction: the battlefield the
    source was played from. It narrows the matches; it never widens them.
    """
    if selector.scope == "self":
        return [source] if source is not None else []
    restrict = restrict_location if restriction_binds(selector) else None
    return sorted(
        ref.instance_id
        for ref in state.cards.values()
        if matches(state, ref, selector, controller, source)
        and (restrict is None or ref.location == restrict)
    )


def _targets(
    state, selector: Selector, ctx: EffectContext, prompt: str
) -> tuple[list[int], ChoiceRequest | None]:
    """Resolve a selector to concrete instance ids, or ask the player.

    Returns `(ids, None)` when no decision is needed, or `([], request)` when
    the controller must choose.
    """
    if ctx.chosen:
        # 359.3.e.5 -- re-check before honouring the choice. A ChoiceRequest
        # lists what is legal when it is *raised*, and the cleanup at the end
        # of that same action can kill one of the options before the answer
        # arrives in a later one. An option that has since become illegal is
        # unaffected, exactly as a declared target would be.
        live = candidates(state, selector, ctx.controller, ctx.source,
                          ctx.restrict_location)
        return [i for i in ctx.chosen if i in live], None
    if ctx.targets_declared:
        # 359.3.e.5 -- "If any of the spell's targets are no longer legal,
        # those game objects ... are unaffected by the spell as it resolves.
        # Any instructions related to an illegal target can't be followed."
        # The declared target is gone, so the instruction is skipped -- not
        # re-aimed at whatever else happens to be legal now.
        return [], None

    options = candidates(
        state, selector, ctx.controller, ctx.source, ctx.restrict_location
    )
    if selector.scope in ("self", "all"):
        return options, None
    if not options:
        return [], None  # 355.6 -- nothing to choose; the effect does nothing
    if len(options) == 1:
        return options, None  # no decision to make
    return [], ChoiceRequest(
        player=ctx.controller, options=tuple(options), prompt=prompt
    )


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def _draw(state, effect: Draw, ctx: EffectContext) -> ChoiceRequest | None:
    """413 Draw."""
    for player in _resolve_player(effect.who, ctx.controller):
        state._draw(player, effect.count)
    return None


def _discard(state, effect: Discard, ctx: EffectContext) -> ChoiceRequest | None:
    """422 Discard -- the discarding player chooses (411.1)."""
    for player in _resolve_player(effect.who, ctx.controller):
        hand = state.players[player].hand
        if not hand:
            continue
        if ctx.chosen:
            picks = list(ctx.chosen)
        elif len(hand) == 1 or effect.count >= len(hand):
            picks = list(hand[: effect.count])
        else:
            return ChoiceRequest(
                player=player, options=tuple(sorted(hand)), prompt="Discard a card"
            )
        for instance_id in picks:
            if instance_id in hand:
                hand.remove(instance_id)
                state.players[state.cards[instance_id].owner].trash.append(instance_id)
                state._emit(
                    f"P{player} discards {state.db[state.cards[instance_id].card_id].name}"
                )
    return None


def _deal(state, effect: Deal, ctx: EffectContext) -> ChoiceRequest | None:
    """417 Deal -- mark damage (142.3)."""
    ids, request = _targets(state, effect.selector, ctx, f"Deal {effect.amount} to")
    if request:
        return request
    for instance_id in ids:
        state.deal_damage(state.cards[instance_id], effect.amount)
    return None


def _kill(state, effect: Kill, ctx: EffectContext) -> ChoiceRequest | None:
    """428 Kill. `each_player` makes each player choose their own (411.1)."""
    if effect.selector.each_player:
        for player in (ctx.controller, 1 - ctx.controller):
            scoped = Selector(
                scope=effect.selector.scope,
                type=effect.selector.type,
                controller="friendly",
                location=effect.selector.location,
            )
            options = candidates(
                state, scoped, player, ctx.source, ctx.restrict_location
            )
            if not options:
                continue
            key = f"killed_{player}"
            if ctx.payload.get(key):
                continue
            if len(options) > 1 and not ctx.chosen:
                return ChoiceRequest(
                    player=player, options=tuple(options), prompt="Kill one of your gear"
                )
            pick = list(ctx.chosen)[0] if ctx.chosen else options[0]
            state._kill(state.cards[pick])
            ctx.payload[key] = True
            ctx.chosen = ()
        return None

    ids, request = _targets(state, effect.selector, ctx, "Kill")
    if request:
        return request
    for instance_id in ids:
        state._kill(state.cards[instance_id])
    return None


def _modify_might(state, effect: ModifyMight, ctx: EffectContext) -> ChoiceRequest | None:
    """A raw Might modification -- "give me +3 Might this turn".

    Not 426 Buff. See `_place_buff` for the Game Action.
    """
    ids, request = _targets(state, effect.selector, ctx,
                            f"Give +{effect.might} Might")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        if effect.duration is Duration.THIS_TURN:
            ref.might_this_turn += effect.might
        else:
            ref.might_permanent += effect.might
        state._emit(f"{state.db[ref.card_id].name} gets +{effect.might} Might")
    return None


def _double(state, effect: Double, ctx: EffectContext) -> ChoiceRequest | None:
    """432 Double -- add the attribute's current value to itself.

    432.1.a: the amount is fixed at resolution from the *current* value, and
    then applies for the stated duration. Their worked example is a 3-Might
    unit with Shield 2 defending: current Might 5, so it gains +5, and after
    combat -- Shield gone -- it is 8 rather than 6.
    """
    ids, request = _targets(state, effect.selector, ctx, "Double the Might of")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        gain = state.might_of(ref)
        if effect.duration is Duration.THIS_TURN:
            ref.might_this_turn += gain
        else:
            ref.might_permanent += gain
        state._emit(f"{state.db[ref.card_id].name}'s Might is doubled (+{gain})")
    return None


def _swap(state, effect: Swap, ctx: EffectContext) -> ChoiceRequest | None:
    """433 Swap -- reverse a numeric value between two game objects.

    433.1.b works by difference rather than assignment, which is what keeps
    the change expressible as two duration-scoped modifiers. 433.1.c makes
    equal values a no-op, and leaving a +0/-0 pair behind would still be one
    for Might but not for anything counting modifiers, so nothing is applied.
    """
    ids, request = _targets(state, effect.selector, ctx, "Swap Might with")
    if request:
        return request
    if len(ids) < 2:
        return None
    first, second = state.cards[ids[0]], state.cards[ids[1]]
    difference = state.might_of(first) - state.might_of(second)
    if difference == 0:
        return None                                   # 433.1.c
    if effect.duration is Duration.THIS_TURN:
        first.might_this_turn -= difference
        second.might_this_turn += difference
    else:
        first.might_permanent -= difference
        second.might_permanent += difference
    state._emit(
        f"{state.db[first.card_id].name} and {state.db[second.card_id].name} "
        f"swap Might"
    )
    return None


def _prevent(state, effect: Prevent, ctx: EffectContext) -> ChoiceRequest | None:
    """437 Prevent -- track a Prevent Value on the chosen units.

    437.7 calls this a Delayed Replacement Effect: nothing happens now, and
    the next damage dealt to the unit is reduced by the tracked value.
    """
    ids, request = _targets(state, effect.selector, ctx, "Prevent damage to")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        if effect.amount is None or ref.prevent is None:
            ref.prevent = None                        # 437.1.b.1.b / 437.3.c
        else:
            ref.prevent += effect.amount
        shown = "all" if ref.prevent is None else ref.prevent
        state._emit(f"{state.db[ref.card_id].name} will prevent {shown} damage")
    return None


def _place_buff(state, effect: PlaceBuff, ctx: EffectContext) -> ChoiceRequest | None:
    """426 Buff -- place a Buff counter on each chosen unit.

    426.1.c: a unit that already has a buff can still be *chosen*, it just is
    not buffed. `state.buff` reports which happened, and that report is what a
    linked instruction ("if it was buffed this way, draw 1") and a trigger
    ("when you buff me") both need. Neither is scripted yet; the information
    is produced now so those do not have to re-derive it later.
    """
    ids, request = _targets(state, effect.selector, ctx, "Buff")
    if request:
        return request
    landed = []
    for instance_id in ids[: effect.count] if effect.count else ids:
        ref = state.cards[instance_id]
        if state.buff(ref):
            landed.append(instance_id)
    ctx.payload["buffed"] = tuple(landed)
    return None


def _spend_buff(state, effect: SpendBuff, ctx: EffectContext) -> ChoiceRequest | None:
    """702.2.b -- remove a Buff counter from a unit its controller owns."""
    ids, request = _targets(state, effect.selector, ctx, "Spend a buff from")
    if request:
        return request
    spent = [i for i in ids
             if state.spend_buff(state.cards[i], spender=ctx.controller)]
    ctx.payload["spent_buff"] = tuple(spent)
    return None


def _grant_keyword(state, effect: GrantKeyword, ctx: EffectContext) -> ChoiceRequest | None:
    ids, request = _targets(state, effect.selector, ctx, f"Give {effect.keyword}")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        ref.granted_keywords = ref.granted_keywords + (
            (effect.keyword, effect.value or 0, effect.duration.value),
        )
        state._emit(
            f"{state.db[ref.card_id].name} gains {effect.keyword} "
            f"{effect.value if effect.value else ''}".strip()
        )
    return None


def _add_energy(state, effect: AddEnergy, ctx: EffectContext) -> ChoiceRequest | None:
    """429 Add (163.1)."""
    state.players[ctx.controller].pool.energy += effect.count
    return None


def _add_power(state, effect: AddPower, ctx: EffectContext) -> ChoiceRequest | None:
    """429 Add (163.2)."""
    pool = state.players[ctx.controller].pool
    if effect.domain.lower() in ("universal", "any", ""):
        pool.universal_power += effect.count
    else:
        pool.add_power(effect.domain, effect.count)
    return None


def _return_to_hand(state, effect: ReturnToHand, ctx: EffectContext) -> ChoiceRequest | None:
    ids, request = _targets(state, effect.selector, ctx, "Return to hand")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        owner_state = state.players[ref.owner]
        controller_state = state.players[ref.controller]
        if instance_id in controller_state.base:
            controller_state.base.remove(instance_id)
        # 719.5 -- leaving the board detaches both directions. This used to
        # clear only the location, so a unit bounced to hand left its
        # Equipment attached to a card that was no longer in play.
        state.leave_board(ref)
        ref.damage = 0
        ref.exhausted = False
        ref.might_this_turn = 0
        owner_state.hand.append(instance_id)
        state._emit(f"{state.db[ref.card_id].name} returns to its owner's hand")
    return None


def _look_at_top(state, effect: LookAtTop, ctx: EffectContext) -> ChoiceRequest | None:
    """Look at the top N, keep `take`, Recycle the rest to the bottom (416.1)."""
    player = _resolve_player(effect.who, ctx.controller)[0]
    deck = state.players[player].main_deck
    looked = deck[: effect.count]
    if not looked:
        return None
    if len(looked) > 1 and not ctx.chosen:
        return ChoiceRequest(
            player=player, options=tuple(looked), prompt="Put into your hand"
        )
    keep = list(ctx.chosen)[: effect.take] if ctx.chosen else looked[: effect.take]
    for instance_id in looked:
        deck.remove(instance_id)
    for instance_id in keep:
        state.players[player].hand.append(instance_id)
    for instance_id in looked:
        if instance_id not in keep:
            deck.append(instance_id)  # recycled to the bottom
    state._emit(f"P{player} looks at {len(looked)} and keeps {len(keep)}")
    return None


def _attach(state, effect: Attach, ctx: EffectContext) -> ChoiceRequest | None:
    """434 Attach / 716 Attachment."""
    ids, request = _targets(state, effect.selector, ctx, "Attach to")
    if request:
        return request
    if not ids or ctx.source is None:
        return None
    gear = state.cards[ctx.source]
    gear.attached_to = ids[0]
    host = state.cards[ids[0]]
    gear.location = host.location
    state._emit(
        f"{state.db[gear.card_id].name} is attached to {state.db[host.card_id].name}"
    )
    return None


def _equip_to_me(state, effect: EquipToMe, ctx: EffectContext) -> ChoiceRequest | None:
    """821 Weaponmaster -- attach a chosen Equipment to the source unit.

    821.1.c: "Pay the cost of its Equip ability, reduced by [A], to attach it
    to this unit."

    821.1.c.5 is the whole error path: if the cost cannot be paid, or the card
    cannot be detached or attached, "it stays in its current location,
    Attached to anything it was already Attached to". So nothing is changed
    until the payment succeeds.

    821.1.c and 725.3 make this an exception to 718.2 -- an Equipment already
    worn by another unit has Inactive Rules Text, and Weaponmaster reaches its
    Equip cost anyway. That is why the cost is read from the card's profile
    here rather than from its (Inactive) activated ability.
    """
    from cards.gear import discounted_equip_cost, equipment_profile

    ids, request = _targets(state, effect.selector, ctx, "Equip to me")
    if request:
        return request
    if not ids or ctx.source is None:
        return None
    gear = state.cards[ids[0]]
    host = state.cards[ctx.source]
    profile = equipment_profile(state.db[gear.card_id])
    if profile is None:
        return None                        # 821.1.c -- not an Equipment
    cost = discounted_equip_cost(profile.equip_cost)
    if cost is None:
        return None                        # 821.1.c.4 -- no cost to pay
    energy, domains = cost
    pool = state.players[ctx.controller].pool
    if not pool.can_pay(energy, list(domains)):
        return None                        # 821.1.c.5
    pool.pay(energy, list(domains))
    gear.attached_to = host.instance_id
    gear.location = host.location          # 719.3
    state._emit(
        f"{state.db[gear.card_id].name} is equipped to "
        f"{state.db[host.card_id].name} (Weaponmaster)"
    )
    return None


def _recycle(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """416 -- to the bottom of the corresponding deck (416.1.a/b), and always
    to the card owner's own deck (416.1.c)."""
    for player in _resolve_player(effect.who, ctx.controller):
        zones = state.players[player]
        source = zones.trash if effect.zone == "trash" else zones.hand
        moving = list(source[: effect.count])
        for instance_id in moving:
            source.remove(instance_id)
            ref = state.cards[instance_id]
            owner = state.players[ref.owner]
            if state.db[ref.card_id].type == "rune":
                owner.rune_deck.append(instance_id)      # 416.1.b
            else:
                owner.main_deck.append(instance_id)      # 416.1.a
        if moving:
            state._emit(f"P{player} recycles {len(moving)} card(s) from "
                        f"{effect.zone}")
    return None


def _reveal(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """424 -- announce cards publicly. The cards do not change zones.

    Announcing in the shared log is what makes the information public, but a
    player also *remembers* what they were shown. A card revealed from a hand
    is recorded so `engine.knowledge` can stop treating it as unseen: an agent
    that forgets a revelation plays strictly worse than the rules allow.

    Only hand reveals are recorded. A card shown from the top of a deck is
    already inside the pool the deducer subtracts to, and nothing pins it
    there afterwards -- claiming to know where it went would be an invention.
    """
    for player in _resolve_player(effect.who, ctx.controller):
        zones = state.players[player]
        source = zones.hand if effect.zone == "hand" else zones.main_deck
        shown = source[: effect.count]
        if not shown:
            continue
        names = ", ".join(state.db[state.cards[i].card_id].name for i in shown)
        state._emit(f"P{player} reveals {names} (from {effect.zone})")
        if effect.zone == "hand":
            state.revealed_in_hand.update(shown)
    return None


def _heal(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """418 -- clearing damage is Healing (418.1.a)."""
    ids, request = _targets(state, effect.selector, ctx, "Heal")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        if ref.damage:
            ref.damage = 0
            state._emit(f"{state.db[ref.card_id].name} is healed")
    return None


def _banish(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """427 -- straight into Banishment, from wherever the card is.

    Not routed through kill or discard: 427.2.a/b say Banish is a subset of
    neither, so a banished permanent fires no death trigger.
    """
    ids, request = _targets(state, effect.selector, ctx, "Banish")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        owner = state.players[ref.owner]
        controller = state.players[ref.controller]
        for zone in (controller.base, controller.channeled_runes, owner.hand,
                     owner.trash, owner.main_deck, owner.rune_deck,
                     owner.champion_zone):
            if instance_id in zone:
                zone.remove(instance_id)
        if ref.is_token:
            # 186.1 -- a token in a non-board zone ceases to exist instead.
            state.cease_to_exist(ref)
            continue
        state.leave_board(ref)
        ref.damage = 0
        owner.banishment.append(instance_id)
        state._emit(f"{state.db[ref.card_id].name} is banished")
    return None


def _detach(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """435 -- unlink. 435.1.a.1: doing this to an unattached card does
    nothing."""
    ids, request = _targets(state, effect.selector, ctx, "Detach")
    if request:
        return request
    for instance_id in ids:
        ref = state.cards[instance_id]
        if ref.attached_to is None:
            continue
        ref.attached_to = None
        state._emit(f"{state.db[ref.card_id].name} is detached")
    return None


def _counter(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """425 -- negate the item this one is answering.

    The chain resolves newest-first (340.1), so the item being countered is
    the one directly beneath the countering spell: its controller played it
    in response. 425.1.a clears it, 425.1.a.1 sends a countered card to the
    trash, and 425.1.b means no "when you play" trigger fires for it.
    """
    target = None
    for index in range(len(state.chain) - 1, -1, -1):
        if state.chain[index].instance_id != ctx.source:
            target = index
            break
    if target is None:
        return None
    item = state.chain.pop(target)
    ref = state.cards[item.instance_id]
    name = state.db[ref.card_id].name
    if item.kind == "card":
        # 425.1.a.1 -- cleared cards are placed in the trash.
        state.players[ref.owner].trash.append(item.instance_id)
    state._emit(f"{name} is countered")
    return None


def _create_token(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """439 -- create `count` tokens for the effect's controller (182/183)."""
    from engine.zones import BASE_LOCATION

    location = BASE_LOCATION
    if effect.where == "here" and ctx.source is not None:
        source = state.cards.get(ctx.source)
        # 184.2 -- "here" means where the creating card is; a card in the base
        # creates at the base, which is the same default.
        if source is not None and source.location:
            location = source.location
    for _ in range(effect.count):
        state.create_token(effect.token, ctx.controller, location,
                           effect.exhausted)
    return None


def _channel(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """430 -- channel runes, as many as the Rune Deck allows (430.3)."""
    for player in _resolve_player(effect.who, ctx.controller):
        got = state.channel(player, effect.count, effect.exhausted)
        state._emit(
            f"P{player} channels {got} rune(s)"
            + (" exhausted" if effect.exhausted else "")
        )
    return None


def _stun(state, effect, ctx: EffectContext) -> ChoiceRequest | None:
    """423 -- Stun each chosen unit. 423.1.a.1 makes it a no-op on a unit
    that is already stunned."""
    ids, request = _targets(state, effect.selector, ctx, "Stun")
    if request:
        return request
    for instance_id in ids:
        state.stun(state.cards[instance_id])
    return None


def _exhaust(state, effect: Exhaust, ctx: EffectContext) -> ChoiceRequest | None:
    ids, request = _targets(state, effect.selector, ctx, "Exhaust")
    if request:
        return request
    for instance_id in ids:
        state.cards[instance_id].exhausted = True
    return None


def _ready(state, effect: Ready, ctx: EffectContext) -> ChoiceRequest | None:
    ids, request = _targets(state, effect.selector, ctx, "Ready")
    if request:
        return request
    for instance_id in ids:
        state.cards[instance_id].exhausted = False
    return None


def _gain_points(state, effect: GainPoints, ctx: EffectContext) -> ChoiceRequest | None:
    """194.1.c."""
    for player in _resolve_player(effect.who, ctx.controller):
        state.players[player].points += effect.count
        state._emit(f"P{player} gains {effect.count} point(s)")
    return None


def _units_enter_ready(state, effect: UnitsEnterReady, ctx: EffectContext) -> ChoiceRequest | None:
    state.units_enter_ready.add(ctx.controller)
    state._emit(f"P{ctx.controller}'s units enter ready this turn")
    return None


HANDLERS: dict[type, Callable] = {
    Draw: _draw,
    Discard: _discard,
    Deal: _deal,
    Kill: _kill,
    Double: _double,
    EquipToMe: _equip_to_me,
    ModifyMight: _modify_might,
    Prevent: _prevent,
    Swap: _swap,
    PlaceBuff: _place_buff,
    SpendBuff: _spend_buff,
    GrantKeyword: _grant_keyword,
    AddEnergy: _add_energy,
    AddPower: _add_power,
    ReturnToHand: _return_to_hand,
    LookAtTop: _look_at_top,
    Attach: _attach,
    Recycle: _recycle,
    Reveal: _reveal,
    Heal: _heal,
    Banish: _banish,
    Detach: _detach,
    Counter: _counter,
    CreateToken: _create_token,
    Channel: _channel,
    Stun: _stun,
    Exhaust: _exhaust,
    Ready: _ready,
    GainPoints: _gain_points,
    UnitsEnterReady: _units_enter_ready,
}


def execute(state, effect: Effect, ctx: EffectContext) -> ChoiceRequest | None:
    """Run one primitive. Returns a ChoiceRequest if a decision is needed."""
    handler = HANDLERS.get(type(effect))
    if handler is None:
        raise NotImplementedError(
            f"no interpreter for {type(effect).__name__} -- add it to "
            f"cards/primitives.py, and update DSL.md first"
        )
    return handler(state, effect, ctx)
