"""The Riftbound game state.

Implements the frozen agent-facing contract in `engine.interface` for the 1v1
(Duel) mode of play (485). Rule citations are inline; anything approximated is
marked `APPROX` here and written up in RULES_QUESTIONS.md.

Determinism: all randomness comes from `self._rng`, seeded from the game seed.
Nothing here touches the `random` module's global generator.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
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
from cards.scripts import script_for
from engine.actions import (
    Action,
    ActivateAbility,
    AssignDamageTo,
    ChannelRune,
    ChooseBattlefield,
    ChooseTarget,
    Concede,
    ExhaustRuneForEnergy,
    Mulligan,
    PassPhase,
    PlayCard,
    RecycleRuneForPower,
    StandardMove,
)
from engine.zones import (
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
    # Phase to return to once the effect queue drains.
    _resume_phase: "Phase | None" = None
    _rng: random.Random = field(default_factory=random.Random)

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
        if self.phase is Phase.MAIN:
            return self._main_phase_actions(player)
        if self.phase is Phase.CHOOSING:
            assert self.awaiting is not None
            return [ChooseTarget(i) for i in self.awaiting.options]
        if self.phase is Phase.COMBAT_ASSIGN:
            return self._assign_actions(player)
        return [PassPhase()]

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

    def _main_phase_actions(self, player: int) -> list[Action]:
        state = self.players[player]
        actions: list[Action] = [PassPhase()]
        if self.allow_concede:
            actions.append(Concede())

        # Play a card from hand or the Champion Zone (108.3.d).
        for instance_id in state.hand + state.champion_zone:
            card = self.db[self.cards[instance_id].card_id]
            if card.type not in ("unit", "spell", "gear"):
                continue
            if state.pool.can_pay(card.energy, card.power_domains):
                actions.append(PlayCard(instance_id))

        # Standard Move (144): exhaust a unit, Base <-> Battlefield.
        for ref in self.cards.values():
            if ref.controller != player or ref.exhausted:
                continue
            if self.db[ref.card_id].type != "unit" or ref.location is None:
                continue
            if ref.location == BASE_LOCATION:
                # 144.4.a -- Base to a Battlefield.
                for bf in self.battlefields:
                    actions.append(StandardMove(ref.instance_id, bf_location(bf.index)))
            else:
                # 144.4.b -- Battlefield to Base.
                actions.append(StandardMove(ref.instance_id, BASE_LOCATION))
                # 144.4.c / 810 -- Ganking also allows Battlefield to Battlefield.
                if self.db[ref.card_id].has_ganking:
                    for bf in self.battlefields:
                        if bf_location(bf.index) != ref.location:
                            actions.append(
                                StandardMove(ref.instance_id, bf_location(bf.index))
                            )

        # Activated abilities on permanents the player controls (376, 145.2).
        for ref in self.cards.values():
            if ref.controller != player or ref.location is None:
                continue
            script = script_for(ref.card_id)
            if script is None:
                continue
            for index, ability in enumerate(script.of_kind(TriggerKind.ACTIVATED)):
                if self._can_pay_ability(ref, ability):
                    actions.append(ActivateAbility(ref.instance_id, index))

        # Rune abilities (164.2). Both are Reactions, so both are legal here.
        for instance_id in state.channeled_runes:
            ref = self.cards[instance_id]
            if not ref.exhausted:
                actions.append(ExhaustRuneForEnergy(instance_id))
            actions.append(RecycleRuneForPower(instance_id))

        return actions

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
        for name, value, _duration in ref.granted_keywords:
            if name == "Assault":
                assault += value
        bonus = assault if ref.is_attacker else 0
        # Attached gear contributes its printed Might (716).
        attached = sum(
            self.db[g.card_id].might
            for g in self.cards.values()
            if g.attached_to == ref.instance_id
        )
        return card.might + ref.might_this_turn + ref.might_permanent + bonus + attached

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
        script = script_for(ref.card_id)
        if script is None:
            return
        for ability in script.of_kind(kind):
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
        elif isinstance(action, Concede):
            self._apply_concede()
        elif isinstance(action, PassPhase):
            self._advance_phase()
        else:
            raise ValueError(f"unhandled action {action!r}")

        self._cleanup()

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
        player = self._current_player
        state = self.players[player]
        ref = self.cards[action.instance_id]
        card = self.db[ref.card_id]

        state.pool.pay(card.energy, card.power_domains)  # 444 Pay
        if action.instance_id in state.hand:
            state.hand.remove(action.instance_id)
        else:
            state.champion_zone.remove(action.instance_id)

        if card.type == "spell":
            # 351.2 -- a spell's effects execute, then it goes to the trash.
            self._emit(f"P{player} plays {card.name} (spell)")
            self._fire(TriggerKind.ON_RESOLVE, action.instance_id)
            state.trash.append(action.instance_id)
        else:
            # Units and Gear are played to the controller's Base (148.1.a.1);
            # they resolve immediately off the chain (337.2).
            ref.location = BASE_LOCATION
            ref.exhausted = False
            state.base.append(action.instance_id)
            self._emit(f"P{player} plays {card.name}")
            self._fire(TriggerKind.ON_PLAY, action.instance_id)
        self._resolve_effects()

    def _apply_move(self, action: StandardMove) -> None:
        """144 Standard Move; 190.3.a Contested; 450."""
        player = self._current_player
        ref = self.cards[action.instance_id]
        ref.exhausted = True  # 144.2 -- exhausting is the cost
        ref.location = action.destination
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
        ref.location = None
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
        """376 -- pay the costs, then queue the effects."""
        ref = self.cards[action.instance_id]
        script = script_for(ref.card_id)
        assert script is not None
        ability = script.of_kind(TriggerKind.ACTIVATED)[action.index]
        self._pay_ability(ref, ability)
        self._emit(f"P{ref.controller} activates {self.db[ref.card_id].name}")
        self._queue(ability, ref.controller, ref.instance_id)
        self._resolve_effects()

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

    def _phase_channel(self) -> None:
        """315.3 -- channel 2 runes; the player going second gets +1 once (485.7)."""
        state = self.players[self.turn_player]
        count = CHANNEL_PER_TURN
        if not state.has_channeled and self.turn_player != self.first_player:
            count += 1
        state.has_channeled = True
        channeled = 0
        for _ in range(count):
            if not state.rune_deck:
                break  # 315.3.b.1 -- channel as many as possible
            instance_id = state.rune_deck.pop(0)
            state.channeled_runes.append(instance_id)
            self.cards[instance_id].location = BASE_LOCATION
            self.cards[instance_id].exhausted = False
            channeled += 1
        self._emit(f"P{self.turn_player} channels {channeled}")

    def _phase_draw(self) -> None:
        """315.4 -- draw 1; an empty deck is a Burn Out (431)."""
        state = self.players[self.turn_player]
        if not state.main_deck:
            # 431 -- Burn Out. APPROX: the opponent gains 1 point (194.1.d)
            # without the choice the rule implies. See RQ-8.
            opponent = self.opponent(self.turn_player)
            self.players[opponent].points += 1
            self._emit(f"P{self.turn_player} burns out; P{opponent} +1 point")
            return
        self._draw(self.turn_player, 1)

    def _phase_main_start(self) -> None:
        """316.3 -- rune pools empty; unspent Energy and Power are lost."""
        for state in self.players:
            state.pool.clear()
        self._current_player = self.turn_player

    def _phase_ending(self) -> None:
        """317, then the turn passes (306). Turn-scoped modifiers expire."""
        for ref in self.cards.values():
            ref.might_this_turn = 0
            ref.granted_keywords = tuple(
                g for g in ref.granted_keywords if g[2] != Duration.THIS_TURN.value
            )
        self.units_enter_ready.clear()
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
            changed |= self._maybe_start_combat()
            if not changed:
                return

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
        for gear in self.cards.values():
            if gear.attached_to == ref.instance_id:
                gear.attached_to = None
                gear.location = BASE_LOCATION
        state = self.players[ref.controller]
        if ref.instance_id in state.base:
            state.base.remove(ref.instance_id)
        ref.location = None
        ref.damage = 0
        ref.is_attacker = ref.is_defender = False
        self.players[ref.owner].trash.append(ref.instance_id)
        self._emit(f"{self.db[ref.card_id].name} is killed")

    def _resolve_control(self) -> bool:
        """190.3.b.1 -- clear Contested when the contester has no units there."""
        changed = False
        for bf in self.battlefields:
            location = bf_location(bf.index)
            occupants = self.units_at(location)
            holders = {r.controller for r in occupants}

            in_combat = self.combat is not None and self.combat.battlefield == bf.index

            if bf.contested and bf.contested_by is not None:
                still_there = any(r.controller == bf.contested_by for r in occupants)
                if not still_there and not in_combat:
                    # 190.3.b.1 -- the contester left; clear at this Cleanup.
                    bf.contested = False
                    bf.contested_by = None
                    changed = True

            # 344.2 -- a Contested battlefield with no opposing units present
            # opens a Non-Combat Showdown, which resolves into Control being
            # established (190.3.b: Contested persists "until Control is
            # established or re-established").
            #
            # APPROX: the showdown's spell windows are not simulated, so it
            # resolves immediately in favour of the sole occupant. With no
            # scripted card text there is nothing a player could have played
            # into that window anyway. See RQ-10.
            if len(holders) == 1 and not in_combat:
                sole = next(iter(holders))
                if bf.contested:
                    bf.contested = False
                    bf.contested_by = None
                    changed = True
                if bf.controller != sole:
                    bf.controller = sole
                    changed = True
                    self._score(sole, bf, "Conquer")
        return changed

    def _maybe_start_combat(self) -> bool:
        """460-461 -- opposing units at a contested battlefield stage a combat."""
        if self.combat is not None or self.phase is Phase.GAME_OVER:
            return False
        for bf in self.battlefields:
            location = bf_location(bf.index)
            occupants = self.units_at(location)
            holders = {r.controller for r in occupants}
            if len(holders) < 2:
                continue
            attacker = bf.contested_by if bf.contested_by is not None else self.turn_player
            defender = self.opponent(attacker)
            self.combat = CombatState(
                battlefield=bf.index, attacker=attacker, defender=defender
            )
            for ref in occupants:
                ref.is_attacker = ref.controller == attacker
                ref.is_defender = ref.controller == defender
            self._emit(
                f"Combat at {self.db[bf.card_id].name}: "
                f"P{attacker} attacks P{defender}"
            )
            self._begin_damage_step()
            return True
        return False

    def _begin_damage_step(self) -> None:
        """465.2 -- sum Might per side; the Attacker assigns first."""
        assert self.combat is not None
        combat = self.combat
        location = bf_location(combat.battlefield)
        attack_might = sum(
            self.might_of(r) for r in self.units_at(location, combat.attacker)
        )
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
            defend_might = sum(
                self.might_of(r) for r in self.units_at(location, combat.defender)
            )
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
            # 466.2 -- both sides remain, attackers are recalled (454).
            for ref in attackers:
                ref.location = BASE_LOCATION
                ref.is_attacker = False
            self._emit("Attackers are recalled")
        elif attackers and not defenders:
            # 466.3 -- battlefield is Conquered; control changes hands.
            bf.controller = combat.attacker
            self._emit(f"P{combat.attacker} conquers {self.db[bf.card_id].name}")
            self._score(combat.attacker, bf, "Conquer")

        bf.contested = False  # 466.4
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
        if how == "Conquer":
            # 471.2.a -- Conquer abilities trigger. Gear attached to that
            # player's units carries the "When I conquer" abilities here.
            for ref in sorted(self.cards.values(), key=lambda r: r.instance_id):
                if ref.controller == player and ref.location is not None:
                    self._fire(TriggerKind.ON_CONQUER, ref.instance_id)
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
