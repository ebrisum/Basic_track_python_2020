"""The DSL interpreter: executes `cards.dsl` effects against a game state.

One function per primitive, dispatched from `execute`. An effect that needs a
player decision returns a `ChoiceRequest` instead of acting; the engine parks
it, offers the options through `legal_actions()`, and calls back with the
choice. That keeps every decision inside the frozen agent-facing interface.

This module knows the rules of each Game Action but nothing about specific
cards; `cards/scripts/` supplies the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from cards.dsl import (
    AddEnergy,
    AddPower,
    Attach,
    Buff,
    ChoiceRequest,
    Deal,
    Discard,
    Draw,
    Duration,
    Effect,
    Exhaust,
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


def _resolve_player(who: Who, controller: int) -> list[int]:
    if who is Who.YOU:
        return [controller]
    if who is Who.OPPONENT:
        return [1 - controller]
    return [controller, 1 - controller]


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


def candidates(state, selector: Selector, controller: int, source: int | None) -> list[int]:
    """All instance ids a selector matches, in canonical order."""
    if selector.scope == "self":
        return [source] if source is not None else []
    return sorted(
        ref.instance_id
        for ref in state.cards.values()
        if matches(state, ref, selector, controller, source)
    )


def _targets(
    state, selector: Selector, ctx: EffectContext, prompt: str
) -> tuple[list[int], ChoiceRequest | None]:
    """Resolve a selector to concrete instance ids, or ask the player.

    Returns `(ids, None)` when no decision is needed, or `([], request)` when
    the controller must choose.
    """
    if ctx.chosen:
        return list(ctx.chosen), None

    options = candidates(state, selector, ctx.controller, ctx.source)
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
        ref = state.cards[instance_id]
        ref.damage += effect.amount
        state._emit(
            f"{state.db[ref.card_id].name} is dealt {effect.amount} "
            f"({ref.damage}/{state.might_of(ref)})"
        )
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
            options = candidates(state, scoped, player, ctx.source)
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


def _buff(state, effect: Buff, ctx: EffectContext) -> ChoiceRequest | None:
    """426 Buff / 701 -- a Might modifier."""
    ids, request = _targets(state, effect.selector, ctx, f"Buff +{effect.might} Might")
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
        ref.location = None
        ref.damage = 0
        ref.exhausted = False
        ref.might_this_turn = 0
        ref.is_attacker = ref.is_defender = False
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
    Buff: _buff,
    GrantKeyword: _grant_keyword,
    AddEnergy: _add_energy,
    AddPower: _add_power,
    ReturnToHand: _return_to_hand,
    LookAtTop: _look_at_top,
    Attach: _attach,
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
