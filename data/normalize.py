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
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import APITCG_SET_CODES, PRECEDENCE  # noqa: E402

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
    # Tri-state on purpose. `None` means "this source has no opinion", which is
    # different from `False` ("this source says no"). Defaulting these to False
    # made every source that simply does not model champions look like it was
    # actively contradicting the one that does -- 146 phantom conflicts.
    is_champion: bool | None = None
    is_battlefield: bool | None = None
    is_rune: bool | None = None
    set: str = ""
    # Card art. `image_url` is Riot's own CDN render (the actual card face);
    # `image_alt` is a TCGPlayer product photo used only as a fallback. They
    # come from different sources on purpose, so they never conflict.
    image_url: str = ""
    image_alt: str = ""
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
# CanonicalCard records. Adapters are the only source-shape-aware code here.
#
# Both were written against the actual cached bytes, not against documentation.

Adapter = Callable[[Path], list[CanonicalCard]]

# Canonical id is "<SETCODE>-<NUMBER>", e.g. "OGN-001". Variant printings keep
# their suffix ("OGN-007A"), since alternate arts are distinct printings of the
# same card and must not silently collapse onto each other.
ID_RE = re.compile(r"^([A-Z]{3})-(\d+)([A-Z]*)$")


def canonical_id(set_code: str, number: str) -> str | None:
    """Build a canonical id, or None if `number` is not a card number.

    apitcg numbers look like "001/298"; the denominator is set size, not part
    of the identity. Sealed products carry bare sequential numbers ("0", "3")
    and are rejected by the caller on cardType instead.
    """
    head = number.split("/")[0].strip()
    match = re.match(r"^(\d+)([A-Za-z]*)$", head)
    if not match:
        return None
    digits, suffix = match.groups()
    return f"{set_code.upper()}-{int(digits):03d}{suffix.upper()}"


def _strip_html(text: str) -> str:
    """apitcg descriptions carry <br>, <em> and CRLF; card text must be plain."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    # A <br> followed by a real newline in the source would otherwise leave a
    # blank line in the middle of card text.
    return re.sub(r"\n{2,}", "\n", text).strip()


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def adapt_riftbound_tools(raw_dir: Path) -> list[CanonicalCard]:
    """npm `riftbound-tools`: ids like "ogn-001", ints already parsed.

    ITS NUMERIC FIELDS ARE DELIBERATELY NOT MAPPED. Measured against apitcg
    across the 503 comparable overlapping cards:

        npm.might  == apitcg.might              0.6%
        npm.might  == apitcg.power             52.3%
        npm.energy == apitcg.energy            31.2%
        npm.cost   == apitcg.energy            50.5%

    0.6% agreement on `might` is not noise, it is a different quantity wearing
    the name, and no single hypothesis explains the rest either. Mapping any of
    them would put confidently-wrong stats into every simulation, so this
    source contributes only what it is demonstrably good at: coverage (it is
    the sole source for the UNL set), keyword/tag arrays, names and card text.

    See DISCREPANCIES.md and RULES_QUESTIONS.md RQ-2.
    """
    records = json.loads((raw_dir / "cards.json").read_text())
    out: list[CanonicalCard] = []
    for rec in records:
        raw_id = str(rec.get("id", "")).upper()
        if not ID_RE.match(raw_id):
            continue
        card_type = str(rec.get("type") or "").strip().lower()
        out.append(
            CanonicalCard(
                riftbound_id=raw_id,
                name=str(rec.get("name") or "").strip(),
                type=card_type,
                domains=sorted(rec.get("domain") or []),
                keywords=sorted(set(rec.get("keywords") or []) | set(rec.get("tags") or [])),
                rules_text=str(rec.get("text") or "").strip(),
                # Riot's official card render.
                image_url=str(rec.get("imageUrl") or "").strip(),
                # This source flattens champions into a bare "Unit", so it has
                # no opinion on is_champion -- left None, not False.
                is_battlefield=(card_type == "battlefield"),
                is_rune=(card_type == "rune"),
                set=str(rec.get("setCode") or "").upper(),
            )
        )
    return out


def adapt_apitcg(raw_dir: Path) -> list[CanonicalCard]:
    """apitcg: HTML descriptions, string numerics, semicolon-joined domains.

    Sealed products (booster packs, displays, decks) share these files and are
    identified by a null cardType; they are dropped.
    """
    out: list[CanonicalCard] = []
    for path in sorted(raw_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        for rec in json.loads(path.read_text()):
            card_type = (rec.get("cardType") or "").strip()
            if not card_type:
                continue  # sealed product, not a card
            set_id = (rec.get("set") or {}).get("id", "")
            set_code = APITCG_SET_CODES.get(set_id)
            if not set_code:
                continue
            card_id = canonical_id(set_code, str(rec.get("number") or ""))
            if card_id is None:
                continue

            subtypes = [t.strip() for t in card_type.split(";") if t.strip()]
            lowered = card_type.lower()
            out.append(
                CanonicalCard(
                    riftbound_id=card_id,
                    name=str(rec.get("name") or "").strip(),
                    # "Champion Unit" / "Signature Spell" -> the base type; the
                    # qualifier is preserved in is_champion and keywords.
                    type=subtypes[0].split()[-1].lower() if subtypes else "",
                    energy=_int_or_none(rec.get("energyCost")),
                    power=_int_or_none(rec.get("powerCost")),
                    might=_int_or_none(rec.get("might")),
                    domains=sorted(
                        d.strip()
                        for d in str(rec.get("domain") or "").split(";")
                        if d.strip() and d.strip().lower() != "none"
                    ),
                    keywords=sorted({t for t in subtypes if " " in t or "Token" in t}),
                    rules_text=_strip_html(str(rec.get("description") or "")),
                    image_alt=str((rec.get("images") or {}).get("large") or "").strip(),
                    is_champion="champion" in lowered,
                    is_battlefield="battlefield" in lowered,
                    is_rune="rune" in lowered,
                    set=set_code,
                )
            )
    return out


def adapt_community_sheet(raw_dir: Path) -> list[CanonicalCard]:
    """The community "All Card Info" sheet, supplied as an xlsx and cached as CSV.

    THE COLUMN HEADED "Might" IS THE POWER COST, not Might. Measured against
    apitcg over 529 comparable cards:

        sheet.Might == apitcg.power        99.3% (where filled)
        sheet.Might blank -> power == 0    98.3%
        combined                           98.9%
        sheet.Might == apitcg.might         1.1%

    1.1% agreement with the field it is named after is conclusive. This also
    explains RQ-1: the npm source derives from this sheet, which is why its
    `might` matched apitcg's `power` 52% of the time.

    `Energy` is mapped but agrees with apitcg on only ~60% of shared cards
    (scattered at +/-1, so neither source is obviously right). apitcg outranks
    it everywhere they overlap; for UNL and VEN this sheet is the only source
    and that uncertainty is unresolved. See RQ-11.

    Might itself is NOT mapped -- this sheet does not carry it under any
    column, and inventing it would repeat the RQ-1 mistake.
    """
    import csv

    path = raw_dir / "all_card_data.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    header = rows[0]
    col = {name: i for i, name in enumerate(header) if name}
    required = {"ID", "Name", "Card Type", "Domain", "Energy", "Might", "Ability"}
    if not required <= set(col):
        raise ValueError(f"community sheet is missing columns: {required - set(col)}")

    def cell(row: list[str], name: str) -> str:
        index = col.get(name)
        return (row[index].strip() if index is not None and index < len(row) else "")

    out: list[CanonicalCard] = []
    for row in rows[1:]:
        raw_id = cell(row, "ID").upper()
        if not ID_RE.match(raw_id):
            continue  # tokens like "UNL-T01" are not Main Deck cards
        card_type = cell(row, "Card Type").lower()
        domains = [
            d.strip()
            for d in cell(row, "Domain").replace(";", ",").split(",")
            if d.strip() and d.strip().lower() not in ("none", "colorless")
        ]
        tags = [t.strip() for t in cell(row, "Tags").split(",") if t.strip()]
        out.append(
            CanonicalCard(
                riftbound_id=raw_id,
                name=cell(row, "Name"),
                type=card_type,
                energy=_int_or_none(cell(row, "Energy")),
                # See the docstring: this column is the power cost, and a blank
                # means zero rather than unknown.
                power=_int_or_none(cell(row, "Might")) or 0,
                domains=sorted(domains),
                keywords=sorted(tags),
                rules_text=cell(row, "Ability"),
                image_url=cell(row, "Image URL"),
                is_battlefield=(card_type == "battlefield"),
                is_rune=(card_type == "rune"),
                set=raw_id.split("-")[0],
            )
        )
    return out


ADAPTERS: dict[str, Adapter] = {
    "apitcg": adapt_apitcg,
    "community_sheet": adapt_community_sheet,
    "riftbound_tools": adapt_riftbound_tools,
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


# Fields the engine cannot simulate a card without, per card type. Type-aware
# on purpose: a spell has no Might and a Colorless card has no domain, so a
# flat required-field list reports correct data as missing and buries the real
# gaps. `power` is required on playable cards because Riftbound costs are an
# (energy, power) pair, not a single scalar -- see RULES_SUMMARY.md.
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "unit": ("energy", "power", "might"),
    "spell": ("energy", "power"),
    "gear": ("energy", "power"),
    "battlefield": (),
    "rune": (),
    "legend": (),
    "token": (),
}
DEFAULT_REQUIRED: tuple[str, ...] = ("energy", "power")


def required_fields(card: "CanonicalCard") -> tuple[str, ...]:
    return REQUIRED_BY_TYPE.get((card.type or "").lower(), DEFAULT_REQUIRED)


def is_complete(card: "CanonicalCard") -> bool:
    return all(not _is_empty(getattr(card, f)) for f in required_fields(card))


def render_coverage(merged: dict[str, CanonicalCard]) -> list[str]:
    """Which cards are actually simulatable, broken down by set.

    This is the operationally important number: a card missing its costs
    cannot be played by the engine at all, regardless of how well its text is
    understood.
    """
    by_set: dict[str, list[CanonicalCard]] = defaultdict(list)
    for card in merged.values():
        by_set[card.set or "?"].append(card)

    lines = [
        "## Simulation coverage",
        "",
        "A card is *complete* when it has the fields the engine cannot run "
        "without, which depend on its type:",
        "",
    ]
    lines += [
        f"- `{t}`: {', '.join(f'`{f}`' for f in fields) if fields else '(none)'}"
        for t, fields in sorted(REQUIRED_BY_TYPE.items())
    ]
    lines += [
        "",
        "| set | cards | complete | incomplete |",
        "| --- | --- | --- | --- |",
    ]
    for set_code, cards in sorted(by_set.items()):
        complete = sum(1 for c in cards if is_complete(c))
        lines.append(
            f"| {set_code} | {len(cards)} | {complete} | {len(cards) - complete} |"
        )
    total = len(merged)
    complete_total = sum(1 for c in merged.values() if is_complete(c))
    lines += [
        f"| **all** | **{total}** | **{complete_total}** | "
        f"**{total - complete_total}** |",
        "",
    ]

    incomplete = sorted(
        (c for c in merged.values() if not is_complete(c)),
        key=lambda c: c.riftbound_id,
    )
    if incomplete:
        gaps: dict[str, int] = defaultdict(int)
        for card in incomplete:
            for f in required_fields(card):
                if _is_empty(getattr(card, f)):
                    gaps[f] += 1
        lines += ["Missing field counts across incomplete cards:", ""]
        lines += [f"- `{f}`: {n}" for f, n in sorted(gaps.items(), key=lambda kv: -kv[1])]
        lines.append("")
    return lines


def render_discrepancies(
    discrepancies: list[Discrepancy],
    totals: dict[str, int],
    merged: dict[str, CanonicalCard] | None = None,
) -> str:
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
    lines.append("")
    if merged is not None:
        lines += render_coverage(merged)
    lines += [f"## Disagreements: {len(discrepancies)}", ""]

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
    DISCREPANCIES_MD.write_text(render_discrepancies(discrepancies, totals, merged))

    print(f"{len(merged)} canonical cards -> {CARDS_JSON.name}")
    print(f"{len(discrepancies)} field disagreements -> {DISCREPANCIES_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
