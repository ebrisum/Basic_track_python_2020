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
from cards.primitives import EffectContext, execute
from cards.gear import equipment_profile
from cards.scripts import abilities_of_kind, activated_abilities, script_for
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
    # 811.1.d.1 -- set while a card played from Hidden is on the chain, so it
    # enters at that battlefield rather than the Base.
    _playing_from_hidden: int | None = None
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
        clone._resume_phase = self._resume_phase
        clone._playing_from_hidden = self._playing_from_hidden
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
            if state.pool.can_pay(card.energy, card.power_domains):
                actions.append(PlayCard(instance_id))
                # 805.2 -- ACCELERATE is an optional additional cost paid as
                # part of playing the unit, never once it is on the board.
                if card.type == "unit" and card.has_accelerate:
                    if state.pool.can_pay(
                        card.energy + 1,
                        card.power_domains + card.accelerate_power_domains,
                    ):
                        actions.append(PlayCard(instance_id, accelerate=True))

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
            for index, ability in enumerate(activated_abilities(card)):
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
        """465.2.c, with Tank forced first (626.1.d.4)."""
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
        return card.might + ref.might_this_turn + ref.might_permanent + bonus + attached

    def _facedown_at(self, index: int) -> CardRef | None:
        """811.1.b -- at most one facedown card per battlefield."""
        for ref in self.cards.values():
            if ref.hidden_at == index:
                return ref
        return None

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
        """142.4.a -- lethal damage is nonzero damage >= Might."""
        return ref.damage > 0 and ref.damage >= self.might_of(ref)

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

    def _queue(self, ability: Ability, controller: int, source: int | None) -> None:
        """Push an ability's effects onto the resolution queue."""
        for effect in ability.effects:
            self.pending.append((effect, EffectContext(controller=controller, source=source)))

    def _fire(self, kind: TriggerKind, instance_id: int) -> None:
        """382 -- queue every ability of `kind` printed on this card."""
        ref = self.cards[instance_id]
        for ability in abilities_of_kind(self.db[ref.card_id], kind):
            self._queue(ability, ref.controller, instance_id)

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
        """Feed a chosen instance back into the paused effect (355.2)."""
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
        """349-355 -- move the card to the Chain, pay, then finalize."""
        player = self._current_player
        state = self.players[player]
        ref = self.cards[action.instance_id]
        card = self.db[ref.card_id]

        from_hidden = ref.hidden_at
        if from_hidden is not None:
            # 811.1.b -- played from Hidden, "ignoring its base cost". The
            # card is already off the hand, so there is nothing to remove.
            ref.hidden_at = None
        elif action.accelerate:
            # 805.1.a -- pay [1][C] on top of the printed cost.
            state.pool.pay(
                card.energy + 1, card.power_domains + card.accelerate_power_domains
            )
            if action.instance_id in state.hand:
                state.hand.remove(action.instance_id)
            else:
                state.champion_zone.remove(action.instance_id)
        else:
            state.pool.pay(card.energy, card.power_domains)  # 444 Pay
            if action.instance_id in state.hand:
                state.hand.remove(action.instance_id)
            else:
                state.champion_zone.remove(action.instance_id)

        # 811.1.d.1 -- a hidden permanent must be played to that battlefield,
        # which overrides the normal restriction that gear go to base.
        self._playing_from_hidden = from_hidden

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
        item = ChainItem(
            kind="card", instance_id=action.instance_id, controller=player, pending=False
        )
        self.chain.append(item)  # 354 -- this Closes the State
        self.chain_passes = 0

        # 337.2 -- a finalized Unit or Gear resolves immediately.
        if resolves_immediately(card):
            self._resolve_top()
        else:
            self._open_priority_window(self.opponent(player))

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
            if card.type == "spell":
                self._fire(TriggerKind.ON_RESOLVE, item.instance_id)
                state.trash.append(item.instance_id)  # 351.2
            else:
                # 148.1.a.1 -- permanents enter the Base, unless 811.1.d.1
                # sends a card played from Hidden to its battlefield instead.
                hidden_bf = getattr(self, "_playing_from_hidden", None)
                ref.location = (
                    bf_location(hidden_bf) if hidden_bf is not None
                    else BASE_LOCATION
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
                self._playing_from_hidden = None
                self._fire(TriggerKind.ON_PLAY, item.instance_id)
        else:
            ability = activated_abilities(card)[item.ability_index]
            self._queue(ability, item.controller, item.instance_id)

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
        ref.location = action.destination
        name = self.db[ref.card_id].name
        # 719.3 -- a Top-Most Card and everything Attached to it are at the
        # same location; 719.3.a moves them together. Attached cards have no
        # move of their own (718.5.c), so this is the only way they travel.
        for attached in self.cards.values():
            # Only cards still on the board travel; anything that has left
            # is no longer attached to anything that can carry it.
            if attached.attached_to == ref.instance_id and attached.location is not None:
                attached.location = ref.location

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
        needed = max(0, self.might_of(target) - target.damage)
        dealt = min(combat.remaining, needed if needed > 0 else combat.remaining)
        target.damage += dealt
        combat.remaining -= dealt
        self._emit(
            f"P{combat.assigning} assigns {dealt} to {self.db[target.card_id].name}"
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
                pending=False,
                from_trigger=adds_resources,
            )
        )
        self.chain_passes = 0
        # 337.2 -- an ability that Adds resources resolves immediately, and
        # 346.1 keeps Focus with its controller.
        if adds_resources:
            self._resolve_top()
        else:
            self._open_priority_window(self.opponent(ref.controller))

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

    def _phase_awaken(self) -> None:
        """315.1 -- ready everything the turn player controls."""
        for ref in self.cards.values():
            if ref.controller == self.turn_player:
                ref.exhausted = False
        self._emit(f"-- P{self.turn_player} turn {self.turn_number}: awaken")

    def _phase_beginning(self) -> None:
        """315.2.b -- the turn player Holds every battlefield they control."""
        for bf in self.battlefields:
            bf.scored_by.clear()  # 470 -- once per battlefield per turn
        for bf in self.battlefields:
            if bf.controller == self.turn_player:
                self._score(self.turn_player, bf, "Hold")

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
        """317, then the turn passes (306). Turn-scoped modifiers expire."""
        for ref in self.cards.values():
            ref.might_this_turn = 0
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

    def _kill(self, ref: CardRef) -> None:
        """428 Kill -- the card goes to its owner's trash (108.2.b)."""
        # 457.1 -- gear left unattached at a battlefield is recalled; gear
        # attached to a dying unit detaches here.
        # 719.5 -- attached cards Detach, "remaining in their current zones".
        # They are not sent home here: an Equipment whose host died at a
        # battlefield stays there until 457.1 recalls it at the next Cleanup,
        # which is a window other effects can see.
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

    def _resolve_control(self) -> bool:
        """190.3.b.1 -- clear Contested when its author has left and no
        showdown or combat is running there."""
        changed = False
        for bf in self.battlefields:
            if not bf.contested or bf.contested_by is None:
                continue
            busy = (
                (self.showdown is not None and self.showdown.battlefield == bf.index)
                or (self.combat is not None and self.combat.battlefield == bf.index)
            )
            if busy:
                continue
            occupants = self.units_at(bf_location(bf.index))
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
                ref.location = BASE_LOCATION
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
        for ref in self.cards.values():  # 466.5 -- clear all marked damage
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
                self._fire(kind, ref.instance_id)
        self._resolve_effects()

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
