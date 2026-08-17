"""Per-player observations with hidden information removed.

Privacy rules (128, and per-zone at 107-108):

- Own hand: visible to its owner only.
- Main Deck and Rune Deck order: Secret (108.4.d, 108.5.d) -- only sizes leak.
- Permanents and runes in Bases: Public (107.1.d).
- Battlefields and permanents at them: Public (107.2.c).
- Trashes: Public (108.2.d) -- contents, not just counts.
- Champion Zone: Public (108.3.e).
- Legend Zone: Public (107.4.c).

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
    might: int  # printed
    current_might: int  # printed + buffs + attachments (+ Assault while attacking)
    damage: int
    exhausted: bool
    controller: int
    owner: int
    location: str | None
    domains: tuple[str, ...]
    keywords: tuple[str, ...]
    rules_text: str
    text_implemented: bool
    script_note: str
    image_url: str
    image_alt: str
    is_champion: bool
    attached_to: int | None
    granted_keywords: tuple[tuple[str, int, str], ...]
    is_attacker: bool
    is_defender: bool


@dataclass(frozen=True)
class VisibleBattlefield:
    index: int
    card_id: str
    name: str
    image_url: str
    controller: int | None
    contested: bool
    contested_by: int | None
    scored_by: tuple[int, ...]
    provider: int


@dataclass(frozen=True)
class RiftboundObservation:
    """One player's legal view of the game."""

    player_id: int
    phase: str
    turn_player: int
    turn_number: int
    current_player: int
    points: tuple[int, int]
    hand: tuple[VisibleCard, ...]
    opponent_hand_size: int
    deck_sizes: tuple[int, int]
    rune_deck_sizes: tuple[int, int]
    trash_sizes: tuple[int, int]
    # 108.2.d -- trashes are public, so both are exposed in full.
    trash: tuple[tuple[VisibleCard, ...], tuple[VisibleCard, ...]]
    board: tuple[VisibleCard, ...]
    battlefields: tuple[VisibleBattlefield, ...]
    pool: tuple[int, tuple[tuple[str, int], ...], int]
    opponent_pool: tuple[int, tuple[tuple[str, int], ...], int]
    # 108.3.e / 107.4 -- both players' champion zones and legends are public.
    champion_zone: tuple[VisibleCard | None, VisibleCard | None]
    legend: tuple[VisibleCard | None, VisibleCard | None]
    combat: tuple | None
    choice_prompt: str = ""
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
                current_might=state.might_of(ref) if card.type == "unit" else card.might,
                damage=ref.damage,
                exhausted=ref.exhausted,
                controller=ref.controller,
                owner=ref.owner,
                location=ref.location,
                domains=card.domains,
                keywords=tuple(str(k) for k in card.parsed_keywords),
                rules_text=card.rules_text,
                text_implemented=card.text_implemented,
                script_note=card.script_note,
                image_url=card.image_url,
                image_alt=card.image_alt,
                is_champion=card.is_champion,
                attached_to=ref.attached_to,
                granted_keywords=ref.granted_keywords,
                is_attacker=ref.is_attacker,
                is_defender=ref.is_defender,
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

        def one(ids: list[int]) -> VisibleCard | None:
            return visible(ids[0]) if ids else None

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
            trash=(
                tuple(visible(i) for i in state.players[0].trash),
                tuple(visible(i) for i in state.players[1].trash),
            ),
            board=board,
            battlefields=tuple(
                VisibleBattlefield(
                    index=bf.index,
                    card_id=bf.card_id,
                    name=db[bf.card_id].name,
                    image_url=db[bf.card_id].image_url,
                    controller=bf.controller,
                    contested=bf.contested,
                    contested_by=bf.contested_by,
                    scored_by=tuple(sorted(bf.scored_by)),
                    provider=bf.provider,
                )
                for bf in state.battlefields
            ),
            pool=pool_key(player_id),
            opponent_pool=pool_key(opponent),
            champion_zone=(
                one(state.players[0].champion_zone),
                one(state.players[1].champion_zone),
            ),
            legend=(
                visible(state.players[0].legend) if state.players[0].legend is not None else None,
                visible(state.players[1].legend) if state.players[1].legend is not None else None,
            ),
            combat=combat,
            choice_prompt=state.awaiting.prompt if state.awaiting else "",
            winner=state.winner,
            log_tail=tuple(state.log[-12:]),
        )

    def to_canonical_bytes(self) -> bytes:
        """Stable serialization.

        `log_tail` and `choice_prompt` are excluded -- they are presentation,
        and hashing them would make every replay depend on message wording.
        """
        payload = asdict(self)
        payload.pop("log_tail", None)
        payload.pop("choice_prompt", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
