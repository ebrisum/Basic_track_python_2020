"""Registry of the upstream card-data and rules sources.

Single source of truth for *where* data comes from. Nothing here fetches;
`fetch.py` does that and caches into `data/raw/`. Simulation code must never
touch the network -- it reads only `data/cards.json`.

## Why these sources and not the four in the project brief

All four originally specified sources are refused by this environment's egress
proxy (403 at CONNECT): `api.riftcodex.com`, `riftscribe.gg`, `docs.google.com`,
and `riftbound.gg`. See BLOCKER-1 in `RULES_QUESTIONS.md`.

The three below are *reachable equivalents*, retrieved through hosts the proxy
does allow (the npm registry and github.com). They are recorded as substitutes,
not replacements: when the original four become reachable they should be added
back and cross-checked against these, since Riftcodex remains the higher
authority for card data.

Precedence for conflict resolution is `PRECEDENCE` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Blocked -- the brief's original four. Kept so the substitution stays visible
# and so they can be re-enabled the moment egress allows.
# --------------------------------------------------------------------------

BLOCKED_SOURCES: dict[str, str] = {
    "riftcodex": "https://api.riftcodex.com",
    "riftscribe": "https://riftscribe.gg",
    "community_sheet": (
        "https://docs.google.com/spreadsheets/d/"
        "1Bq0FW3zV0etBjJw-mQ8JcSB2XX4naOmhqKyixGpyswY"
        "/export?format=csv&gid=530194913"
    ),
    "riftbound_gg_rules": "https://riftbound.gg/rules/core-rules/",
}


@dataclass(frozen=True)
class Source:
    """One upstream data source."""

    key: str
    kind: str  # "npm" | "git" | "rules"
    url: str
    notes: str
    # npm sources: files to lift out of the tarball -> destination filename.
    # git sources: repo-relative globs to copy into data/raw/<key>/.
    paths: dict[str, str] = field(default_factory=dict)
    globs: tuple[str, ...] = ()
    version: str = ""


RIFTBOUND_TOOLS = Source(
    key="riftbound_tools",
    kind="npm",
    url="https://registry.npmjs.org/riftbound-tools",
    version="1.0.0",
    notes=(
        "npm package `riftbound-tools`, sourced from github.com/jamescer/"
        "riftbound-tools. Broadest coverage: 950 records across OGN, OGS, SFD "
        "and UNL, with keyword/tag arrays the other source lacks entirely. "
        "Carries no power cost and flattens Champion/Token typing into a bare "
        "'Unit', which is why it does not hold top precedence."
    ),
    paths={"package/dist/data/cards.json": "cards.json"},
)

APITCG = Source(
    key="apitcg",
    kind="git",
    url="https://github.com/apitcg/riftbound-tcg-data.git",
    notes=(
        "Independent community dataset behind apitcg.dev. Narrower (OGN, OGS, "
        "SFD only -- no UNL) but rules-richer: carries powerCost, and "
        "distinguishes 'Champion Unit' / 'Signature Unit' / 'Token' where the "
        "npm source says only 'Unit'. Both fields matter to the engine, so it "
        "takes precedence where the two overlap. Its card files also include "
        "sealed products (booster packs, displays); those have a null cardType "
        "and are dropped during normalization."
    ),
    globs=("cards/en/*.json", "sets/en.json"),
)

CORE_RULES = Source(
    key="core_rules",
    kind="git",
    url="https://github.com/ChristianIvicevic/riftboundfaq.git",
    notes=(
        "Mirrors the OFFICIAL Riot Core Rules and Tournament Rules PDFs under "
        "sources/, versions 1.0 through 1.4, plus a rules-manifest.json naming "
        "the current one. Also carries ~200 judge-level card rulings as MDX "
        "with Core Rules citations. This is a third-party mirror of a "
        "first-party document -- the text is authoritative, the hosting is "
        "not. Re-verify against playriftbound.com when egress permits."
    ),
    globs=("sources/*.pdf", "sources/rules-manifest.json", "content/**/*.mdx"),
)

COMMUNITY_SHEET = Source(
    key="community_sheet",
    kind="manual",
    url=(
        "https://docs.google.com/spreadsheets/d/"
        "1Bq0FW3zV0etBjJw-mQ8JcSB2XX4naOmhqKyixGpyswY"
    ),
    notes=(
        "The brief's original source #3, supplied by the repo owner as an xlsx "
        "because docs.google.com is blocked here. Cached as CSVs; `fetch.py` "
        "cannot refresh it, so it is `kind='manual'`.\n\n"
        "Its column headed 'Might' is NOT might -- it is the POWER cost, "
        "matching apitcg's powerCost on 99.3% of filled cells, and a blank "
        "means zero (98.3%). Combined that is 98.9% correct across 529 "
        "comparable cards. That also explains RQ-1: the npm source derives "
        "from this sheet, which is why its `might` matched apitcg's `power`.\n\n"
        "It is the ONLY source for the UNL and VEN sets, and the only source of "
        "power costs for them. Its `Energy` column agrees with apitcg on only "
        "~60% of shared cards, scattered at +/-1, so it sits at the bottom of "
        "PRECEDENCE and every disagreement is logged. See RQ-11."
    ),
    globs=("all_card_data.csv", "summary.csv", "card_counts.csv"),
)

ALL_SOURCES: tuple[Source, ...] = (RIFTBOUND_TOOLS, APITCG, CORE_RULES, COMMUNITY_SHEET)

# Card-data sources only, highest precedence first.
#
# apitcg outranks riftbound_tools because it carries two fields the engine
# cannot work without and the npm source simply does not have: power cost, and
# the Champion/Signature/Token distinction. Where apitcg has no opinion --
# keywords, tags, the UNL set -- the omission rule in normalize.py lets
# riftbound_tools fill the gap without either being treated as a conflict.
PRECEDENCE: tuple[str, ...] = ("apitcg", "community_sheet", "riftbound_tools")

# community_sheet is above riftbound_tools because it is verifiably right about
# power (98.9%) where the npm source has no power at all, and it is the sole
# source for UNL and VEN. It stays below apitcg because its Energy column is
# only ~60% accurate on the sets both cover.

# apitcg set id -> the three-letter set code used in canonical riftbound_ids.
# Verified by card count: origins/360, origins-proving-grounds/24,
# spiritforged/315 line up with OGN, OGS and SFD in the npm source.
APITCG_SET_CODES: dict[str, str] = {
    "origins": "OGN",
    "origins-proving-grounds": "OGS",
    "spiritforged": "SFD",
}

# The current Core Rules revision, per data/raw/core_rules/rules-manifest.json.
CORE_RULES_VERSION = "1.4"
CORE_RULES_NAME = "Vendetta"
CORE_RULES_TEXT = "CR-v1.4.txt"
