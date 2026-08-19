"""Deck loading and the setup process (110-118, 485).

A deck is JSON:

    {
      "name": "Fury Aggro",
      "legend": "OGN-XXX",
      "champion": "OGN-027",
      "main": ["OGN-001", "OGN-001", ...],     # >= 40 (103.2)
      "runes": ["OGN-007", ...],               # exactly 12 (161.2.a)
      "battlefields": ["OGN-3xx", ...]         # exactly 3 (485.4.a)
    }

`build_state` performs setup up to the point where the first real choice
happens, so the caller (agent or UI) drives battlefield selection and
mulligans through `legal_actions()`.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from cards.database import CardDatabase, load as load_db
from engine.state import (
    BATTLEFIELDS_PER_PLAYER,
    Phase,
    RiftboundState,
)
from engine.zones import CardRef, PlayerState

DECKS_DIR = Path(__file__).resolve().parents[1] / "decks"

MIN_MAIN_DECK = 40  # 103.2
RUNE_DECK_SIZE = 12  # 161.2.a
MAX_COPIES = 3  # 103.2.b


class DeckError(ValueError):
    """A deck that does not satisfy deck construction (101-103)."""


@dataclass
class Deck:
    name: str
    legend: str
    champion: str
    main: list[str]
    runes: list[str]
    battlefields: list[str]

    @classmethod
    def from_dict(cls, raw: dict) -> "Deck":
        return cls(
            name=raw.get("name", "unnamed"),
            legend=raw["legend"],
            champion=raw["champion"],
            main=list(raw["main"]),
            runes=list(raw["runes"]),
            battlefields=list(raw["battlefields"]),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Deck":
        return cls.from_dict(json.loads(Path(path).read_text()))


def validate(deck: Deck, db: CardDatabase) -> list[str]:
    """Return deck-construction violations. Empty list means legal.

    Domain Identity (103.1.b) is checked against the legend's domains.
    """
    problems: list[str] = []

    missing = [cid for cid in deck.main + deck.runes + deck.battlefields if cid not in db]
    if deck.legend not in db:
        missing.append(deck.legend)
    if deck.champion not in db:
        missing.append(deck.champion)
    if missing:
        problems.append(f"unknown card ids: {sorted(set(missing))[:6]}")
        return problems

    if len(deck.main) < MIN_MAIN_DECK:
        problems.append(f"main deck has {len(deck.main)}, needs >= {MIN_MAIN_DECK} (103.2)")
    if len(deck.runes) != RUNE_DECK_SIZE:
        problems.append(f"rune deck has {len(deck.runes)}, needs {RUNE_DECK_SIZE} (161.2.a)")
    if len(deck.battlefields) != BATTLEFIELDS_PER_PLAYER:
        problems.append(
            f"{len(deck.battlefields)} battlefields, needs "
            f"{BATTLEFIELDS_PER_PLAYER} (485.4.a)"
        )

    counts: dict[str, int] = {}
    for card_id in deck.main:
        name = db[card_id].name
        counts[name] = counts.get(name, 0) + 1
    over = {n: c for n, c in counts.items() if c > MAX_COPIES}
    if over:
        problems.append(f"more than {MAX_COPIES} copies by name (103.2.b): {over}")

    # 825.3.a -- "A deck can contain only one card of a given name if the card
    # has Unique." A tighter limit than 103.2.b's three, and it is the only
    # thing Unique does: 825.4 says it has no effect during gameplay.
    unique_over = {
        name: count for name, count in counts.items()
        if count > 1 and any(
            db[cid].name == name and db[cid].has_unique for cid in deck.main
        )
    }
    if unique_over:
        problems.append(f"more than one copy of a Unique card (825.3.a): {unique_over}")

    identity = set(db[deck.legend].domains)
    if identity:
        # 103.1.b.4 -- a multi-domain card needs all its domains present.
        offenders = sorted(
            {
                db[cid].name
                for cid in deck.main
                if db[cid].domains and not set(db[cid].domains) <= identity
            }
        )
        if offenders:
            problems.append(
                f"outside Domain Identity {sorted(identity)} (103.1.b): "
                f"{offenders[:6]}"
            )

    for card_id in deck.runes:
        if db[card_id].type != "rune":
            problems.append(f"{db[card_id].name} is not a rune (161)")
            break
    for card_id in deck.battlefields:
        if db[card_id].type != "battlefield":
            problems.append(f"{db[card_id].name} is not a battlefield")
            break
    return problems


def roll_for_first_player(rng: random.Random) -> tuple[int, int, int]:
    """115 -- "any fair random method". Two d6; higher goes first, reroll ties.

    Returns (player0_roll, player1_roll, first_player).
    """
    while True:
        a, b = rng.randint(1, 6), rng.randint(1, 6)
        if a != b:
            return a, b, (0 if a > b else 1)


def build_state(
    deck0: Deck,
    deck1: Deck,
    seed: int,
    db: CardDatabase | None = None,
    validate_decks: bool = True,
) -> RiftboundState:
    """Run setup steps 111-115 and stop at the first player choice (485.5)."""
    db = db or load_db()
    decks = [deck0, deck1]

    if validate_decks:
        for pid, deck in enumerate(decks):
            problems = validate(deck, db)
            if problems:
                raise DeckError(f"player {pid} deck '{deck.name}': {'; '.join(problems)}")

    rng = random.Random(seed)
    state = RiftboundState(db=db, seed=seed)
    state._rng = rng

    next_instance = 0

    def mint(card_id: str, owner: int) -> int:
        nonlocal next_instance
        instance_id = next_instance
        next_instance += 1
        state.cards[instance_id] = CardRef(
            instance_id=instance_id, card_id=card_id, owner=owner, controller=owner
        )
        return instance_id

    for pid, deck in enumerate(decks):
        player = PlayerState(player_id=pid)
        player.legend = mint(deck.legend, pid)  # 111
        player.champion_zone.append(mint(deck.champion, pid))  # 112

        main = [mint(cid, pid) for cid in deck.main]
        runes = [mint(cid, pid) for cid in deck.runes]
        rng.shuffle(main)  # 114
        rng.shuffle(runes)
        player.main_deck = main
        player.rune_deck = runes
        state.players.append(player)

        # 113 -- battlefields set aside; one of three chosen during setup.
        state.battlefield_choices.append(list(deck.battlefields))

    roll0, roll1, first = roll_for_first_player(rng)  # 115
    state.die_roll = (roll0, roll1)
    state.first_player = first
    state.turn_player = first
    state._current_player = first
    state.phase = Phase.SETUP_BATTLEFIELD
    state._emit(f"Die roll: P0={roll0} P1={roll1} -> P{first} goes first")
    return state


def load_deck(name_or_path: str) -> Deck:
    """Load a deck by file path, or by name from `decks/`."""
    path = Path(name_or_path)
    if path.exists():
        return Deck.load(path)
    candidate = DECKS_DIR / f"{name_or_path}.json"
    if candidate.exists():
        return Deck.load(candidate)
    raise FileNotFoundError(f"no deck at {name_or_path} or {candidate}")


def available_decks() -> list[str]:
    return sorted(p.stem for p in DECKS_DIR.glob("*.json"))
