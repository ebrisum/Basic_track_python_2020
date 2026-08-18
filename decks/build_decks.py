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

import argparse
import json
import re
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


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def discover(db, limit: int) -> list[tuple[str, str, str]]:
    """Pick `limit` legends that can each seed a legal, playable deck.

    Training on one matchup teaches the matchup, not the game -- the weights
    have no way to tell "this is good in Riftbound" from "this is good against
    Volibear". A field of decks is the cheapest fix available, and the pool
    already holds 94 legends.

    Legends are taken in a spread across domain identities rather than in card
    order, so the field is varied rather than five flavours of Fury.
    """
    legends = sorted(
        (c for c in db.cards.values() if c.type == "legend" and c.domains),
        key=lambda c: c.card_id,
    )
    by_identity: dict[tuple, list] = {}
    for legend in legends:
        by_identity.setdefault(tuple(sorted(legend.domains)), []).append(legend)

    specs: list[tuple[str, str, str]] = []
    round_index = 0
    while len(specs) < limit:
        added = False
        for identity in sorted(by_identity):
            bucket = by_identity[identity]
            if round_index >= len(bucket) or len(specs) >= limit:
                continue
            legend = bucket[round_index]
            label = f"{legend.name} ({'/'.join(legend.domains)})"
            specs.append((legend.card_id, slugify(legend.name), label))
            added = True
        if not added:
            break
        round_index += 1
    return specs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=2,
                    help="how many decks to build; >2 gives training a field "
                         "instead of a single matchup")
    ap.add_argument("--keep-starters", action="store_true", default=True,
                    help="always include the two named starter decks")
    ap.add_argument("--force", action="store_true",
                    help="overwrite decks that already exist. Off by default: "
                         "a deck on disk is a fixture, not an output. Replays "
                         "are recorded against exact decklists, so rebuilding "
                         "one silently invalidates every game recorded with "
                         "it -- which is how this flag came to exist.")
    ap.add_argument("--min-implemented", type=float, default=0.0,
                    help="reject decks below this fraction of executable card "
                         "text (1.0 = every card plays as printed). Training "
                         "on a deck where a third of the cards are inert "
                         "teaches a different game than the printed one.")
    args = ap.parse_args(argv)

    db = load()
    specs = [
        ("OGN-251", "jinx_chaos_fury", "Jinx - Loose Cannon (Chaos/Fury)"),
        ("OGN-249", "volibear_body_fury", "Volibear - Relentless Storm (Body/Fury)"),
    ]
    if args.count > len(specs):
        seen = {slug for _, slug, _ in specs}
        for spec in discover(db, args.count * 3):
            if len(specs) >= args.count:
                break
            if spec[1] in seen or spec[0] in {s[0] for s in specs}:
                continue
            specs.append(spec)
            seen.add(spec[1])

    written = 0
    failed = []
    for index, (legend_id, slug, label) in enumerate(specs):
        try:
            deck = build(legend_id, label, db, bf_offset=index)
        except ValueError as exc:
            # Not every legend has 40 legal, priceable cards inside its
            # identity; those are skipped rather than shipped broken.
            failed.append(f"{slug}: {exc}")
            continue
        problems = validate(deck, db)
        if problems:
            failed.append(f"{slug}: {problems}")
            continue
        coverage = sum(1 for cid in deck.main if db[cid].text_implemented) / len(deck.main)
        if coverage < args.min_implemented:
            failed.append(f"{slug}: only {coverage:.0%} of card text executes")
            continue
        path = HERE / f"{slug}.json"
        if path.exists() and not args.force:
            print(f"{path.name}: already exists, left alone (--force to rebuild)")
            written += 1
            continue
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
            f"{vanilla}/{len(deck.main)} fully-implemented text "
            f"({vanilla / len(deck.main):.0%})"
        )
        written += 1
    if failed:
        print(f"\nskipped {len(failed)} legend(s) that cannot field a legal deck:")
        for line in failed[:5]:
            print(f"  {line}")
    print(f"\n{written} deck(s) written to {HERE}/")
    return 0 if written >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
