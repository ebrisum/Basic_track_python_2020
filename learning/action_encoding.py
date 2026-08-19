"""A stable, factorized action encoding (`TCG_AI_BUILD.md` section 7).

A policy head emits a fixed-width vector of logits, so every action the engine
can offer needs an index that means the same thing every time, and a mask
saying which of those indices are legal right now. Section 29 makes "the agent
never attempts an illegal action" an acceptance criterion; a mask is how that
becomes structural instead of hoped for.

The shape of the problem
------------------------

Riftbound actions name cards by `instance_id`, which is a per-game counter. It
cannot be an index: instance 47 is a different card in every game, and the
space would have to be as large as the longest game. The standard answer, and
the one used here, is to point at a **slot** -- a position in a canonical
ordering of the objects the observation exposes -- so index `k` always means
"the k-th object this player can see", whatever that object happens to be.

That makes the encoding stable in the sense a policy needs: the same
(observation, action) pair always produces the same index, and an index means
the same *kind* of thing across every game. It is deliberately not stable in a
sense it cannot be: a card's slot shifts when other cards enter or leave the
observation, because slots are positions, not identities. Sorting by
`instance_id` keeps that drift monotone -- a card's slot only moves when
objects on one side of it appear or disappear -- which is the best available
without a global card-identity space that could not tell two copies apart.

Layout
------

The space is a concatenation of per-action-type segments, each laid out as
`slot * options + option`, so the factorization section 7 asks for --
(type, source, option) -- is recoverable from a flat index by `factor()` and
does not have to be reconstructed by the caller.

Capacities are set from measurement, not guessed. 150 fuzzed games of the two
Milestone 1 decks peaked at 84 addressable objects, 26 cards in hand, 2
battlefields, 1 activatable ability per card and mulligans of 2. The limits
below sit above those with room for a pool that leans harder on tokens.
Exceeding one raises `EncodingOverflow` rather than dropping the action: a
silently unencodable action is exactly the hidden approximation the project
forbids.
"""

from __future__ import annotations

from dataclasses import dataclass

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

# 485.5 -- each player brings 3 battlefields and selects 1, so a 1v1 board
# carries 2. The cap leaves room for a format that seats more.
MAX_BATTLEFIELDS = 4
# 117 -- "set aside up to two cards".
MAX_MULLIGAN = 2
# 485.5 again: the choice is between the player's own 3.
BATTLEFIELD_CHOICES = 3


class EncodingOverflow(RuntimeError):
    """An observation held more objects than the encoding has slots for."""


@dataclass(frozen=True)
class Factored:
    """The (type, source, option) decomposition of a flat index."""

    action_type: str
    slot: int
    option: int


@dataclass(frozen=True)
class _Segment:
    name: str
    start: int
    slots: int
    options: int

    @property
    def width(self) -> int:
        return self.slots * self.options


class ActionEncoder:
    """Encodes actions to indices in a fixed-width space, and back.

    Every method takes a `RiftboundObservation`. None of them may take a
    `RiftboundState`: see the note in `learning/__init__.py`.
    """

    def __init__(
        self,
        max_objects: int = 160,
        max_hand: int = 40,
        max_abilities: int = 6,
    ) -> None:
        self.max_objects = max_objects
        self.max_hand = max_hand
        self.max_abilities = max_abilities
        # One-entry memo; see `slots`.
        self._slots_for = None
        self._slots_cache: tuple[int, ...] = ()
        self._slots_index: dict[int, int] = {}

        moves = 1 + MAX_BATTLEFIELDS  # "base", plus one per battlefield
        mulligans = 1 + max_hand + max_hand * (max_hand - 1) // 2

        layout: list[tuple[str, int, int]] = [
            # (name, slots, options)
            (PassPhase.__name__, 1, 1),
            (ChannelRune.__name__, 1, 1),
            (Concede.__name__, 1, 1),
            (ChooseBattlefield.__name__, BATTLEFIELD_CHOICES, 1),
            # Mulligan names a *set* of up to two cards, so it cannot be one
            # slot. Its segment enumerates keep-all, each single, and each
            # unordered pair, over hand positions rather than object slots.
            (Mulligan.__name__, mulligans, 1),
            (PlayCard.__name__, max_objects, 4),  # plain / accel / repeat / both
            (HideCard.__name__, max_objects, MAX_BATTLEFIELDS),
            (StandardMove.__name__, max_objects, moves),
            (ActivateAbility.__name__, max_objects, max_abilities),
            (ExhaustRuneForEnergy.__name__, max_objects, 1),
            (RecycleRuneForPower.__name__, max_objects, 1),
            (AssignDamageTo.__name__, max_objects, 1),
            (ChooseTarget.__name__, max_objects, 1),
        ]

        self.segments: dict[str, _Segment] = {}
        cursor = 0
        for name, slots, options in layout:
            segment = _Segment(name, cursor, slots, options)
            self.segments[name] = segment
            cursor += segment.width
        self.size = cursor

    # -- the slot table ----------------------------------------------------

    def addressable(self, obs) -> list[int]:
        """Every instance this observation lets the viewer identify.

        The union of the zones the privacy rules expose to this player: their
        own hand (128.4), everything on the board (107.1.d, 107.2.c), both
        trashes (108.2.d), the champion and legend zones (108.3.e, 107.4.c),
        opponent cards revealed from hand (424), any facedown card this player
        controls (128.4), and the options of a pending choice (431.1.c.1).
        """
        ids: set[int] = {card.instance_id for card in obs.hand}
        ids |= {card.instance_id for card in obs.board}
        for trash in obs.trash:
            ids |= {card.instance_id for card in trash}
        for card in obs.champion_zone + obs.legend:
            if card is not None:
                ids.add(card.instance_id)
        ids |= {card.instance_id for card in obs.revealed_opponent_hand}
        ids |= {card.instance_id for card in obs.choice_options}
        for battlefield in obs.battlefields:
            if battlefield.facedown_card is not None:
                ids.add(battlefield.facedown_card.instance_id)
        return sorted(ids)

    def slots(self, obs) -> tuple[int, ...]:
        """The canonical slot table: instance ids in ascending order.

        Memoised for the most recent observation, because the caller that
        matters -- encoding every legal action at one decision, then the mask
        -- asks for the same table five to fifty times in a row, and building
        it walks every zone and sorts. The memo holds a strong reference to
        the observation it is keyed on, which is what makes keying on `id()`
        safe: an object that cannot be collected cannot have its id reused.
        """
        if obs is self._slots_for:
            return self._slots_cache
        ids = self.addressable(obs)
        if len(ids) > self.max_objects:
            raise EncodingOverflow(
                f"{len(ids)} addressable objects exceeds max_objects="
                f"{self.max_objects}"
            )
        table = tuple(ids)
        self._slots_for = obs
        self._slots_cache = table
        self._slots_index = {instance_id: slot
                             for slot, instance_id in enumerate(table)}
        return table

    def _slot_of(self, obs, instance_id: int) -> int:
        self.slots(obs)  # populates the index for this observation
        try:
            return self._slots_index[instance_id]
        except KeyError:
            raise KeyError(
                f"instance {instance_id} is not addressable in this observation"
            ) from None

    def _hand_positions(self, obs) -> tuple[int, ...]:
        ids = tuple(sorted(card.instance_id for card in obs.hand))
        if len(ids) > self.max_hand:
            raise EncodingOverflow(
                f"{len(ids)} cards in hand exceeds max_hand={self.max_hand}"
            )
        return ids

    # -- mulligan subsets --------------------------------------------------

    def _mulligan_option(self, positions: tuple[int, ...]) -> int:
        """Index a keep-all / single / unordered-pair choice over hand slots."""
        if not positions:
            return 0
        if len(positions) == 1:
            return 1 + positions[0]
        if len(positions) > MAX_MULLIGAN:
            raise EncodingOverflow(
                f"mulligan of {len(positions)} exceeds the rules' limit of "
                f"{MAX_MULLIGAN} (117)"
            )
        low, high = sorted(positions)
        # Pairs enumerated in lexicographic order after the singles.
        offset = low * self.max_hand - low * (low + 1) // 2 + (high - low - 1)
        return 1 + self.max_hand + offset

    def _mulligan_positions(self, option: int) -> tuple[int, ...]:
        if option == 0:
            return ()
        if option <= self.max_hand:
            return (option - 1,)
        offset = option - 1 - self.max_hand
        for low in range(self.max_hand):
            span = self.max_hand - low - 1
            if offset < span:
                return (low, low + 1 + offset)
            offset -= span
        raise EncodingOverflow(f"mulligan option {option} is outside the space")

    # -- encode ------------------------------------------------------------

    def encode(self, obs, action: Action) -> int:
        name = type(action).__name__
        segment = self.segments.get(name)
        if segment is None:
            raise KeyError(f"{name} has no segment in this encoding")
        slot, option = self._factor(obs, action)
        if slot >= segment.slots:
            raise EncodingOverflow(
                f"{name} slot {slot} exceeds its {segment.slots} slots"
            )
        if option >= segment.options:
            raise EncodingOverflow(
                f"{name} option {option} exceeds its {segment.options} options"
            )
        return segment.start + slot * segment.options + option

    def _factor(self, obs, action: Action) -> tuple[int, int]:
        if isinstance(action, (PassPhase, ChannelRune, Concede)):
            return 0, 0
        if isinstance(action, ChooseBattlefield):
            return action.index, 0
        if isinstance(action, Mulligan):
            hand = self._hand_positions(obs)
            positions = []
            for instance_id in action.instance_ids:
                if instance_id not in hand:
                    raise KeyError(
                        f"mulligan names {instance_id}, which is not in hand"
                    )
                positions.append(hand.index(instance_id))
            return self._mulligan_option(tuple(sorted(positions))), 0
        if isinstance(action, PlayCard):
            option = (1 if action.accelerate else 0) + (2 if action.repeat else 0)
            return self._slot_of(obs, action.instance_id), option
        if isinstance(action, HideCard):
            if action.battlefield >= MAX_BATTLEFIELDS:
                raise EncodingOverflow(
                    f"battlefield {action.battlefield} exceeds MAX_BATTLEFIELDS"
                )
            return self._slot_of(obs, action.instance_id), action.battlefield
        if isinstance(action, StandardMove):
            return (
                self._slot_of(obs, action.instance_id),
                self._destination_option(action.destination),
            )
        if isinstance(action, ActivateAbility):
            return self._slot_of(obs, action.instance_id), action.index
        if isinstance(
            action,
            (ExhaustRuneForEnergy, RecycleRuneForPower, AssignDamageTo, ChooseTarget),
        ):
            return self._slot_of(obs, action.instance_id), 0
        raise KeyError(f"{type(action).__name__} has no factorization")

    @staticmethod
    def _destination_option(destination: str) -> int:
        if destination == "base":
            return 0
        if destination.startswith("bf:"):
            index = int(destination.split(":", 1)[1])
            if index >= MAX_BATTLEFIELDS:
                raise EncodingOverflow(f"{destination} exceeds MAX_BATTLEFIELDS")
            return 1 + index
        raise KeyError(f"unrecognised move destination {destination!r}")

    # -- decode ------------------------------------------------------------

    def factor(self, index: int) -> Factored:
        """Recover (type, slot, option) from a flat index."""
        if not 0 <= index < self.size:
            raise IndexError(f"{index} is outside the {self.size}-wide space")
        for segment in self.segments.values():
            if segment.start <= index < segment.start + segment.width:
                local = index - segment.start
                return Factored(
                    segment.name, local // segment.options, local % segment.options
                )
        raise IndexError(f"{index} fell between segments, which cannot happen")

    def decode(self, obs, index: int) -> Action | None:
        """The action an index names in this observation, or None.

        None means the index is well-formed but names nothing here -- a slot
        past the end of the table, or a battlefield that is not in play. A
        masked policy never selects one; `decode` returns None rather than
        raising so a caller can audit an unmasked argmax without a try block.
        """
        parts = self.factor(index)
        name, slot, option = parts.action_type, parts.slot, parts.option

        if name == PassPhase.__name__:
            return PassPhase()
        if name == ChannelRune.__name__:
            return ChannelRune()
        if name == Concede.__name__:
            return Concede()
        if name == ChooseBattlefield.__name__:
            return ChooseBattlefield(index=slot)
        if name == Mulligan.__name__:
            hand = self._hand_positions(obs)
            positions = self._mulligan_positions(slot)
            if any(position >= len(hand) for position in positions):
                return None
            return Mulligan(instance_ids=tuple(hand[p] for p in positions))

        table = self.slots(obs)
        if slot >= len(table):
            return None
        instance_id = table[slot]

        if name == PlayCard.__name__:
            return PlayCard(
                instance_id=instance_id,
                accelerate=bool(option & 1),
                repeat=bool(option & 2),
            )
        if name == HideCard.__name__:
            if option >= len(obs.battlefields):
                return None
            return HideCard(instance_id=instance_id, battlefield=option)
        if name == StandardMove.__name__:
            if option == 0:
                destination = "base"
            elif option - 1 < len(obs.battlefields):
                destination = f"bf:{option - 1}"
            else:
                return None
            return StandardMove(instance_id=instance_id, destination=destination)
        if name == ActivateAbility.__name__:
            return ActivateAbility(instance_id=instance_id, index=option)
        if name == ExhaustRuneForEnergy.__name__:
            return ExhaustRuneForEnergy(instance_id=instance_id)
        if name == RecycleRuneForPower.__name__:
            return RecycleRuneForPower(instance_id=instance_id)
        if name == AssignDamageTo.__name__:
            return AssignDamageTo(instance_id=instance_id)
        if name == ChooseTarget.__name__:
            return ChooseTarget(instance_id=instance_id)
        raise KeyError(f"no decoder for segment {name}")

    # -- mask --------------------------------------------------------------

    def mask(self, obs, legal: list[Action]) -> list[bool]:
        """A `size`-wide boolean mask, True exactly where an action is legal."""
        out = [False] * self.size
        for action in legal:
            out[self.encode(obs, action)] = True
        return out
