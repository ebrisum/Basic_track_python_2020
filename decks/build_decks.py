"""Generate legal starter decks from the real card pool.

    .venv/bin/python decks/build_decks.py

Builds decks that satisfy deck construction (101-103) and the 1v1 mode
(485.4.a) using only cards the engine can actually price -- i.e. those with a
complete (energy, power) cost, and Might for units.

These are *starter* decks so the game is playable today; they are not tuned,
and they are not the Milestone 1 lists. Replace them with real decklists when
those arrive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cards.database import load  # noqa: E402
from engine.setup import (  # noqa: E402
    MIN_MAIN_DECK,
    RUNE_DECK_SIZE,
    Deck,
    validate,
)

HERE = Path(__file__).resolve().parent
BATTLEFIELDS_PER_DECK = 3
MAX_COPIES = 3


def build(legend_id: str, name: str, db, prefer_vanilla: bool = True,
          bf_offset: int = 0) -> Deck:
    """Build a legal deck around `legend_id`.

    `prefer_vanilla` puts cards whose rules text the engine actually executes
    first, so a starter game behaves as printed rather than silently ignoring
    text. See RQ-5.
    """
    legend = db[legend_id]
    identity = set(legend.domains)

    def in_identity(card) -> bool:
        # 103.1.b.3-4 -- a card's domains must all be inside the identity.
        return not card.domains or set(card.domains) <= identity

    pool = [
        c
        for c in db.cards.values()
        if c.type in ("unit", "spell", "gear") and in_identity(c)
    ]
    # Vanilla first (text fully implemented), then by cost so the curve is sane.
    pool.sort(key=lambda c: (not c.text_implemented if prefer_vanilla else 0,
                             c.energy + c.power, c.card_id))

    champion = next(
        (c for c in pool if c.type == "unit" and c.is_champion),
        None,
    )
    if champion is None:
        raise ValueError(f"no champion unit inside {legend.name}'s identity")

    main: list[str] = []
    for card in pool:
        if len(main) >= MIN_MAIN_DECK:
            break
        copies = min(MAX_COPIES, MIN_MAIN_DECK - len(main))
        main.extend([card.card_id] * copies)
    if len(main) < MIN_MAIN_DECK:
        raise ValueError(f"only {len(main)} legal cards for {legend.name}")

    # Runes matching the identity, topped up to 12. Alternate-art printings are
    # distinct ids but the same rune, which is fine -- the 3-copy limit is on
    # Main Deck cards (103.2.b), not the Rune Deck.
    rune_pool = [c for c in db.cards.values() if c.type == "rune"]
    matching = [c for c in rune_pool if set(c.domains) <= identity and c.domains]
    runes: list[str] = []
    while len(runes) < RUNE_DECK_SIZE and matching:
        for card in matching:
            if len(runes) >= RUNE_DECK_SIZE:
                break
            runes.append(card.card_id)

    # Each deck gets a distinct trio so the two battlefields in play are not
    # the same card twice (485.4.a -- each player contributes three).
    all_bf = [
        c.card_id
        for c in sorted(db.cards.values(), key=lambda c: c.card_id)
        if c.type == "battlefield"
    ]
    start = (bf_offset * BATTLEFIELDS_PER_DECK) % max(1, len(all_bf) - BATTLEFIELDS_PER_DECK)
    battlefields = all_bf[start : start + BATTLEFIELDS_PER_DECK]

    return Deck(
        name=name,
        legend=legend_id,
        champion=champion.card_id,
        main=main,
        runes=runes,
        battlefields=battlefields,
    )


def main() -> int:
    db = load()
    specs = [
        ("OGN-251", "jinx_chaos_fury", "Jinx - Loose Cannon (Chaos/Fury)"),
        ("OGN-249", "volibear_body_fury", "Volibear - Relentless Storm (Body/Fury)"),
    ]
    written = 0
    for index, (legend_id, slug, label) in enumerate(specs):
        deck = build(legend_id, label, db, bf_offset=index)
        problems = validate(deck, db)
        if problems:
            print(f"REJECTED {slug}: {problems}")
            continue
        path = HERE / f"{slug}.json"
        path.write_text(
            json.dumps(
                {
                    "name": deck.name,
                    "legend": deck.legend,
                    "champion": deck.champion,
                    "main": deck.main,
                    "runes": deck.runes,
                    "battlefields": deck.battlefields,
                },
                indent=2,
            )
            + "\n"
        )
        vanilla = sum(1 for cid in deck.main if db[cid].text_implemented)
        print(
            f"{path.name}: {len(deck.main)} main, {len(deck.runes)} runes, "
            f"{vanilla}/{len(deck.main)} fully-implemented text"
        )
        written += 1
    return 0 if written == len(specs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
