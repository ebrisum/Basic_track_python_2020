"""Per-player observations with hidden information removed.

Privacy rules (128, and per-zone at 107-108):

- Own hand: visible to its owner only.
- Main Deck and Rune Deck order: Secret (108.4.d, 108.5.d) -- only sizes leak.
- Permanents and runes in Bases: Public (107.1.d).
- Battlefields and permanents at them: Public (107.2.c).
- Trashes: Public (108.2.d).
- Champion Zone: Public (108.3.e).

The serialization must be byte-stable: it backs replay hashing and will back
ISMCTS determinization.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class VisibleCard:
    instance_id: int
    card_id: str
    name: str
    type: str
    energy: int
    power: int
    might: int
    damage: int
    exhausted: bool
    controller: int
    location: str | None
    keywords: tuple[str, ...]
    rules_text: str
    text_implemented: bool


@dataclass(frozen=True)
class VisibleBattlefield:
    index: int
    card_id: str
    name: str
    controller: int | None
    contested: bool
    contested_by: int | None
    scored_by: tuple[int, ...]


@dataclass(frozen=True)
class RiftboundObservation:
    """One player's legal view of the game."""

    player_id: int
    phase: str
    turn_player: int
    turn_number: int
    current_player: int
    points: tuple[int, int]
    # Own hand in full; the opponent's as a count only.
    hand: tuple[VisibleCard, ...]
    opponent_hand_size: int
    deck_sizes: tuple[int, int]
    rune_deck_sizes: tuple[int, int]
    trash_sizes: tuple[int, int]
    board: tuple[VisibleCard, ...]
    battlefields: tuple[VisibleBattlefield, ...]
    pool: tuple[int, tuple[tuple[str, int], ...], int]
    opponent_pool: tuple[int, tuple[tuple[str, int], ...], int]
    champion_zone: tuple[VisibleCard, ...]
    combat: tuple | None
    winner: int | None = None
    log_tail: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def build(cls, state, player_id: int) -> "RiftboundObservation":
        opponent = 1 - player_id
        db = state.db

        def visible(instance_id: int) -> VisibleCard:
            ref = state.cards[instance_id]
            card = db[ref.card_id]
            return VisibleCard(
                instance_id=ref.instance_id,
                card_id=ref.card_id,
                name=card.name,
                type=card.type,
                energy=card.energy,
                power=card.power,
                might=card.might,
                damage=ref.damage,
                exhausted=ref.exhausted,
                controller=ref.controller,
                location=ref.location,
                keywords=card.keywords,
                rules_text=card.rules_text,
                text_implemented=card.text_implemented,
            )

        # Everything on the board is public (107.1.d, 107.2.c).
        board = tuple(
            visible(ref.instance_id)
            for ref in sorted(state.cards.values(), key=lambda r: r.instance_id)
            if ref.location is not None
        )

        def pool_key(pid: int):
            pool = state.players[pid].pool
            return (
                pool.energy,
                tuple(sorted(pool.power.items())),
                pool.universal_power,
            )

        combat = None
        if state.combat is not None:
            combat = (
                state.combat.battlefield,
                state.combat.attacker,
                state.combat.defender,
                state.combat.assigning,
                state.combat.remaining,
            )

        return cls(
            player_id=player_id,
            phase=state.phase.value,
            turn_player=state.turn_player,
            turn_number=state.turn_number,
            current_player=state.current_player,
            points=(state.players[0].points, state.players[1].points),
            hand=tuple(visible(i) for i in state.players[player_id].hand),
            opponent_hand_size=len(state.players[opponent].hand),
            deck_sizes=(
                len(state.players[0].main_deck),
                len(state.players[1].main_deck),
            ),
            rune_deck_sizes=(
                len(state.players[0].rune_deck),
                len(state.players[1].rune_deck),
            ),
            trash_sizes=(
                len(state.players[0].trash),
                len(state.players[1].trash),
            ),
            board=board,
            battlefields=tuple(
                VisibleBattlefield(
                    index=bf.index,
                    card_id=bf.card_id,
                    name=db[bf.card_id].name,
                    controller=bf.controller,
                    contested=bf.contested,
                    contested_by=bf.contested_by,
                    scored_by=tuple(sorted(bf.scored_by)),
                )
                for bf in state.battlefields
            ),
            pool=pool_key(player_id),
            opponent_pool=pool_key(opponent),
            champion_zone=tuple(
                visible(i) for i in state.players[player_id].champion_zone
            ),
            combat=combat,
            winner=state.winner,
            log_tail=tuple(state.log[-12:]),
        )

    def to_canonical_bytes(self) -> bytes:
        """Stable serialization. `log_tail` is excluded -- it is presentation,
        not game state, and including it would make every hash depend on
        message wording."""
        payload = asdict(self)
        payload.pop("log_tail", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
