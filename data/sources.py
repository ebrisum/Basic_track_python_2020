"""Registry of the upstream card-data and rules sources.

Single source of truth for *where* data comes from. Nothing here fetches;
`fetch.py` does that and caches into `data/raw/`. Simulation code must never
touch the network -- it reads only `data/cards.json`.

Precedence for conflict resolution (highest first) is the order of
`PRECEDENCE` below: APIs beat the community spreadsheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The community "Riftbound - All Card Info" spreadsheet.
SHEET_ID = "1Bq0FW3zV0etBjJw-mQ8JcSB2XX4naOmhqKyixGpyswY"

# Only the gid supplied in the project brief is known. The remaining tabs must
# be enumerated from the live document (see fetch.py --enumerate-tabs) rather
# than guessed; guessed gids silently return the wrong tab.
SHEET_KNOWN_GIDS: dict[str, str] = {
    "all_card_info": "530194913",
}


def sheet_csv_url(gid: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/export?format=csv&gid={gid}"
    )


def sheet_html_url() -> str:
    """Full HTML of the doc -- used to enumerate tab names and gids."""
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/htmlview"


@dataclass(frozen=True)
class Source:
    """One upstream data source."""

    key: str
    kind: str  # "api" | "sheet" | "rules"
    base: str
    notes: str
    # Endpoint path -> filename under data/raw/<key>/. Paths are relative to
    # `base`. Left empty where the real endpoint list has not been observed.
    endpoints: dict[str, str] = field(default_factory=dict)


RIFTCODEX = Source(
    key="riftcodex",
    kind="api",
    base="https://api.riftcodex.com",
    notes=(
        "Public REST, no auth. Primary source for cards, sets, and full-text "
        "search over rules text. Endpoint list below is UNVERIFIED -- it has "
        "never been reached from this environment (egress blocked). Confirm "
        "against the live service before trusting it."
    ),
    endpoints={},
)

RIFTSCRIBE = Source(
    key="riftscribe",
    kind="api",
    base="https://riftscribe.gg",
    notes=(
        "Public JSON, no auth. Used as a cross-check against Riftcodex, not "
        "as a primary. Endpoint list UNVERIFIED for the same reason."
    ),
    endpoints={},
)

COMMUNITY_SHEET = Source(
    key="community_sheet",
    kind="sheet",
    base=sheet_csv_url(SHEET_KNOWN_GIDS["all_card_info"]),
    notes=(
        "Community-maintained. Lowest precedence: human-entered, lags errata. "
        "Valuable mainly for fields the APIs omit."
    ),
)

CORE_RULES = Source(
    key="core_rules",
    kind="rules",
    # Located via web search, NOT yet retrieved or verified.
    base="https://riftbound.gg/wp-content/uploads/sites/67/2025/12/Riftbound-Core-Rules-March-30-2026.pdf",
    notes=(
        "Official comprehensive/core rules PDF. Authority for anything card "
        "text does not state; every rule in engine/ cites it. The URL above "
        "came from a search-result listing and has not been fetched -- treat "
        "the version date as unconfirmed until the PDF is in data/raw/."
    ),
)

ALL_SOURCES: tuple[Source, ...] = (
    RIFTCODEX,
    RIFTSCRIBE,
    COMMUNITY_SHEET,
    CORE_RULES,
)

# Card-data sources only, highest precedence first. Used by normalize.py to
# resolve field disagreements.
PRECEDENCE: tuple[str, ...] = ("riftcodex", "riftscribe", "community_sheet")
