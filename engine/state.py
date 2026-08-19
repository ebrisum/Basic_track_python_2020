"""The Riftbound game state.

Implements the frozen agent-facing contract in `engine.interface` for the 1v1
(Duel) mode of play (485). Rule citations are inline; anything approximated is
marked `APPROX` here and written up in RULES_QUESTIONS.md.

Determinism: all randomness comes from `self._rng`, seeded from the game seed.
Nothing here touches the `random` module's global generator.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from enum import Enum

from cards.database import CardData, CardDatabase
from cards.dsl import (
    Ability,
    ChoiceRequest,
    DiscardCost,
    Duration,
    ExhaustSelf,
    PayEnergy,
    PayPower,
    RecycleFromTrash,
    TriggerKind,
)
from cards.primitives import EffectContext, candidates, execute, targets_of
from cards.repeat import repeat_cost
from cards.gear import equipment_profile
from cards.scripts import (
    abilities_of_kind,
    activatable_indices,
    activated_abilities,
    script_for,
)
from engine.chain import (
    ChainItem,
    Showdown,
    ability_can_activate,
    can_play,
    resolves_immediately,
    timing_label,
)
from engine.actions import (
    Action,
    ActivateAbility,
    AssignDamageTo,
    ChannelRune,
    ChooseBattlefield,
    ChooseTarget,
    Concede,
    ExhaustRuneForEnergy,
    HideCard,
    Mulligan,
    PassPhase,
    PlayCard,
    RecycleRuneForPower,
    StandardMove,
)
from engine.interface import PLAYERS
from engine.zones import (
    ANY_DOMAIN,
    BASE_LOCATION,
    Battlefield,
    CardRef,
    PlayerState,
    RunePool,
)

VICTORY_SCORE = 8  # 194.3 / 485.3
OPENING_HAND = 4  # 116
MAX_MULLIGAN = 2  # 117.1
CHANNEL_PER_TURN = 2  # 315.3.b
BATTLEFIELDS_PER_PLAYER = 3  # 485.4.a
BATTLEFIELDS_IN_PLAY = 2  # 485.4


class Phase(str, Enum):
    """Turn phases (314-317) plus the interactive setup steps (110-118)."""

    SETUP_BATTLEFIELD = "setup_battlefield"
    SETUP_MULLIGAN = "setup_mulligan"
    AWAKEN = "awaken"  # 315.1
    BEGINNING = "beginning"  # 315.2
    CHANNEL = "channel"  # 315.3
    DRAW = "draw"  # 315.4
    MAIN = "main"  # 316
    COMBAT_ASSIGN = "combat_assign"  # 465.2.c
    CHOOSING = "choosing"  # 355.2 -- an effect is waiting on a choice
    CHAIN = "chain"  # 309.1 -- a Chain exists; priority is passing
    SHOWDOWN = "showdown"  # 341-348 -- Showdown Open; the Focus holder acts
    ENDING = "ending"  # 317
    GAME_OVER = "game_over"


def bf_location(index: int) -> str:
    return f"bf:{index}"


@dataclass
class CombatState:
    """A combat in progress (459-466)."""

    battlefield: int
    attacker: int
    defender: int
    # Whose turn it is to assign damage, and how much they have left.
    assigning: int | None = None
    remaining: int = 0
    attacker_assigned: bool = False
    defender_assigned: bool = False

    def copy(self) -> "CombatState":
        return replace(self)   # every field is a scalar

    def clone_key(self) -> tuple:
        return (
            self.battlefield,
            self.attacker,
            self.defender,
            self.assigning,
            self.remaining,
            self.attacker_assigned,
            self.defender_assigned,
        )


@dataclass
class RiftboundState:
    """Mutable game state implementing `engine.interface.GameState`."""

    db: CardDatabase
    seed: int
    players: list[PlayerState] = field(default_factory=list)
    cards: dict[int, CardRef] = field(default_factory=dict)
    battlefields: list[Battlefield] = field(default_factory=list)
    # Each player's 3 candidate battlefields during setup (485.4.a).
    battlefield_choices: list[list[str]] = field(default_factory=list)

    phase: Phase = Phase.SETUP_BATTLEFIELD
    turn_player: int = 0
    first_player: int = 0
    _current_player: int = 0
    turn_number: int = 0
    combat: CombatState | None = None
    # 327 The Chain, 311-313 Priority and Focus, 341 Showdowns.
    chain: list = field(default_factory=list)
    showdown: Showdown | None = None
    priority: int | None = None
    focus: int | None = None
    chain_passes: int = 0
    winner: int | None = None
    conceded: int | None = None
    log: list[str] = field(default_factory=list)
    # Set by setup; the die roll result is kept for display (115).
    die_roll: tuple[int, int] = (0, 0)
    _mulliganed: list[bool] = field(default_factory=lambda: [False, False])
    # 649 Conceding is a real rule, but a random policy that may concede on any
    # turn produces meaningless statistics. It is therefore offered only in
    # interactive play; batch simulation leaves it off. See RQ-9.
    allow_concede: bool = False
    # Effect resolution: a queue of (effect, context) pushed when a card or
    # ability resolves, drained by `_resolve_effects`.
    pending: list = field(default_factory=list)
    awaiting: ChoiceRequest | None = None
    # Which players' units enter ready this turn (Confront).
    units_enter_ready: set = field(default_factory=set)
    # Instances whose ACCELERATE cost was paid while being played (805.2.b).
    accelerated: set = field(default_factory=set)
    # Phase to return to once the effect queue drains.
    _resume_phase: "Phase | None" = None
    # 354.4 -- the automatic phase a chain interrupted, so the turn can carry
    # on through Channel, Draw and the rest once the chain empties.
    _interrupted_phase: "Phase | None" = None
    # 424 Reveal -- instances shown to all players while in their owner's hand.
    # Revealing does not move the card (424.1.a), so this is a record of what
    # was *seen*, not a zone. It is deliberately never pruned: `knowledge` and
    # the observation both re-check where the instance actually is now, which
    # cannot go stale, where a pruning hook on every zone move could.
    revealed_in_hand: set = field(default_factory=set)
    _rng: random.Random = field(default_factory=random.Random)

    # ------------------------------------------------------------- cloning

    def __deepcopy__(self, memo):
        """A targeted clone, because search spends almost all its time here.

        Profiling a greedy game showed **89% of the runtime inside
        `copy.deepcopy`** -- 7.8 million object copies for 1,844 clones. The
        agent clones the whole state once per legal action, and the generic
        deepcopy walks every card, every list and every string in the log.

        Nothing in this state graph needs that. Every mutable component holds
        only scalars, strings, tuples and collections of ints, so each one can
        be copied in a single shallow pass; the card database is immutable and
        shared by reference (it already returns itself from `__deepcopy__`).

        This is `__deepcopy__` rather than a `clone()` method so that agents
        keep calling `copy.deepcopy(state)` and get the fast path for free --
        no agent knows this happened.

        The correctness argument is not "it looks right": the committed
        replays hash every state in two full games and would diverge on any
        aliasing mistake, and `test_cloning.py` mutates every mutable field of
        a clone and asserts the original is untouched.
        """
        clone = RiftboundState.__new__(RiftboundState)
        memo[id(self)] = clone

        clone.db = self.db                       # immutable, deliberately shared
        clone.seed = self.seed
        clone.players = [p.copy() for p in self.players]
        clone.cards = {k: v.copy() for k, v in self.cards.items()}
        clone.battlefields = [b.copy() for b in self.battlefields]
        clone.battlefield_choices = [list(c) for c in self.battlefield_choices]

        clone.phase = self.phase
        clone.turn_player = self.turn_player
        clone.first_player = self.first_player
        clone._current_player = self._current_player
        clone.turn_number = self.turn_number
        clone.combat = self.combat.copy() if self.combat is not None else None
        clone.chain = [item.copy() for item in self.chain]
        clone.showdown = self.showdown.copy() if self.showdown is not None else None
        clone.priority = self.priority
        clone.focus = self.focus
        clone.chain_passes = self.chain_passes
        clone.winner = self.winner
        clone.conceded = self.conceded
        clone.log = list(self.log)               # strings are immutable
        clone.die_roll = self.die_roll
        clone._mulliganed = list(self._mulliganed)
        clone.allow_concede = self.allow_concede
        # Effects are frozen dataclasses and shared; only the context carries
        # a mutable payload.
        clone.pending = [(effect, ctx.copy()) for effect, ctx in self.pending]
        clone.awaiting = replace(self.awaiting) if self.awaiting is not None else None
        clone.units_enter_ready = set(self.units_enter_ready)
        clone.accelerated = set(self.accelerated)
        clone.revealed_in_hand = set(self.revealed_in_hand)
        clone._resume_phase = self._resume_phase
        clone._interrupted_phase = self._interrupted_phase
        clone._last_from_trigger = self._last_from_trigger

        # A clone must not share a random stream with its original, or one
        # search branch silently consumes another's randomness.
        clone._rng = random.Random()
        clone._rng.setstate(self._rng.getstate())
        return clone

    # ---------------------------------------------------------------- helpers

    def card(self, instance_id: int) -> CardRef:
        return self.cards[instance_id]

    def data(self, instance_id: int) -> CardData:
        return self.db[self.cards[instance_id].card_id]

    def opponent(self, player: int) -> int:
        return 1 - player

    def units_at(self, location: str, player: int | None = None) -> list[CardRef]:
        out = [
            ref
            for ref in self.cards.values()
            if ref.location == location and self.db[ref.card_id].type == "unit"
        ]
        if player is not None:
            out = [r for r in out if r.controller == player]
        return sorted(out, key=lambda r: r.instance_id)

    def _emit(self, message: str) -> None:
        self.log.append(message)

    # ------------------------------------------------------- interface: five

    @property
    def current_player(self) -> int:
        return self._current_player

    def is_terminal(self) -> bool:
        return self.phase is Phase.GAME_OVER

    def returns(self) -> tuple[float, float]:
        if not self.is_terminal():
            raise ValueError("returns() is undefined for a non-terminal state")
        if self.winner is None:
            return (0.5, 0.5)
        return (1.0, 0.0) if self.winner == 0 else (0.0, 1.0)

    def legal_actions(self) -> list[Action]:
        if self.is_terminal():
            return []
        actions = self._legal_actions_inner()
        # Canonical order is required by the interface contract.
        return sorted(set(actions))

    def _legal_actions_inner(self) -> list[Action]:
        player = self._current_player
        if self.phase is Phase.CHOOSING and self.awaiting is not None:
            player = self.awaiting.player
        if self.phase is Phase.SETUP_BATTLEFIELD:
            return [
                ChooseBattlefield(i)
                for i in range(len(self.battlefield_choices[player]))
            ]
        if self.phase is Phase.SETUP_MULLIGAN:
            return self._mulligan_actions(player)
        if self.phase is Phase.CHOOSING:
            assert self.awaiting is not None
            return [ChooseTarget(i) for i in self.awaiting.options]
        if self.phase is Phase.COMBAT_ASSIGN:
            return self._assign_actions(player)
        if self.phase in (Phase.MAIN, Phase.CHAIN, Phase.SHOWDOWN):
            return self._window_actions(player)
        return [PassPhase()]

    # ---------------------------------------------------------- play windows

    def timing(self) -> str:
        """The turn's state, one of the four names at 310."""
        return timing_label(self.chain, self.showdown)

    def _window_actions(self, player: int) -> list[Action]:
        """Everything legal in the current window (310).

        The same builder serves Main Phase, a Chain, and a Showdown; what
        differs is which cards pass the timing test and whether board actions
        (moves, standard plays) are available at all.
        """
        state = self.players[player]
        neutral_open = self.phase is Phase.MAIN
        actions: list[Action] = [PassPhase()]
        if self.allow_concede and neutral_open:
            actions.append(Concede())

        # Play a card from hand or the Champion Zone (108.3.d).
        for instance_id in state.hand + state.champion_zone:
            card = self.db[self.cards[instance_id].card_id]
            if card.type not in ("unit", "spell", "gear"):
                continue
            if not can_play(card, player, self.turn_player, self.chain, self.showdown):
                continue
            # 355.8 -- a spell with no valid choice for one of its targets
            # cannot be put on the chain, so it is not a legal action.
            if not self._spell_has_targets(self.cards[instance_id]):
                continue
            if self._can_afford_play(self.cards[instance_id], player):
                actions.append(PlayCard(instance_id))
                # 805.2 -- ACCELERATE is an optional additional cost paid as
                # part of playing the unit, never once it is on the board.
                if card.type == "unit" and card.has_accelerate:
                    if self._can_afford_play(self.cards[instance_id], player,
                                             accelerate=True):
                        actions.append(PlayCard(instance_id, accelerate=True))
                # 820.1 -- Repeat is an optional additional cost, offered only
                # when the card prints one the engine can read and the player
                # can afford it (820.1.c.1 / 358).
                if repeat_cost(card) is not None and self._can_afford_play(
                    self.cards[instance_id], player, repeat=True
                ):
                    actions.append(PlayCard(instance_id, repeat=True))

        # Activated abilities (376, 145.2). Their own printed timing governs,
        # which is why a rune seal's REACTION ability works inside a chain.
        #
        # Iterating the player's zone lists rather than every card instance:
        # this runs on every ply of every search node, and scanning all ~108
        # instances to find the ~4 on the board was the largest remaining cost
        # in ISMCTS. `test_cloning.py` pins the invariant this relies on --
        # that base + channeled_runes accounts for every card with a location.
        for instance_id in state.base + state.channeled_runes:
            ref = self.cards[instance_id]
            if ref.controller != player or ref.location is None:
                continue
            card = self.db[ref.card_id]
            # 718.2 / 721.2 -- an attached card's printed Rules Text is
            # Inactive and "cannot be activated", so an equipped gear stops
            # offering its own Equip ability. Without this an Equipment could
            # re-target itself onto any other unit, every turn, for its cost.
            live = activatable_indices(card, ref.attached_to is not None)
            abilities = activated_abilities(card)
            for index in live:
                ability = abilities[index]
                if not ability_can_activate(
                    card, ability, player, self.turn_player, self.chain, self.showdown
                ):
                    continue
                if self._can_pay_ability(ref, ability):
                    actions.append(ActivateAbility(ref.instance_id, index))

        # 421 / 811.1.b -- Hide. On your turn in an Open State, pay one power
        # of any domain to put a HIDDEN card facedown at a battlefield you
        # control that has no facedown card already.
        if neutral_open and player == self.turn_player:
            for instance_id in state.hand + state.champion_zone:
                card = self.db[self.cards[instance_id].card_id]
                if not card.has_hidden:
                    continue
                if not state.pool.can_pay(0, [ANY_DOMAIN]):   # [A]: any one power
                    continue
                for bf in self.battlefields:
                    if bf.controller != player:
                        continue
                    if self._facedown_at(bf.index) is not None:
                        continue
                    actions.append(HideCard(instance_id, bf.index))

        # 811.1.b -- "Beginning on the next turn, this gains [Reaction] and
        # you may play this, ignoring its base cost."
        for ref in self.cards.values():
            if ref.hidden_at is None or ref.controller != player:
                continue
            if self.turn_number <= ref.hidden_on_turn:
                continue          # not until the next turn
            if not can_play(self.db[ref.card_id], player, self.turn_player,
                            self.chain, self.showdown, reaction_override=True):
                continue
            if not self._hidden_play_has_targets(ref):
                continue          # 811.1.d
            actions.append(PlayCard(ref.instance_id))

        # Rune abilities (164.2) are both Reactions, so they stay available
        # inside a chain and during showdowns.
        for instance_id in state.channeled_runes:
            ref = self.cards[instance_id]
            if not ref.exhausted:
                actions.append(ExhaustRuneForEnergy(instance_id))
            actions.append(RecycleRuneForPower(instance_id))

        # Board actions are Neutral-Open only: the Standard Move cannot be
        # performed in a Closed State or during a Showdown (144.1.b-c).
        if neutral_open and player == self.turn_player:
            for ref in self.cards.values():
                if ref.controller != player or ref.exhausted:
                    continue
                if self.db[ref.card_id].type != "unit" or ref.location is None:
                    continue
                if ref.location == BASE_LOCATION:
                    for bf in self.battlefields:
                        actions.append(
                            StandardMove(ref.instance_id, bf_location(bf.index))
                        )
                else:
                    actions.append(StandardMove(ref.instance_id, BASE_LOCATION))
                    if self.db[ref.card_id].has_ganking:  # 144.4.c / 810
                        for bf in self.battlefields:
                            if bf_location(bf.index) != ref.location:
                                actions.append(
                                    StandardMove(ref.instance_id, bf_location(bf.index))
                                )
        return actions

    # -------------------------------------------------------------- mulligan

    def _mulligan_actions(self, player: int) -> list[Action]:
        """117.1 -- choose up to two cards to set aside.

        Offered as: keep everything, or set aside any 1 or 2 cards.
        """
        hand = self.players[player].hand
        options: list[Action] = [Mulligan(())]
        for i, first in enumerate(hand):
            options.append(Mulligan((first,)))
            for second in hand[i + 1 :]:
                options.append(Mulligan(tuple(sorted((first, second)))))
        return options

    # ------------------------------------------------------------ main phase

    # ---------------------------------------------------------------- combat

    def _assign_actions(self, player: int) -> list[Action]:
        """465.2.c damage assignment, ordered by Tank and Backline.

        815.1.c.2 -- "Units without Tank are invalid assignments until all
        units with Tank have lethal damage assigned to them."
        826.4.b -- the mirror: "Units with Backline are invalid assignments
        until all units without Backline have lethal damage assigned."

        (This previously cited 626.1.d.4, which is not the rule.)
        """
        assert self.combat is not None
        target_player = self.opponent(player)
        targets = self.units_at(bf_location(self.combat.battlefield), target_player)
        alive = [r for r in targets if not self._has_lethal(r)]
        if not alive:
            return [PassPhase()]
        # 815 Tank must be assigned lethal damage first; 826 Backline last.
        tanks = [r for r in alive if self.db[r.card_id].has_tank]
        backline = [r for r in alive if self.db[r.card_id].has_backline]
        middle = [r for r in alive if r not in tanks and r not in backline]
        pool = tanks or middle or backline
        return [AssignDamageTo(r.instance_id) for r in pool]

    def might_of(self, ref: CardRef) -> int:
        """Current Might: printed, plus buffs (426/701), plus Assault (807).

        Assault counts printed and granted instances; a unit given ASSAULT 3
        by Cleave attacks for +3.
        """
        card = self.db[ref.card_id]
        assault = card.assault
        shield = card.shield
        # 807.2 / 814.2 -- granted instances sum with the printed one.
        for name, value, _duration in ref.granted_keywords:
            if name == "Assault":
                assault += value
            elif name == "Shield":
                shield += value
        # 807.1.c / 814.1.c -- Assault applies while attacking, Shield while
        # defending. A unit is never both, so these never stack.
        bonus = (assault if ref.is_attacker else 0) + (
            shield if ref.is_defender else 0
        )
        # 718.4 / 137.3 -- an Attached card modulates the Top-Most card's
        # Might by its printed *Might Bonus*, which is not the card's `might`
        # field: B.F. Sword prints "+3 Might" and carries might 0. Using the
        # field silently gave most Equipment the wrong number.
        attached = 0
        for gear in self.cards.values():
            if gear.attached_to != ref.instance_id:
                continue
            profile = equipment_profile(self.db[gear.card_id])
            if profile is not None and profile.might_bonus is not None:
                attached += profile.might_bonus
        # 703 -- each Buff counter contributes exactly +1 Might.
        return (card.might + ref.might_this_turn + ref.might_permanent
                + ref.buffs + bonus + attached)

    def _cheapest_deflect(self, ref: CardRef, here: str | None = None) -> int:
        """The least Deflect (809) this card's targets can be made to cost.

        One target set has to be affordable for the play to be legal (358), so
        the gate is priced off the cheapest one. Per selector, that is the
        candidate whose Deflect tax is lowest -- usually zero, since most
        permanents have none.
        """
        card = self.db[ref.card_id]
        if card.type != "spell":
            return 0
        attached = ref.attached_to is not None
        total = 0
        for _a, _e, selector in targets_of(
            abilities_of_kind(card, TriggerKind.ON_RESOLVE, attached)
        ):
            options = candidates(self, selector, ref.controller,
                                 ref.instance_id, here)
            if not options:
                continue
            total += min(
                self.deflect_cost([i], ref.controller) for i in options
            )
        return total

    def _can_afford_play(self, ref: CardRef, player: int,
                         accelerate: bool = False,
                         repeat: bool = False) -> bool:
        """356-358 -- can the total cost be paid for *some* legal target set?

        Base cost plus the cheapest Deflect the card's targets can incur. A
        card whose only legal target taxes more than the player can pay is not
        a legal play, which is 358 Check legality doing its job at the point
        where the action is offered.
        """
        card = self.db[ref.card_id]
        energy, domains = card.energy, list(card.power_domains)
        if accelerate:
            energy += 1
            domains += list(card.accelerate_power_domains)
        if repeat:
            more = repeat_cost(card)               # 820.1.c.1
            if more is None:
                return False
            energy += more[0]
            domains += list(more[1])
        extra = self._cheapest_deflect(ref)
        return self.players[player].pool.can_pay(
            energy, domains + [ANY_DOMAIN] * extra
        )

    def _spell_has_targets(self, ref: CardRef, here: str | None = None) -> bool:
        """355.8 -- "In order to put a spell or ability on the chain, valid
        choices must be made for all targets." One unsatisfiable target is
        enough to bar the play, so a spell with nothing legal to hit is not a
        legal action.

        811.1.d says the same thing again for Hidden, narrowed to that
        battlefield: pass `here` and the same check gates the hidden play.

        Only spells are gated. A permanent's "when you play me" effect belongs
        to a triggered ability, which 383 puts on the chain as its own item
        later; its targets are declared then, and 355.6 lets an unfulfillable
        choice simply do nothing.

        The DSL has no "you may choose" flag, so an optional target would be
        treated as required here. No scripted card has one; RQ-14 records it.
        """
        card = self.db[ref.card_id]
        if card.type != "spell":
            return True
        attached = ref.attached_to is not None
        for _key, selector in [
            ((a, e), sel) for a, e, sel in
            targets_of(abilities_of_kind(card, TriggerKind.ON_RESOLVE, attached))
        ]:
            if not candidates(self, selector, ref.controller,
                              ref.instance_id, here):
                return False
        return True

    def _hidden_play_has_targets(self, ref: CardRef) -> bool:
        """811.1.d -- the same gate, narrowed to the hidden battlefield."""
        return self._spell_has_targets(ref, bf_location(ref.hidden_at))

    def _facedown_at(self, index: int) -> CardRef | None:
        """811.1.b -- at most one facedown card per battlefield."""
        for ref in self.cards.values():
            if ref.hidden_at == index:
                return ref
        return None

    def buff(self, ref, ignore_cap: bool = False) -> bool:
        """426 Buff -- place a Buff counter, and report whether it landed.

        702.3 / 426.1.b.1: a unit already holding a buff does not get another.
        426.1.b.2 lets an effect grant permission to be buffed anyway, which
        is what `ignore_cap` is for.

        The **return value is the point**. 426.1.c: "Units with Buff Counters
        can still be chosen for actions that Buff units, but will not be
        Buffed as part of the execution." So "Buff a unit. Then, if it was
        buffed this way, draw a card" must not draw when the chosen unit was
        already buffed, and "when you buff me" must not trigger. A `buff()`
        that only acted could not express either.
        """
        if ref.buffs and not ignore_cap:
            return False
        ref.buffs += 1
        self._emit(f"{self.db[ref.card_id].name} is buffed")
        return True

    def spend_buff(self, ref, spender: int | None = None) -> bool:
        """702.2.b -- remove a single Buff counter, reporting success.

        702.2.b.1: nothing to spend from an unbuffed unit. 702.2.b.2: a player
        may only spend buffs on units they control, which is why `spender` is
        checked rather than assumed.
        """
        if not ref.buffs:
            return False
        if spender is not None and ref.controller != spender:
            return False
        ref.buffs -= 1
        self._emit(f"P{ref.controller} spends a buff on {self.db[ref.card_id].name}")
        return True

    def stun(self, ref: CardRef) -> bool:
        """423 -- Stun a unit. Returns whether the status actually changed.

        423.1.a.1: a Stunned unit cannot be Stunned again, and the return
        value is why that matters -- "when you stun an enemy unit" triggers
        must not fire on a unit that was already stunned.
        """
        if ref.stunned or self.db[ref.card_id].type != "unit":
            return False
        ref.stunned = True
        self._emit(f"{self.db[ref.card_id].name} is stunned")
        return True

    def combat_might(self, player: int, location: str) -> int:
        """465.2.c -- the Might a side contributes as combat damage.

        423.1.b: "A Stunned Unit does not contribute its might to damage in
        the combat damage step." Deliberately not folded into `might_of`: the
        rule is about contributing damage, not about being easier to kill, so
        a stunned unit still has its full Might when lethal damage is checked
        against it.
        """
        return sum(
            self.might_of(ref)
            for ref in self.units_at(location, player)
            if not ref.stunned
        )

    def _has_lethal(self, ref: CardRef) -> bool:
        """142.4.a -- lethal damage is nonzero damage >= Might.

        437.5.a raises the bar by the Prevent Value still tracked on the unit:
        "a unit with 2 [M] and 'prevent the first 3 damage' would need to be
        assigned 5 damage in order to have lethal damage assigned to it."
        437.5.b makes "All" never lethal at any amount.
        """
        if ref.prevent is None:
            return False                              # 437.5.b
        return ref.damage > 0 and ref.damage >= self.might_of(ref) + ref.prevent

    def deal_damage(self, ref: CardRef, amount: int) -> int:
        """417 Deal, through 437 Prevent. Returns the damage actually marked.

        437.2 replaces the damage with the same amount reduced by the Prevent
        Value; 437.3 spends the Prevent Value by what it absorbed, and 437.3.a
        expires it at zero. 437.4 is why the return value matters: damage
        entirely prevented "is not considered to have been dealt to it at
        all", so a linked instruction keyed on damage being dealt must be able
        to tell.
        """
        if amount <= 0:
            return 0
        if ref.prevent is None:                       # 437.1.b.1.b -- "All"
            self._emit(f"{self.db[ref.card_id].name} prevents all {amount}")
            return 0
        absorbed = min(ref.prevent, amount)
        if absorbed:
            ref.prevent -= absorbed                   # 437.3 / 437.3.a
            self._emit(f"{self.db[ref.card_id].name} prevents {absorbed}")
        dealt = amount - absorbed                     # 437.2.a -- never < 0
        if dealt:
            ref.damage += dealt
            self._emit(
                f"{self.db[ref.card_id].name} is dealt {dealt} "
                f"({ref.damage}/{self.might_of(ref)})"
            )
        return dealt

    # ------------------------------------------------- effects and abilities

    def _can_pay_ability(self, ref: CardRef, ability: Ability) -> bool:
        """201-204 -- an ability is only legal if every cost can be paid."""
        state = self.players[ref.controller]
        for cost in ability.costs:
            if isinstance(cost, ExhaustSelf):
                if ref.exhausted:
                    return False
            elif isinstance(cost, DiscardCost):
                if len(state.hand) < cost.count:
                    return False
            elif isinstance(cost, RecycleFromTrash):
                # 416.3 -- the recycle must be completable.
                if len(state.trash) < cost.count:
                    return False
            elif isinstance(cost, PayPower):
                if not state.pool.can_pay(0, [cost.domain] * cost.count):
                    return False
            elif isinstance(cost, PayEnergy):
                if state.pool.energy < cost.count:
                    return False
            else:
                return False
        return True

    def _pay_ability(self, ref: CardRef, ability: Ability) -> None:
        state = self.players[ref.controller]
        for cost in ability.costs:
            if isinstance(cost, ExhaustSelf):
                ref.exhausted = True
            elif isinstance(cost, DiscardCost):
                for instance_id in list(state.hand[: cost.count]):
                    state.hand.remove(instance_id)
                    self.players[self.cards[instance_id].owner].trash.append(instance_id)
            elif isinstance(cost, RecycleFromTrash):
                for instance_id in list(state.trash[: cost.count]):
                    state.trash.remove(instance_id)
                    state.main_deck.append(instance_id)  # 416.1 -- to the bottom
            elif isinstance(cost, PayPower):
                state.pool.pay(0, [cost.domain] * cost.count)
            elif isinstance(cost, PayEnergy):
                state.pool.pay(cost.count, [])

    def _queue(self, ability: Ability, controller: int, source: int | None,
               restrict_location: str | None = None,
               declared: dict | None = None, ability_index: int = 0,
               execution: int = 0) -> None:
        """Push an ability's effects onto the resolution queue.

        `restrict_location` carries 811.1.d.2 down to every selector the
        ability resolves: when the source was played from Hidden, its targets
        must be chosen from among options at that battlefield.

        `declared` holds targets chosen when the item was played (355.8),
        keyed by (ability index, effect index). Each is **re-checked against
        the board now**: 359.3.e.5 says an illegal target is unaffected, so a
        target that has since left is dropped and its instruction skipped,
        rather than the effect being re-aimed at something still legal.
        """
        for effect_index, effect in enumerate(ability.effects):
            ctx = EffectContext(controller=controller, source=source,
                                restrict_location=restrict_location)
            if declared is not None:
                key = (execution, ability_index, effect_index)
                if key in declared:
                    selector = getattr(effect, "selector", None)
                    legal = candidates(self, selector, controller, source,
                                       restrict_location) if selector else []
                    still = tuple(i for i in declared[key] if i in legal)
                    ctx.targets_declared = True
                    ctx.chosen = still
                    if declared[key] and not still:
                        self._emit(
                            f"{self.db[self.cards[source].card_id].name}'s target "
                            f"is no longer legal; that instruction is skipped "
                            f"(359.3.e.5)"
                        ) if source in self.cards else None
            self.pending.append((effect, ctx))

    def top_most(self, instance_id: int) -> int:
        """719 -- the Top-Most Card of the attachment stack `instance_id` is in.

        Walks up rather than assuming one link: 719 allows a chain, and a
        cycle would hang, so the walk is bounded.
        """
        current = instance_id
        for _ in range(8):
            host = self.cards[current].attached_to
            if host is None or host not in self.cards:
                return current
            current = host
        return current

    def _trigger(self, kind: TriggerKind, instance_id: int) -> bool:
        """383.3 -- put every triggered ability of `kind` on the Chain.

        "When a Condition is met, a Triggered Ability behaves like an
        Activated Ability and is placed on the Chain." So a trigger is not
        executed inside the game action that caused it: it becomes a chain
        item, its targets are declared when *it* is finalized (355.5.b), and
        383.3.c lets either player respond before it resolves.

        341 of 526 playable cards print trigger wording, so this is the most
        common mechanic in the game.

        383.3.d lets a controller order their simultaneous triggers. The
        engine orders them by instance id, deterministically; offering the
        choice would add an action to every multi-trigger board for a decision
        that rarely matters. Logged as an approximation in RULES_QUESTIONS.

        `from_trigger` is set so 346.1 keeps Focus with the controller: a
        trigger does not hand the showdown window to the opponent.
        """
        ref = self.cards[instance_id]
        attached = ref.attached_to is not None
        abilities = abilities_of_kind(self.db[ref.card_id], kind, attached)
        if not abilities:
            return False
        for index, _ability in enumerate(abilities):
            self.chain.append(ChainItem(
                kind="trigger",
                instance_id=instance_id,
                controller=ref.controller,
                ability_index=index,
                pending=True,
                from_trigger=True,
                trigger_kind=kind.value,
            ))
        return True

    def _fire(self, kind: TriggerKind, instance_id: int,
              restrict_location: str | None = None,
              declared: dict | None = None, execution: int = 0) -> None:
        """382 -- queue every ability of `kind` printed on this card.

        136.2.c -- "The abilities in the Effect Text section of a card are
        appended to the Rules Text of the card to which the card with the
        Effect Text is Attached." So an attached Equipment's ability is the
        *host's* ability, and "me" in it is the host.

        Warmog's Armor is the case that found this: "When I conquer, buff me"
        was buffing the gear, which is not a unit and cannot hold a buff
        (702). The state invariant caught it within a few random games.

        136.2.d's exception -- "this", or the attachment's own name, still
        refers to the attachment -- has no DSL representation yet. No scripted
        card needs it; RULES_QUESTIONS records it.
        """
        ref = self.cards[instance_id]
        source = self.top_most(instance_id)
        attached = ref.attached_to is not None
        for index, ability in enumerate(
            abilities_of_kind(self.db[ref.card_id], kind, attached)
        ):
            self._queue(ability, ref.controller, source, restrict_location,
                        declared, index, execution)

    def _resolve_effects(self) -> None:
        """Drain the effect queue, pausing whenever a choice is needed."""
        guard = 0
        while self.pending and self.awaiting is None:
            guard += 1
            if guard > 256:
                raise RuntimeError("effect resolution did not terminate")
            effect, ctx = self.pending[0]
            request = execute(self, effect, ctx)
            if request is not None:
                self.awaiting = request
                if self._resume_phase is None:
                    self._resume_phase = self.phase
                self.phase = Phase.CHOOSING
                self._current_player = request.player
                return
            self.pending.pop(0)

        if not self.pending and self.awaiting is None and self._resume_phase is not None:
            self.phase = self._resume_phase
            self._resume_phase = None
            self._current_player = self.turn_player

    def _apply_choice(self, action: ChooseTarget) -> None:
        """Feed a chosen instance back to whatever asked for it.

        Two things ask. 355.8 declares a chain item's targets as it is played,
        which is answered here by recording the choice on the item and
        resuming `_advance_targeting`. Everything else -- a Limited Action a
        player performs as an effect resolves (411.1), a choice in a
        non-public zone (355.10.a) -- is answered by resuming the effect.
        """
        if self.chain and self.chain[-1].pending:
            item = self.chain[-1]
            for key, _selector in self._target_slots(item):
                if key not in item.targets:
                    item.targets[key] = (action.instance_id,)
                    break
            self.awaiting = None
            if self._resume_phase is not None:
                self.phase = self._resume_phase
                self._resume_phase = None
            self._advance_targeting()
            return

        assert self.awaiting is not None and self.pending
        effect, ctx = self.pending[0]
        ctx.chosen = (action.instance_id,)
        self.awaiting = None
        request = execute(self, effect, ctx)
        if request is not None:
            # A multi-part effect (e.g. each player kills a gear) asks again.
            self.awaiting = request
            self._current_player = request.player
            return
        self.pending.pop(0)
        self._resolve_effects()

    # ----------------------------------------------------------------- apply

    def apply(self, action: Action) -> None:
        if self.is_terminal():
            raise ValueError("cannot apply an action to a terminal state")
        if action not in self.legal_actions():
            raise ValueError(f"illegal action {action!r} in phase {self.phase.value}")

        if isinstance(action, ChooseBattlefield):
            self._apply_choose_battlefield(action)
        elif isinstance(action, Mulligan):
            self._apply_mulligan(action)
        elif isinstance(action, PlayCard):
            self._apply_play(action)
        elif isinstance(action, StandardMove):
            self._apply_move(action)
        elif isinstance(action, ExhaustRuneForEnergy):
            self._apply_tap_energy(action)
        elif isinstance(action, RecycleRuneForPower):
            self._apply_recycle_power(action)
        elif isinstance(action, ActivateAbility):
            self._apply_activate(action)
        elif isinstance(action, ChooseTarget):
            self._apply_choice(action)
        elif isinstance(action, AssignDamageTo):
            self._apply_assign(action)
        elif isinstance(action, HideCard):
            self._apply_hide(action)
        elif isinstance(action, Concede):
            self._apply_concede()
        elif isinstance(action, PassPhase):
            self._apply_pass()
        else:
            raise ValueError(f"unhandled action {action!r}")

        self._cleanup()
        self._normalize_window()

    # ------------------------------------------------------------ setup steps

    def _apply_choose_battlefield(self, action: ChooseBattlefield) -> None:
        player = self._current_player
        chosen = self.battlefield_choices[player][action.index]
        index = len(self.battlefields)
        self.battlefields.append(
            Battlefield(index=index, card_id=chosen, provider=player)
        )
        self._emit(f"P{player} selects battlefield {self.db[chosen].name}")
        if player == self.first_player:
            self._current_player = self.opponent(player)
        else:
            # Both chosen (485.5) -- deal opening hands (116).
            for pid in (self.first_player, self.opponent(self.first_player)):
                self._draw(pid, OPENING_HAND)
            self.phase = Phase.SETUP_MULLIGAN
            self._current_player = self.first_player

    def _apply_mulligan(self, action: Mulligan) -> None:
        player = self._current_player
        state = self.players[player]
        set_aside = list(action.instance_ids)
        for instance_id in set_aside:
            state.hand.remove(instance_id)
        self._draw(player, len(set_aside))  # 117.2
        for instance_id in set_aside:  # 117.3 -- recycle to bottom (416.1)
            state.main_deck.append(instance_id)
        self._emit(f"P{player} mulligans {len(set_aside)}")
        self._mulliganed[player] = True

        if not all(self._mulliganed):
            self._current_player = self.opponent(player)
            return
        # 118 -- begin play with the First Player.
        self.turn_player = self.first_player
        self._current_player = self.first_player
        self.turn_number = 1
        self.phase = Phase.AWAKEN
        self._run_automatic_phases()

    # ----------------------------------------------------------- main actions

    def _apply_play(self, action: PlayCard) -> None:
        """The process of play, in the printed order (353-359).

        1. **354** move the card to the Chain; it is Pending.
        2. **355** make relevant choices, targets among them.
        3. **356** determine total cost -- which is where Deflect (809.1.d)
           attaches, because it is priced off the targets chosen in step 2.
        4. **357** pay.

        The engine used to pay first and target afterwards, which is why
        Deflect had nowhere to attach at all (RQ-19).
        """
        player = self._current_player
        state = self.players[player]
        ref = self.cards[action.instance_id]
        card = self.db[ref.card_id]

        from_hidden = ref.hidden_at
        if from_hidden is not None:
            # 811.1.b -- played from Hidden, "ignoring its base cost". The
            # card is already off the hand, so there is nothing to remove.
            ref.hidden_at = None
        elif action.instance_id in state.hand:
            state.hand.remove(action.instance_id)
        else:
            state.champion_zone.remove(action.instance_id)

        self._emit(
            f"P{player} plays {card.name}"
            + (" (accelerated)" if action.accelerate else "")
            + (" from hidden" if from_hidden is not None else "")
        )
        if action.accelerate:
            # 805.2.b -- paying the cost generates a delayed replacement
            # effect, so the unit enters ready even if it loses the keyword
            # during finalization. Recorded per instance, not per card.
            self.accelerated.add(action.instance_id)
        # 811.1.d -- recorded on the item, not on the state: the chain can
        # hold several cards, and a card played in response must not disturb
        # where an earlier hidden card is played to or makes its choices.
        item = ChainItem(
            kind="card", instance_id=action.instance_id, controller=player,
            pending=True, from_hidden=from_hidden, accelerated=action.accelerate,
            repeat=action.repeat,
        )
        self.chain.append(item)  # 354 -- this Closes the State
        self.chain_passes = 0
        self._break_showdown_pass_sequence()
        # 355.8 / 329.2 -- the item is Pending until targets are declared,
        # costs are determined and paid, and legality is checked. If a choice
        # is needed the turn parks here and resumes once it is answered.
        self._advance_targeting()

    def deflect_cost(self, target_ids, controller: int) -> int:
        """809 -- the extra Power an opponent's Deflect permanents demand.

        809.1.c: "Spells and abilities an opponent controls that target [me]
        cost an amount of Power equal to [Deflect Value] more to play ...
        **for each time they choose [me]**", so a permanent chosen twice taxes
        twice. 809.2 sums multiple instances on one object; 809.1.b.3 reads an
        omitted value as 1.

        Only opponents' permanents tax: your own Deflect unit is free to aim
        at.
        """
        total = 0
        for instance_id in target_ids:
            ref = self.cards.get(instance_id)
            if ref is None or ref.controller == controller:
                continue
            card = self.db[ref.card_id]
            if not card.has_deflect:
                continue
            total += card.deflect or 1     # 809.1.b.3
        return total

    def _base_cost(self, item) -> tuple[int, list[str]]:
        """356.1-356.2 -- the printed cost, before Deflect.

        811.1.b zeroes the base cost of a card played from Hidden; 805.1.a
        adds [1][C] when ACCELERATE was chosen.

        An **ability** item has no card cost: its own cost was paid by
        `_pay_ability` when it was activated (204.3.a), and charging the
        source card's play cost here would bill the player twice for using a
        permanent they already own.
        """
        if item.kind in ("ability", "trigger"):
            return 0, []
        card = self.db[self.cards[item.instance_id].card_id]
        if item.from_hidden is not None:
            return 0, []
        energy = card.energy
        domains = list(card.power_domains)
        if item.accelerated:
            # 805.1.a -- [1][C] on top of the printed cost.
            energy += 1
            domains += list(card.accelerate_power_domains)
        if item.repeat:
            # 820.1.c.1 -- "The Cost is an Additional Cost to be paid during
            # the steps of playing the spell or ability."
            extra = repeat_cost(card)
            if extra is not None:
                energy += extra[0]
                domains += list(extra[1])
        return energy, domains

    def _total_cost(self, item) -> tuple[int, list[str]]:
        """356 -- base cost plus mandatory additional costs.

        The Deflect share is paid in Power of any domain (809.1.c.1), which is
        `ANY_DOMAIN` here.
        """
        energy, domains = self._base_cost(item)
        chosen = [i for ids in item.targets.values() for i in ids]
        extra = self.deflect_cost(chosen, item.controller)
        return energy, domains + [ANY_DOMAIN] * extra

    def _advance_targeting(self) -> None:
        """355.8 -- declare every target before the item is Finalized.

        Walks the pending item's targeted selectors in order. A selector with
        one legal option is assigned without asking (355.10.d.2 keeps it a
        target, but there is no decision to offer); one with several parks a
        `ChoiceRequest`, exactly as resolution-time choices already do.
        """
        item = self.chain[-1] if self.chain else None
        if item is None or not item.pending:
            return
        here = bf_location(item.from_hidden) if item.from_hidden is not None else None
        for key, selector in self._target_slots(item):
            if key in item.targets:
                continue
            options = candidates(self, selector, item.controller,
                                 item.instance_id, here)
            # 358 -- a choice whose total cost cannot be paid fails the
            # legality check, so 809's tax narrows the offer rather than
            # producing an unpayable play.
            options = [i for i in options if self._affordable_with(item, key, i)]
            if not options:
                # 355.8 -- no valid choice. `legal_actions` gates this, so
                # reaching it means a target vanished between offer and play.
                item.targets[key] = ()
                continue
            if len(options) == 1:
                item.targets[key] = (options[0],)
                continue
            self.awaiting = ChoiceRequest(
                player=item.controller, options=tuple(options),
                prompt=f"Choose a target ({selector.describe()})",
            )
            if self._resume_phase is None:
                self._resume_phase = self.phase
            self.phase = Phase.CHOOSING
            self._current_player = item.controller
            return
        self._finish_playing(item)

    def _target_slots(self, item) -> list:
        """The item's targeted selectors, as ((ability, effect), selector).

        Only the item's **own** effects: a spell's ON_RESOLVE, an activated
        ability's own effects. A "when you play me" effect belongs to a
        *triggered ability*, which 383 puts on the chain as its own item later
        and which therefore declares its targets then. RQ-19 records that the
        second half is still resolved late.
        """
        ref = self.cards[item.instance_id]
        card = self.db[ref.card_id]
        if item.kind == "trigger":
            # 355.5.b -- a trigger's targets are declared when *it* is
            # finalized, not when the card that caused it was played.
            attached = ref.attached_to is not None
            found = abilities_of_kind(card, TriggerKind(item.trigger_kind), attached)
            abilities = ((found[item.ability_index],)
                         if item.ability_index < len(found) else ())
        elif item.kind == "ability":
            abilities = (activated_abilities(card)[item.ability_index],)
        elif card.type == "spell":
            abilities = abilities_of_kind(
                card, TriggerKind.ON_RESOLVE, ref.attached_to is not None
            )
        else:
            return []
        # 820.2.a -- a repeated item makes its choices twice, independently,
        # so each execution gets its own slots. The key carries the execution
        # index; execution 0 is the ordinary single pass.
        executions = 2 if item.repeat else 1
        return [((run, a, e), sel)
                for run in range(executions)
                for a, e, sel in targets_of(abilities)]

    def _affordable_with(self, item, key, instance_id: int) -> bool:
        """Whether the controller could still pay if `instance_id` were chosen.

        Optimistic about the targets not yet declared -- they are priced when
        their own turn comes -- and exact about the ones already fixed.
        """
        chosen = [i for k, ids in item.targets.items() if k != key for i in ids]
        chosen.append(instance_id)
        energy, domains = self._base_cost(item)
        extra = self.deflect_cost(chosen, item.controller)
        return self.players[item.controller].pool.can_pay(
            energy, domains + [ANY_DOMAIN] * extra
        )

    def _finish_playing(self, item) -> None:
        """356-359 -- determine the total cost, pay it, and Finalize.

        Costs are paid here rather than when the action arrived, because 356
        is step 3 and the targets that price Deflect are chosen in step 2.
        """
        if item.kind == "trigger":
            # 383.3.b -- a trigger has no base cost unless its text opens with
            # one, which no scripted card does yet. It is simply finalized,
            # and 383.3.c opens a window before it resolves.
            item.pending = False
            self._open_priority_window(item.controller)   # 337.4
            return
        energy, domains = self._total_cost(item)
        self.players[item.controller].pool.pay(energy, domains)   # 357 / 444
        item.pending = False
        card = self.db[self.cards[item.instance_id].card_id]
        player = item.controller
        if item.kind == "ability":
            ability = activated_abilities(card)[item.ability_index]
            # 337.2 -- an ability that Adds resources resolves immediately, and
            # 346.1 keeps Focus with its controller.
            if resolves_immediately(card, ability):
                self._resolve_top()
            else:
                self._open_priority_window(player)   # 337.4
            return

        # 337.2 -- a finalized Unit or Gear resolves immediately.
        if resolves_immediately(card):
            self._resolve_top()
        else:
            # 337.4 -- "the controller of the next item on the chain gains
            # Priority", and the item just played *is* the next to resolve. So
            # the caster gets the first response window, not the opponent.
            #
            # This was the opponent, which let them respond before the caster
            # could stack a second item of their own. 338.1.a.5 is explicit
            # that the first player with priority after creating the chain may
            # add "an additional item to the item that Started the Chain", and
            # 340.4 -- the same rule shape after a resolve -- was already
            # implemented this way. The engine was inconsistent with itself.
            self._open_priority_window(player)

    _last_from_trigger: bool = False

    def _resolve_top(self) -> None:
        """340.1 -- the newest Finalized item resolves in full."""
        if not self.chain:
            # Nothing to resolve: fall through to the window logic rather than
            # leaving the turn parked in a Closed state with an empty chain.
            self._after_chain_change()
            return
        item = self.chain.pop()
        self._last_from_trigger = item.from_trigger
        ref = self.cards[item.instance_id]
        card = self.db[ref.card_id]
        state = self.players[item.controller]

        if item.kind == "card":
            # 811.1.d.2 -- a card played from Hidden makes its choices at the
            # battlefield it was hidden at. Captured before either branch can
            # clear it, and handed to the play effect rather than consulted
            # after the fact.
            hidden_bf = item.from_hidden
            hidden_here = bf_location(hidden_bf) if hidden_bf is not None else None
            if card.type == "spell":
                state.trash.append(item.instance_id)  # 351.2
                # 820.1.d -- "execute the instructions of this chain item
                # one additional time during resolution", each execution
                # using the choices declared for it (820.2.a).
                for run in range(2 if item.repeat else 1):
                    self._fire(TriggerKind.ON_RESOLVE, item.instance_id,
                               hidden_here, item.targets, run)
            else:
                # 148.1.a.1 -- permanents enter the Base, unless 811.1.d.1
                # sends a card played from Hidden to its battlefield instead.
                ref.location = (
                    hidden_here if hidden_here is not None else BASE_LOCATION
                )
                # 143.4 -- units enter exhausted, unless ACCELERATE was paid
                # (805.1.a) or an effect says otherwise this turn.
                ref.exhausted = not (
                    item.instance_id in self.accelerated
                    or item.controller in self.units_enter_ready
                )
                self.accelerated.discard(item.instance_id)
                if card.type == "gear":
                    ref.exhausted = False
                state.base.append(item.instance_id)
                # 383.3 -- a play trigger goes on the Chain rather than
                # executing inside the play. 811.1.d.2's battlefield
                # restriction rides on the item.
                if self._trigger(TriggerKind.ON_PLAY, item.instance_id):
                    self.chain[-1].from_hidden = hidden_bf
        elif item.kind == "trigger":
            # 383.3 -- the trigger's own effects, with the targets declared
            # when this item was finalized (355.5.b) and re-checked now.
            attached = ref.attached_to is not None
            abilities = abilities_of_kind(
                card, TriggerKind(item.trigger_kind), attached
            )
            if item.ability_index < len(abilities):
                here = (bf_location(item.from_hidden)
                        if item.from_hidden is not None else None)
                self._queue(abilities[item.ability_index], item.controller,
                            self.top_most(item.instance_id), here,
                            item.targets, item.ability_index)
        else:
            ability = activated_abilities(card)[item.ability_index]
            self._queue(ability, item.controller, item.instance_id,
                        None, item.targets, item.ability_index)

        self._resolve_effects()
        self._after_chain_change()

    def _open_priority_window(self, player: int) -> None:
        """338 -- the next player may respond or pass."""
        self.priority = player
        self._current_player = player
        self.phase = Phase.CHAIN

    def _after_chain_change(self) -> None:
        """340.2-340.4 -- decide where play goes once an item has resolved."""
        if self.awaiting is not None:
            return  # a choice is still outstanding
        if self.chain:
            # More items remain; the controller of the newest gets priority.
            self._open_priority_window(self.chain[-1].controller)
            return
        # 340.2 -- the chain emptied, so play returns to an Open state.
        self.chain_passes = 0
        self.priority = None
        if self._interrupted_phase is not None:
            # The chain interrupted an automatic phase; pick the sequence back
            # up where it stopped rather than dropping the phases after it.
            resume, self._interrupted_phase = self._interrupted_phase, None
            self.phase = resume
            self._current_player = self.turn_player
            self._run_automatic_phases()
            return
        if self.showdown is not None:
            # 347.1.b -- when that chain closes, Focus passes. 346.1 excepts a
            # chain opened by a triggered ability or one that Adds resources,
            # which is why tapping a rune seal mid-showdown does not hand the
            # window to the opponent.
            if self._last_from_trigger:
                self.priority = self.focus
                self._current_player = self.focus if self.focus is not None else self.turn_player
                self.phase = Phase.SHOWDOWN
            else:
                self._pass_focus()
        else:
            self.phase = Phase.MAIN
            self._current_player = self.turn_player

    def _apply_move(self, action: StandardMove) -> None:
        """144 Standard Move; 190.3.a Contested; 450."""
        player = self._current_player
        ref = self.cards[action.instance_id]
        ref.exhausted = True  # 144.2 -- exhausting is the cost
        self.move_to(ref, action.destination)
        name = self.db[ref.card_id].name

        if action.destination != BASE_LOCATION:
            index = int(action.destination.split(":")[1])
            bf = self.battlefields[index]
            # 450 -- destination becomes Contested if not already, and the
            # mover does not control it.
            if bf.controller != player and not bf.contested:
                bf.contested = True
                bf.contested_by = player
                self._emit(f"P{player} contests {self.db[bf.card_id].name}")
        self._emit(f"P{player} moves {name} to {action.destination}")

    def _apply_tap_energy(self, action: ExhaustRuneForEnergy) -> None:
        ref = self.cards[action.instance_id]
        ref.exhausted = True
        self.players[ref.controller].pool.energy += 1

    def _apply_recycle_power(self, action: RecycleRuneForPower) -> None:
        """164.2.b -- recycle for one power of the rune's domain (416.1.b)."""
        ref = self.cards[action.instance_id]
        state = self.players[ref.controller]
        card = self.db[ref.card_id]
        state.channeled_runes.remove(action.instance_id)
        state.rune_deck.append(action.instance_id)  # bottom of the Rune Deck
        self.leave_board(ref)
        ref.exhausted = False
        if card.domains:
            state.pool.add_power(card.domains[0])
        else:
            state.pool.universal_power += 1

    def _apply_assign(self, action: AssignDamageTo) -> None:
        assert self.combat is not None
        combat = self.combat
        target = self.cards[action.instance_id]
        # 437.5.a -- the assignment target includes the Prevent Value, so a
        # protected unit soaks up that much more of the attacker's Might
        # before the assignment counts as lethal.
        cushion = 0 if target.prevent is None else target.prevent
        needed = max(0, self.might_of(target) + cushion - target.damage)
        assigned = min(combat.remaining, needed if needed > 0 else combat.remaining)
        self.deal_damage(target, assigned)
        # 437.5 -- "Damage can still be assigned to Units in combat that are
        # affected by Prevent." The assignment spends the attacker's Might
        # whether or not Prevent then eats it, so the pool drops by what was
        # assigned rather than by what got through.
        combat.remaining -= assigned
        self._emit(
            f"P{combat.assigning} assigns {assigned} to {self.db[target.card_id].name}"
        )
        if combat.remaining <= 0:
            self._finish_assignment()

    def _apply_activate(self, action: ActivateAbility) -> None:
        """376/398 -- pay the costs, then put the ability on the Chain."""
        ref = self.cards[action.instance_id]
        ability = activated_abilities(self.db[ref.card_id])[action.index]
        self._pay_ability(ref, ability)
        self._emit(f"P{ref.controller} activates {self.db[ref.card_id].name}")

        adds_resources = resolves_immediately(self.db[ref.card_id], ability)
        self.chain.append(
            ChainItem(
                kind="ability",
                instance_id=action.instance_id,
                controller=ref.controller,
                ability_index=action.index,
                pending=True,          # 329.2 -- until targets are declared
                from_trigger=adds_resources,
            )
        )
        self.chain_passes = 0
        self._break_showdown_pass_sequence()   # 347.2.a
        # 355.8 -- declare targets, then finalize. `_finish_playing` runs the
        # 337.2 immediate-resolution branch for an ability that Adds
        # resources, and opens the priority window otherwise.
        self._advance_targeting()

    def _apply_pass(self) -> None:
        """Pass priority, pass focus, or end the phase, depending on state."""
        if self.phase is Phase.CHAIN:
            self._pass_priority()
        elif self.phase is Phase.SHOWDOWN:
            self._pass_in_showdown()
        else:
            self._advance_phase()

    def _pass_priority(self) -> None:
        """339 -- when all players pass in sequence, the top item resolves."""
        self.chain_passes += 1
        if self.chain_passes >= len(PLAYERS):
            self.chain_passes = 0
            self._resolve_top()
            return
        self._open_priority_window(self.opponent(self._current_player))

    # ------------------------------------------------------------- showdowns

    def _open_showdown(self, bf_index: int, attacker: int, is_combat: bool) -> None:
        """344-345 / 464 -- open a Showdown; the contester gains Focus."""
        defender = self.opponent(attacker)
        self.showdown = Showdown(
            battlefield=bf_index, attacker=attacker, defender=defender, is_combat=is_combat
        )
        self.focus = attacker  # 345 / 464.2.d
        self.priority = attacker  # 313.2 -- gaining Focus grants Priority
        self._current_player = attacker
        self.phase = Phase.SHOWDOWN
        kind = "Combat" if is_combat else "Showdown"
        self._emit(
            f"{kind} opens at {self.db[self.battlefields[bf_index].card_id].name} "
            f"— P{attacker} has focus"
        )
        if is_combat:
            # 464.2.c.3 -- units present gain their controller's designation.
            for ref in self.units_at(bf_location(bf_index)):
                ref.is_attacker = ref.controller == attacker
                ref.is_defender = ref.controller == defender

    def _pass_focus(self) -> None:
        """346 / 347.1.b -- Focus passes to the next player in turn order."""
        assert self.showdown is not None
        self.focus = self.opponent(self.focus if self.focus is not None else self.turn_player)
        self.priority = self.focus
        self._current_player = self.focus
        self.phase = Phase.SHOWDOWN

    def _break_showdown_pass_sequence(self) -> None:
        """347.2.a -- the Showdown ends when all players have passed once *in
        sequence*. Playing a card or activating an ability is not a pass, so
        it restarts the count.

        Without this the counter only ever climbed, and any spell cast in a
        showdown ended it one pass early: the player who had already passed
        never got to answer the spell.
        """
        if self.showdown is not None:
            self.showdown.passes = 0

    def _pass_in_showdown(self) -> None:
        """347.2 -- all players passing in sequence ends the Showdown."""
        assert self.showdown is not None
        self.showdown.passes += 1
        if self.showdown.passes >= len(PLAYERS):
            self._close_showdown()
            return
        self._pass_focus()

    def _close_showdown(self) -> None:
        """A Showdown ends: combat proceeds to damage, otherwise control settles."""
        assert self.showdown is not None
        showdown = self.showdown
        bf = self.battlefields[showdown.battlefield]
        self.showdown = None
        self.focus = None
        self.priority = None
        self.phase = Phase.MAIN
        self._current_player = self.turn_player

        if showdown.is_combat:
            self._emit("Combat showdown closes — damage step")
            self.combat = CombatState(
                battlefield=showdown.battlefield,
                attacker=showdown.attacker,
                defender=showdown.defender,
            )
            self._begin_damage_step()
            return

        # 190.3.b -- Contested persists until Control is established.
        occupants = self.units_at(bf_location(bf.index))
        holders = {r.controller for r in occupants}
        bf.contested = False
        bf.contested_by = None
        if len(holders) == 1:
            sole = next(iter(holders))
            if bf.controller != sole:
                bf.controller = sole
                self._score(sole, bf, "Conquer")
        self._emit(f"Showdown closes at {self.db[bf.card_id].name}")

    def _apply_hide(self, action: HideCard) -> None:
        """421 / 811.1.b -- put the card facedown at a battlefield you control.

        Hide is not a subset of Play (811.1.c.1) and does not open a chain
        (811.1.c.2), so this neither pays a card cost nor touches the chain.
        The cost is [A]: one power of any domain (135.2.e.5).
        """
        player = self._current_player
        state = self.players[player]
        ref = self.cards[action.instance_id]

        state.pool.pay(0, [ANY_DOMAIN])
        if action.instance_id in state.hand:
            state.hand.remove(action.instance_id)
        elif action.instance_id in state.champion_zone:
            state.champion_zone.remove(action.instance_id)
        ref.hidden_at = action.battlefield
        ref.hidden_on_turn = self.turn_number
        self._emit(
            f"P{player} hides a card at "
            f"{self.db[self.battlefields[action.battlefield].card_id].name}"
        )

    def _apply_concede(self) -> None:
        player = self._current_player
        self.conceded = player
        self.winner = self.opponent(player)
        self.phase = Phase.GAME_OVER
        self._emit(f"P{player} concedes")

    # ------------------------------------------------------------ phase flow

    def _advance_phase(self) -> None:
        if self.phase is Phase.COMBAT_ASSIGN:
            self._finish_assignment()
            return
        if self.phase is Phase.MAIN:
            self.phase = Phase.ENDING
            self._run_automatic_phases()
            return
        self._run_automatic_phases()

    def _run_automatic_phases(self) -> None:
        """Run every non-interactive phase until a decision is needed.

        Awaken, Beginning, Channel, Draw and Ending require no choices in
        Milestone 1, so they execute straight through; the loop stops at Main
        or at a combat that needs damage assigned.
        """
        guard = 0
        while self.phase not in (
            Phase.MAIN,
            Phase.COMBAT_ASSIGN,
            Phase.GAME_OVER,
            Phase.SETUP_BATTLEFIELD,
            Phase.SETUP_MULLIGAN,
        ):
            guard += 1
            if guard > 64:
                raise RuntimeError("phase loop did not terminate")
            if self.phase is Phase.AWAKEN:
                self._phase_awaken()
                self.phase = Phase.BEGINNING
            elif self.phase is Phase.BEGINNING:
                self._phase_beginning()
                self.phase = Phase.CHANNEL
            elif self.phase is Phase.CHANNEL:
                self._phase_channel()
                self.phase = Phase.DRAW
            elif self.phase is Phase.DRAW:
                self._phase_draw()
                self.phase = Phase.MAIN
                self._phase_main_start()
            elif self.phase is Phase.ENDING:
                self._phase_ending()
            self._cleanup()
            # 354.4 -- "If there are Tasks outstanding or currently being
            # handled, finish those Tasks before continuing this process."
            # A trigger that fired in this phase (471.2's Hold abilities, for
            # instance) is now a chain item, and 309.1 makes that a Closed
            # State. The turn stops here and resumes once the chain empties;
            # `_interrupted_phase` is what `_after_chain_change` reads to
            # know the sequence was not finished.
            if self.chain:
                self._interrupted_phase = self.phase
                self._open_priority_window(self.turn_player)
                return

    def _phase_awaken(self) -> None:
        """315.1 -- ready everything the turn player controls."""
        for ref in self.cards.values():
            if ref.controller == self.turn_player:
                ref.exhausted = False
        self._emit(f"-- P{self.turn_player} turn {self.turn_number}: awaken")

    def _phase_beginning(self) -> None:
        """315.2.b -- the turn player Holds every battlefield they control.

        816 Temporary resolves first: "At the start of this permanent's
        controller's Beginning Phase, **before scoring**, kill this." The
        ordering is load-bearing -- a Temporary unit holding a battlefield is
        already dead when 315.2.b runs, so it cannot Hold it on the way out.
        """
        if self._kill_temporary_permanents():
            # 319.6 -- a Cleanup becomes an Outstanding Task "after any number
            # of Game Objects enter or leave the Board", and 334's HOT FEPR
            # handles outstanding tasks before anything else. So step 4 (323.6)
            # runs here, and a battlefield whose only defender was Temporary is
            # uncontrolled before 315.2.b looks at it.
            self._cleanup()
        for bf in self.battlefields:
            bf.scored_by.clear()  # 470 -- once per battlefield per turn
        for bf in self.battlefields:
            if bf.controller == self.turn_player:
                self._score(self.turn_player, bf, "Hold")

    def _kill_temporary_permanents(self) -> bool:
        """816.1.b -- kill every Temporary permanent the turn player controls.

        816.1.c scopes the trigger to *its controller's* Beginning Phase, not
        anybody's, so a Temporary permanent survives the opponent's turn.
        816.2.a makes multiple instances redundant, which is free here: a card
        is killed once and then has no location to be killed from again.
        """
        killed = False
        for ref in list(self.cards.values()):
            if ref.location is None or ref.controller != self.turn_player:
                continue
            if not self.db[ref.card_id].has_temporary:
                continue
            self._emit(f"{self.db[ref.card_id].name} is Temporary and dies (816)")
            self._kill(ref)
            killed = True
        return killed

    def channel(self, player: int, count: int = 1, exhausted: bool = False) -> int:
        """430 -- take runes from the top of the Rune Deck onto the board.

        430.2.a: runes are channeled ready by default; 430.2 lets an effect
        specify otherwise ("Channel 1 rune exhausted").
        430.3: if there are not enough runes, channel as many as possible.

        Returns how many were actually channeled, which is what 430.5's
        "If you couldn't channel 2 runes this way" needs.
        """
        state = self.players[player]
        channeled = 0
        for _ in range(count):
            if not state.rune_deck:
                break
            instance_id = state.rune_deck.pop(0)
            state.channeled_runes.append(instance_id)
            self.cards[instance_id].location = BASE_LOCATION
            self.cards[instance_id].exhausted = exhausted
            channeled += 1
        return channeled

    def _phase_channel(self) -> None:
        """315.3 -- channel 2 runes; the player going second gets +1 once (485.7)."""
        state = self.players[self.turn_player]
        count = CHANNEL_PER_TURN
        if not state.has_channeled and self.turn_player != self.first_player:
            count += 1
        state.has_channeled = True
        channeled = self.channel(self.turn_player, count)
        self._emit(f"P{self.turn_player} channels {channeled}")

    def _phase_draw(self) -> None:
        """315.4 -- draw 1; an empty deck is a Burn Out (431)."""
        if not self.players[self.turn_player].main_deck:
            self._burn_out(self.turn_player)
        self._draw(self.turn_player, 1)   # 431.2.d / 315.4.b.2

    def _burn_out(self, player: int) -> None:
        """431.2 -- the four steps, in sequence.

        Only 431.2.c was implemented, so a player whose deck ran out gave up a
        point and then stayed permanently deckless: the trash was never
        recycled and the draw that caused it never happened. A burned-out
        player could not draw again for the rest of the game.

        431.2.a is implicit here -- the caller has already done as much of the
        prescribed action as it could, which for an empty deck is nothing.
        """
        state = self.players[player]

        # 431.2.b -- recycle the trash into the Main Deck, randomized.
        recycled = len(state.trash)
        if recycled:
            returning = list(state.trash)
            state.trash.clear()
            self._rng.shuffle(returning)
            state.main_deck.extend(returning)

        # 431.2.c -- choose an opponent to gain 1 point. With one opponent
        # there is no choice to make (485 is a two-player mode).
        opponent = self.opponent(player)
        self.players[opponent].points += 1
        self._emit(
            f"P{player} burns out: recycles {recycled} card(s), "
            f"P{opponent} +1 point"
        )

    def _phase_main_start(self) -> None:
        """316.3 -- rune pools empty; unspent Energy and Power are lost."""
        for state in self.players:
            state.pool.clear()
        self._current_player = self.turn_player

    def _phase_ending(self) -> None:
        """317, then the turn passes (306). Turn-scoped modifiers expire.

        317.2 is a Special Cleanup with three inserted steps: 3c heals all
        units, 3d expires every "this turn" effect, 3e empties the rune pools.
        They run after the ordinary cleanup steps, so a unit already killed by
        lethal damage in step 3b is not healed back to life.
        """
        # 317.2.b, step 3c -- "Heal all Units". 143.3.b names exactly two
        # moments damage is healed: here, and a Combat Cleanup (466.1.a.1).
        # Only the second was implemented, so damage dealt *outside* combat
        # accumulated across turns and eventually killed units that the rules
        # heal clean every turn.
        self._cleanup()
        for ref in self.cards.values():
            if ref.location is not None:
                ref.damage = 0
        for ref in self.cards.values():
            ref.might_this_turn = 0
            # 437.1.b.1's wording is "...this turn", so an unspent Prevent
            # Value expires with every other this-turn effect at step 3d, and
            # so does an unused death ward ("the next time it dies this turn").
            ref.prevent = 0
            ref.death_replacement = None
            # 423.1.a.2 -- Stunned is lost during step 3d, which 317.2.c makes
            # the same moment every other "this turn" effect expires.
            ref.stunned = False
            ref.granted_keywords = tuple(
                g for g in ref.granted_keywords if g[2] != Duration.THIS_TURN.value
            )
        self.units_enter_ready.clear()
        # 167 -- "at the start of each player's Main Phase **and the end of
        # each player's turn**". Only the first half was implemented, so power
        # survived into the opponent's Awaken, Beginning, Channel and Draw
        # phases, where Reactions can be played with it.
        for zones in self.players:
            zones.pool.clear()
        self.turn_player = self.opponent(self.turn_player)
        self.turn_number += 1
        self._current_player = self.turn_player
        self.phase = Phase.AWAKEN

    # ---------------------------------------------------------------- cleanup

    def _cleanup(self) -> None:
        """318-323. Repeats until the state stops changing (322)."""
        for _ in range(32):
            changed = False
            if self._check_victory():
                return
            changed |= self._resolve_lethal_damage()
            changed |= self._resolve_control()
            changed |= self._resolve_designations()
            changed |= self._recall_stranded_gear()
            changed |= self._remove_stranded_hidden()
            changed |= self._maybe_open_showdown()
            if not changed:
                return

    def _normalize_window(self) -> None:
        """Keep `phase` consistent with the chain and showdown (307-310).

        Without this, a chain that empties through an unusual path (an effect
        countering an item, a choice resolving mid-resolution) can leave the
        turn in a Closed state with nothing on the chain -- a dead position
        where the only legal action is to pass forever.
        """
        if self.phase in (Phase.CHOOSING, Phase.COMBAT_ASSIGN, Phase.GAME_OVER):
            return
        if self.phase is Phase.CHAIN and not self.chain:
            if self.showdown is not None:
                self.phase = Phase.SHOWDOWN
                self._current_player = self.focus if self.focus is not None else self.turn_player
            else:
                self.phase = Phase.MAIN
                self.priority = None
                self.chain_passes = 0
                self._current_player = self.turn_player
        elif self.phase is Phase.MAIN and self.chain:
            self.phase = Phase.CHAIN
        elif self.phase is Phase.SHOWDOWN and self.showdown is None:
            self.phase = Phase.MAIN
            self._current_player = self.turn_player

    def _check_victory(self) -> bool:
        """323.1 -- >= Victory Score and strictly more than the opponent."""
        if self.phase is Phase.GAME_OVER:
            return True
        scores = [p.points for p in self.players]
        for pid, points in enumerate(scores):
            if points >= VICTORY_SCORE and points > scores[self.opponent(pid)]:
                self.winner = pid
                self.phase = Phase.GAME_OVER
                self._emit(f"P{pid} wins with {points} points")
                return True
        return False

    def _resolve_lethal_damage(self) -> bool:
        """323.3a / 466 -- remove units with lethal damage."""
        changed = False
        for ref in list(self.cards.values()):
            if ref.location is None or self.db[ref.card_id].type != "unit":
                continue
            if self._has_lethal(ref):
                self._kill(ref)
                changed = True
        return changed

    def _apply_death_replacement(self, ref: CardRef) -> bool:
        """367-373 -- if this unit's death is replaced, do that instead.

        370.1.a.1: "A unit's death being replaced ... is the same as the kill
        action that caused that death not occurring." So nothing that triggers
        on dying triggers, and the unit never reaches the trash.

        The replacement is one-shot ("the next time it dies"), and it is
        cleared whether or not it fires: a cost that cannot be paid means it
        did not apply, and 373.2 stops one effect being applied twice to the
        same sequence either way.
        """
        armed = ref.death_replacement
        if armed is None:
            return False
        recall, exhaust, heal, cost_power = armed
        ref.death_replacement = None
        pool = self.players[ref.controller].pool
        if cost_power is not None:
            if not pool.can_pay(0, [cost_power]):
                return False               # the cost cannot be paid
            pool.pay(0, [cost_power])
        if heal:
            ref.damage = 0                 # 418 Heal
        if recall:
            self.move_to(ref, BASE_LOCATION)   # 454 Recall, 719.3.a
        if exhaust:
            ref.exhausted = True
        self._emit(
            f"{self.db[ref.card_id].name}'s death is replaced "
            f"(recalled{' exhausted' if exhaust else ''})"
        )
        return True

    def _kill(self, ref: CardRef) -> None:
        """428 Kill -- the card goes to its owner's trash (108.2.b)."""
        # 457.1 -- gear left unattached at a battlefield is recalled; gear
        # attached to a dying unit detaches here.
        # 719.5 -- attached cards Detach, "remaining in their current zones".
        # They are not sent home here: an Equipment whose host died at a
        # battlefield stays there until 457.1 recalls it at the next Cleanup,
        # which is a window other effects can see.
        # 370.1.a.1 -- a replaced death is the kill not happening at all, so
        # this comes before anything else the kill would do.
        if self._apply_death_replacement(ref):
            return
        if ref.is_token:
            # 186.1 -- a token put into a non-board zone ceases to exist, so
            # it never reaches the trash.
            self._emit(f"{self.db[ref.card_id].name} is killed")
            self.cease_to_exist(ref)
            return
        state = self.players[ref.controller]
        if ref.instance_id in state.base:
            state.base.remove(ref.instance_id)
        self.leave_board(ref)
        ref.damage = 0
        self.players[ref.owner].trash.append(ref.instance_id)
        self._emit(f"{self.db[ref.card_id].name} is killed")

    def create_token(self, card_id: str, controller: int,
                     location: str = BASE_LOCATION,
                     exhausted: bool | None = None) -> int:
        """439 -- produce a Game Object that did not exist before.

        182/183: the token's controller and owner are both the controller of
        the effect that created it. 184.1: the effect may say it enters ready
        or exhausted; `None` means the default for its type, which for a unit
        is exhausted (143.4). 184.2: the effect may restrict where it enters.
        """
        card = self.db[card_id]
        instance_id = (max(self.cards) + 1) if self.cards else 1
        if exhausted is None:
            exhausted = card.type == "unit"
        ref = CardRef(
            instance_id=instance_id,
            card_id=card_id,
            owner=controller,
            controller=controller,
            location=location,
            exhausted=exhausted,
            is_token=True,
        )
        self.cards[instance_id] = ref
        self.players[controller].base.append(instance_id)
        self._emit(
            f"P{controller} creates a {card.name} token"
            + (" exhausted" if exhausted else " ready")
        )
        return instance_id

    def cease_to_exist(self, ref) -> None:
        """186.1 -- a token put into any non-board zone ceases to exist.

        A token does not go to the trash when it dies; it is simply gone. The
        instance is dropped entirely, so nothing can later refer to it and the
        card-conservation invariant does not count it as a lost card.
        """
        self.leave_board(ref)
        for zones in self.players:
            for contents in (zones.base, zones.channeled_runes, zones.hand,
                             zones.trash, zones.main_deck, zones.rune_deck,
                             zones.banishment, zones.champion_zone):
                if ref.instance_id in contents:
                    contents.remove(ref.instance_id)
        self.cards.pop(ref.instance_id, None)
        self._emit(f"the {self.db[ref.card_id].name} token ceases to exist")

    def move_to(self, ref, location: str) -> None:
        """719.3 -- put a card at a location, taking its attachments with it.

        "A Top-Most Card and all cards Attached to it are at the same
        location", and 719.3.a moves them together. Attached cards have no
        move of their own (718.5.c), so this is the only way they travel.

        Every path that changes a card's location on the board goes through
        here. Before it existed, only the Standard Move carried attachments,
        so an attacker recalled at the end of an unresolved combat (466.1.a.2)
        left its Equipment standing on the battlefield -- the same shape as
        the 719.5 bug the invariant checker found in `leave_board`.
        """
        ref.location = location
        for attached in self.cards.values():
            # Only cards still on the board travel; anything that has left is
            # no longer attached to something that can carry it.
            if attached.attached_to == ref.instance_id and attached.location is not None:
                attached.location = location

    def leave_board(self, ref) -> None:
        """719.5 -- a card changing from a board zone to a non-board zone.

        Attachment is a relationship between cards *on the board* (718.5), so
        it cannot survive either end of it leaving: everything attached to
        this card Detaches, and this card stops being attached to anything.
        Designations go too (466.7.a) -- a card that is gone is not attacking.

        Every path that takes a card off the board goes through here. Before
        it existed only `_kill` detached, so a unit returned to hand by
        `ReturnToHand` left its Equipment attached to a card in a hand -- an
        invariant violation the state checker found within six random games.
        """
        for attached in self.cards.values():
            if attached.attached_to == ref.instance_id:
                attached.attached_to = None
        ref.attached_to = None
        ref.location = None
        ref.is_attacker = ref.is_defender = False
        # 705 -- "If a Unit leaves play, remove all Buffs from it", and 705.1
        # says a Champion does not keep them in the Champion Zone either.
        # 124.1 says the same for every temporary modification, which is why
        # the tracked Prevent Value (437) goes with them.
        ref.buffs = 0
        ref.prevent = 0
        ref.death_replacement = None

    def _busy_at(self, index: int) -> bool:
        """Whether a Showdown or Combat is ongoing at this battlefield.

        190.4.b -- while one is, Control cannot change except through the
        steps of that Showdown or Combat.
        """
        return (
            (self.showdown is not None and self.showdown.battlefield == index)
            or (self.combat is not None and self.combat.battlefield == index)
        )

    def _resolve_control(self) -> bool:
        """Cleanup steps 4 and 8 (323.6, 323.11).

        **323.6 / 190.4.c** -- "If a player has no Units at a Battlefield and
        the turn is in an Open state, they lose Control of that Battlefield in
        the following cleanup unless there is a Combat or Showdown ongoing
        there." 190.4.a says the same the other way round: control is
        maintained "for as long as they have Units at that Battlefield".

        This was missing entirely. Control was granted permanently once taken,
        so a battlefield cost nothing to keep and scored a Hold every turn
        from an empty field -- removing the central tension of the game, which
        is that units cannot both garrison and attack.

        **323.11 / 190.3.b.1** -- Contested is removed when the player who
        applied it holds no units there and nothing is running.
        """
        changed = False
        # 309.2 -- a Chain means a Closed State, and step 4 is gated on an
        # Open one. This is what lets a Reaction return a unit to a
        # battlefield before control is checked.
        open_state = not self.chain

        for bf in self.battlefields:
            busy = self._busy_at(bf.index)
            occupants = self.units_at(bf_location(bf.index))

            # Step 4 (323.6).
            if (open_state and not busy and bf.controller is not None
                    and not any(r.controller == bf.controller for r in occupants)):
                self._emit(
                    f"{self.db[bf.card_id].name} becomes uncontrolled: "
                    f"P{bf.controller} has no units there (323.6)"
                )
                bf.controller = None
                changed = True

            # Step 8 (323.11 / 190.3.b.1).
            if not bf.contested or bf.contested_by is None or busy:
                continue
            if not any(r.controller == bf.contested_by for r in occupants):
                bf.contested = False
                bf.contested_by = None
                changed = True
        return changed

    def _resolve_designations(self) -> bool:
        """323.2 / 464.2.c.3.a -- keep Attacker and Defender designations in
        step with who is actually at the battlefield under combat.

        464.2.c.3 stamps the units present when Attacker and Defender are
        established, but a unit can arrive later -- moved in, or played there
        by an effect. 464.2.c.3.a gives it the designation during the Cleanup
        that follows, which is here. 323.2.c takes the designation *off* a
        unit that has left, which is the same comparison in reverse.
        """
        fight = self.combat if self.combat is not None else self.showdown
        if fight is not None and not getattr(fight, "is_combat", True):
            fight = None  # a Non-Combat Showdown designates nobody (344.2)
        location = bf_location(fight.battlefield) if fight is not None else None

        changed = False
        for ref in self.cards.values():
            if self.db[ref.card_id].type != "unit":
                continue
            if fight is None or ref.location != location:
                wants_attacker = wants_defender = False
            else:
                wants_attacker = ref.controller == fight.attacker
                wants_defender = ref.controller == fight.defender
            if ref.is_attacker != wants_attacker or ref.is_defender != wants_defender:
                ref.is_attacker, ref.is_defender = wants_attacker, wants_defender
                changed = True
        return changed

    def _recall_stranded_gear(self) -> bool:
        """457.1 -- an un-attached non-Unit Gear at a battlefield is Recalled
        to its controller's base during the next Cleanup.

        Gear reaches a battlefield only by riding a host (719.3.a), so this
        fires when that host dies or the gear is detached: the Equipment is
        left standing on a battlefield it cannot hold, and goes home.
        """
        changed = False
        for ref in self.cards.values():
            if ref.location in (None, BASE_LOCATION) or ref.attached_to is not None:
                continue
            if self.db[ref.card_id].type != "gear":
                continue
            ref.location = BASE_LOCATION
            self._emit(f"{self.db[ref.card_id].name} is recalled to base")
            changed = True
        return changed

    def _remove_stranded_hidden(self) -> bool:
        """323.7 -- cleanup step 5: "Remove all Hidden cards from all
        Battlefields that are not controlled by the same player and place them
        in their owner's Trash."

        107.3.c says a card may only occupy a Facedown Zone while the card's
        controller also controls the associated battlefield, and 107.3.d says
        the cards are removed at the next Cleanup when that stops being true.
        811.1.b's "for as long as you control that battlefield" is the same
        rule read from the keyword's side.

        421.4 -- a facedown card changing zones is revealed to all players, so
        the removal names the card in the shared log and is recorded as a
        revelation both players may reason from.
        """
        changed = False
        for ref in list(self.cards.values()):
            if ref.hidden_at is None:
                continue
            index = ref.hidden_at
            if 0 <= index < len(self.battlefields):
                if self.battlefields[index].controller == ref.controller:
                    continue
            name = self.db[ref.card_id].name
            ref.hidden_at = None
            ref.hidden_on_turn = 0
            # 421.4 -- the card is revealed as it changes zones. It lands in
            # the trash, which is a public zone with public contents (108.2.d),
            # so `knowledge.public_counts` picks the identity up on its own;
            # naming it in the log is what makes the reveal visible to players.
            if ref.is_token:
                self.cease_to_exist(ref)           # 186.1
            else:
                self.players[ref.owner].trash.append(ref.instance_id)
            self._emit(
                f"{name} is revealed and trashed: P{ref.controller} no longer "
                f"controls battlefield {index} (323.7)"
            )
            changed = True
        return changed

    def _maybe_open_showdown(self) -> bool:
        """344 / 460 -- a Contested battlefield opens a Showdown at a Cleanup.

        Requires a Neutral Open state: no chain, no showdown or combat already
        running (344, 460).
        """
        if self.showdown is not None or self.combat is not None:
            return False
        if self.chain or self.phase is Phase.GAME_OVER:
            return False
        if self.phase not in (Phase.MAIN, Phase.CHAIN):
            return False

        for bf in self.battlefields:
            if not bf.contested or bf.contested_by is None:
                continue
            occupants = self.units_at(bf_location(bf.index))
            holders = {r.controller for r in occupants}
            if not holders:
                continue
            # 344.1 -- units from two players stage a Combat, which opens as a
            # Combat Showdown. 344.2 -- otherwise a Non-Combat Showdown.
            is_combat = len(holders) >= 2
            self._open_showdown(bf.index, bf.contested_by, is_combat)
            return True
        return False

    def _begin_damage_step(self) -> None:
        """465.2 -- sum Might per side; the Attacker assigns first."""
        assert self.combat is not None
        combat = self.combat
        location = bf_location(combat.battlefield)
        attack_might = self.combat_might(combat.attacker, location)
        combat.assigning = combat.attacker
        combat.remaining = attack_might
        self.phase = Phase.COMBAT_ASSIGN
        self._current_player = combat.attacker
        if attack_might <= 0:
            self._finish_assignment()

    def _finish_assignment(self) -> None:
        assert self.combat is not None
        combat = self.combat
        location = bf_location(combat.battlefield)
        if combat.assigning == combat.attacker and not combat.defender_assigned:
            combat.attacker_assigned = True
            defend_might = self.combat_might(combat.defender, location)
            combat.assigning = combat.defender
            combat.remaining = defend_might
            self._current_player = combat.defender
            if defend_might <= 0:
                self._finish_assignment()
            return
        combat.defender_assigned = True
        self._resolve_combat()

    def _resolve_combat(self) -> None:
        """466 -- lethal removal, recall, conquer, clear contested and damage."""
        assert self.combat is not None
        combat = self.combat
        location = bf_location(combat.battlefield)
        bf = self.battlefields[combat.battlefield]

        self._resolve_lethal_damage()  # 466.1

        attackers = self.units_at(location, combat.attacker)
        defenders = self.units_at(location, combat.defender)

        if attackers and defenders:
            # 466.1.a.2 (step 3d) -- both sides remain, attackers are Recalled
            # (454), which makes the result "No Result" (466.3.d).
            for ref in attackers:
                self.move_to(ref, BASE_LOCATION)   # 719.3.a
                ref.is_attacker = False
            self._emit("Attackers are recalled")
        elif attackers or defenders:
            # 466.5 -- the player with Units remaining Establishes Control if
            # they did not already hold this battlefield. 466.5.e is explicit
            # that this need not be the player who applied Contested: a
            # defender who wipes out the attack takes the battlefield too.
            survivor = combat.attacker if attackers else combat.defender
            if bf.controller != survivor:
                bf.controller = survivor
                self._emit(f"P{survivor} conquers {self.db[bf.card_id].name}")
                self._score(survivor, bf, "Conquer")  # 466.5.d
        else:
            # 466.5.b -- nobody is left here, so the battlefield becomes
            # Uncontrolled rather than staying with its previous holder.
            if bf.controller is not None:
                self._emit(f"{self.db[bf.card_id].name} becomes uncontrolled")
                bf.controller = None

        bf.contested = False  # 466.5.a
        bf.contested_by = None
        # 466.1.a.1, step 3c of the Combat Cleanup -- "Heal all Units".
        # 466.7.a -- remove Attacker and Defender designations.
        for ref in self.cards.values():
            ref.damage = 0
            ref.is_attacker = ref.is_defender = False

        self.combat = None
        self.phase = Phase.MAIN
        self._current_player = self.turn_player

    # ---------------------------------------------------------------- scoring

    def _score(self, player: int, bf: Battlefield, how: str) -> None:
        """467-471. Enforces once-per-battlefield-per-turn and the Final Point."""
        if player in bf.scored_by:  # 470
            return
        bf.scored_by.add(player)
        state = self.players[player]

        if how == "Conquer" and state.points >= VICTORY_SCORE - 1:
            # 471.1.b -- the Final Point needs every battlefield scored this
            # turn; otherwise the player draws instead.
            if all(player in other.scored_by for other in self.battlefields):
                state.points += 1
                self._emit(f"P{player} takes the Final Point ({state.points})")
            else:
                self._draw(player, 1)
                self._emit(f"P{player} would take the Final Point; draws instead")
            return

        state.points += 1
        self._emit(f"P{player} scores by {how} ({state.points} points)")
        self._fire_score_triggers(player, bf, how)

    def _fire_score_triggers(self, player: int, bf: Battlefield, how: str) -> None:
        """471.2 -- "Trigger Score abilities at the Battlefield that Scored."

        Two things were wrong here. Conquer abilities fired for every card the
        player controlled *anywhere*, so a unit standing at one battlefield
        triggered on a score at another. And Hold abilities did not exist at
        all -- ten cards' printed text simply did nothing, including
        Ahri - Alluring, whose whole rules text is "When I hold, you score 1
        point."

        Gear attached to a unit shares its location (719.3), so scoping by
        location covers "When I conquer" printed on Equipment too.
        """
        kind = TriggerKind.ON_CONQUER if how == "Conquer" else TriggerKind.ON_HOLD
        location = bf_location(bf.index)
        for ref in sorted(self.cards.values(), key=lambda r: r.instance_id):
            if ref.controller == player and ref.location == location:
                self._trigger(kind, ref.instance_id)   # 383.3
        self._advance_targeting()

    # ----------------------------------------------------------------- draw

    def _draw(self, player: int, count: int) -> None:
        """413 Draw."""
        state = self.players[player]
        for _ in range(count):
            if not state.main_deck:
                return
            state.hand.append(state.main_deck.pop(0))

    # ----------------------------------------------------------- observation

    def observation(self, player_id: int) -> "RiftboundObservation":
        """A player's view with hidden information stripped (128 Privacy)."""
        from engine.observation import RiftboundObservation

        return RiftboundObservation.build(self, player_id)
