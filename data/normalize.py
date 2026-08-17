"""Merge cached raw sources into one canonical data/cards.json.

    python3.12 data/normalize.py

Reads only from data/raw/ -- never the network. Produces:

  * data/cards.json          canonical records keyed by riftbound_id
  * data/DISCREPANCIES.md    every field where sources disagree

Design: each source gets an *adapter* that turns its raw payload into
`CanonicalCard` records. Adapters are the only source-shape-aware code; the
merge below is shape-agnostic, so adding a fourth source means writing one
adapter and nothing else.

Conflict rule (from the project brief): prefer the API over the sheet. Encoded
as `sources.PRECEDENCE`. Every override is logged -- silently preferring one
source is exactly the kind of hidden approximation this project forbids.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import PRECEDENCE  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
CARDS_JSON = HERE / "cards.json"
DISCREPANCIES_MD = HERE / "DISCREPANCIES.md"


@dataclass
class CanonicalCard:
    """The one card shape the rest of the system consumes.

    Field list is fixed by the project brief. `engine/` never reads this type;
    only `cards/` does, and only at load time.
    """

    riftbound_id: str
    name: str
    type: str  # unit | spell | gear | rune | battlefield | legend | ...
    cost: int | None = None
    energy: int | None = None
    might: int | None = None
    power: int | None = None
    runes: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    rules_text: str = ""
    is_champion: bool = False
    is_battlefield: bool = False
    is_rune: bool = False
    set: str = ""
    # Provenance: field name -> source key that supplied the winning value.
    provenance: dict[str, str] = field(default_factory=dict)


# Fields compared across sources when hunting for disagreements. `provenance`
# is bookkeeping, not data, so it is excluded.
COMPARED_FIELDS: tuple[str, ...] = tuple(
    f for f in CanonicalCard.__dataclass_fields__ if f != "provenance"
)


@dataclass
class Discrepancy:
    riftbound_id: str
    name: str
    field_name: str
    values: dict[str, Any]  # source key -> value
    chosen: Any
    chosen_from: str


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------
# Each adapter takes the source's directory under data/raw/ and yields
# CanonicalCard records.
#
# NOT YET IMPLEMENTED. Writing a field mapping without having seen a single
# real payload would mean guessing at key names, and a wrong guess here is
# invisible -- it produces a plausible cards.json full of Nones rather than an
# error. These get written against the actual cached bytes, once data/raw/ is
# populated by fetch.py.

Adapter = Callable[[Path], list[CanonicalCard]]


def adapt_riftcodex(raw_dir: Path) -> list[CanonicalCard]:
    raise NotImplementedError(
        "No Riftcodex payload has ever been observed; run data/fetch.py "
        "--source riftcodex first, then map its fields here."
    )


def adapt_riftscribe(raw_dir: Path) -> list[CanonicalCard]:
    raise NotImplementedError(
        "No RiftScribe payload has ever been observed; run data/fetch.py "
        "--source riftscribe first, then map its fields here."
    )


def adapt_community_sheet(raw_dir: Path) -> list[CanonicalCard]:
    raise NotImplementedError(
        "No spreadsheet CSV has ever been observed; run data/fetch.py "
        "--source community_sheet first, then map its columns here."
    )


ADAPTERS: dict[str, Adapter] = {
    "riftcodex": adapt_riftcodex,
    "riftscribe": adapt_riftscribe,
    "community_sheet": adapt_community_sheet,
}


# --------------------------------------------------------------------------
# Merge -- source-shape agnostic, so this is real code and is unit-testable
# with synthetic records today.
# --------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    """A source that simply omits a field is not disagreeing with one that has it."""
    return value is None or value == "" or value == [] or value == {}


def merge(
    by_source: dict[str, list[CanonicalCard]],
) -> tuple[dict[str, CanonicalCard], list[Discrepancy]]:
    """Fold per-source records into canonical ones, recording every conflict.

    Precedence is `sources.PRECEDENCE`, highest first. A source absent from
    PRECEDENCE loses to every source in it.
    """
    rank = {key: i for i, key in enumerate(PRECEDENCE)}
    lowest = len(PRECEDENCE)

    grouped: dict[str, dict[str, CanonicalCard]] = defaultdict(dict)
    for source_key, cards in by_source.items():
        for card in cards:
            grouped[card.riftbound_id][source_key] = card

    merged: dict[str, CanonicalCard] = {}
    discrepancies: list[Discrepancy] = []

    for card_id, per_source in sorted(grouped.items()):
        ordered = sorted(per_source.items(), key=lambda kv: rank.get(kv[0], lowest))
        winner_key, winner = ordered[0]
        out = CanonicalCard(riftbound_id=card_id, name=winner.name, type=winner.type)

        for fname in COMPARED_FIELDS:
            present = {
                skey: getattr(card, fname)
                for skey, card in ordered
                if not _is_empty(getattr(card, fname))
            }
            if not present:
                continue
            chosen_from, chosen = next(iter(present.items()))
            setattr(out, fname, chosen)
            out.provenance[fname] = chosen_from

            distinct = {json.dumps(v, sort_keys=True, default=str) for v in present.values()}
            if len(distinct) > 1:
                discrepancies.append(
                    Discrepancy(
                        riftbound_id=card_id,
                        name=out.name,
                        field_name=fname,
                        values=present,
                        chosen=chosen,
                        chosen_from=chosen_from,
                    )
                )

        merged[card_id] = out

    return merged, discrepancies


def render_discrepancies(discrepancies: list[Discrepancy], totals: dict[str, int]) -> str:
    lines = [
        "# Source discrepancies",
        "",
        "Generated by `data/normalize.py`. Do not edit by hand.",
        "",
        f"Precedence (highest first): {' > '.join(PRECEDENCE)}",
        "",
        "## Card counts per source",
        "",
        "| source | cards |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in sorted(totals.items())]
    lines += ["", f"## Disagreements: {len(discrepancies)}", ""]

    if not discrepancies:
        lines.append("None. Every field agreed across all sources that supplied it.")
        return "\n".join(lines) + "\n"

    by_field: dict[str, list[Discrepancy]] = defaultdict(list)
    for d in discrepancies:
        by_field[d.field_name].append(d)

    lines += ["| field | count |", "| --- | --- |"]
    lines += [
        f"| {fname} | {len(items)} |"
        for fname, items in sorted(by_field.items(), key=lambda kv: -len(kv[1]))
    ]
    lines.append("")

    for fname, items in sorted(by_field.items()):
        lines += [f"### `{fname}` ({len(items)})", ""]
        for d in sorted(items, key=lambda x: x.riftbound_id):
            lines.append(f"- **{d.riftbound_id}** {d.name} -> chose `{d.chosen!r}` from `{d.chosen_from}`")
            for skey, val in d.values.items():
                lines.append(f"  - `{skey}`: `{val!r}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    by_source: dict[str, list[CanonicalCard]] = {}
    missing: list[str] = []

    for key, adapter in ADAPTERS.items():
        raw_dir = RAW / key
        if not raw_dir.exists() or not any(raw_dir.iterdir()):
            missing.append(key)
            continue
        by_source[key] = adapter(raw_dir)

    if missing:
        print(f"no cached data for: {', '.join(missing)}", file=sys.stderr)
        print("run `python3.12 data/fetch.py --all` first", file=sys.stderr)
    if not by_source:
        return 1

    merged, discrepancies = merge(by_source)
    totals = {k: len(v) for k, v in by_source.items()}

    CARDS_JSON.write_text(
        json.dumps(
            {cid: asdict(card) for cid, card in sorted(merged.items())},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    DISCREPANCIES_MD.write_text(render_discrepancies(discrepancies, totals))

    print(f"{len(merged)} canonical cards -> {CARDS_JSON.name}")
    print(f"{len(discrepancies)} field disagreements -> {DISCREPANCIES_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
