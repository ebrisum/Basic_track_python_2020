"""Adapter tests against the real cached payloads in data/raw/.

These are regression guards on the data pipeline: they pin the findings that
justified how the adapters were written, so a future change that quietly
re-enables a bad field mapping fails here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))

from normalize import (  # noqa: E402
    CARDS_JSON,
    RAW,
    adapt_apitcg,
    adapt_riftbound_tools,
    canonical_id,
    is_complete,
    _strip_html,
)

pytestmark = pytest.mark.skipif(
    not (RAW / "apitcg").exists() or not CARDS_JSON.exists(),
    reason="data/raw not populated; run data/fetch.py then data/normalize.py",
)


# --- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    "set_code,number,expected",
    [
        ("OGN", "001/298", "OGN-001"),
        ("OGN", "1", "OGN-001"),
        ("SFD", "045/315", "SFD-045"),
        ("OGN", "007a", "OGN-007A"),
        ("OGN", "", None),
        ("OGN", "Booster", None),
    ],
)
def test_canonical_id(set_code, number, expected):
    assert canonical_id(set_code, number) == expected


def test_strip_html_flattens_apitcg_markup():
    raw = "Deal 1 damage.<br>\r\n<em>(Reminder text.)</em>"
    assert _strip_html(raw) == "Deal 1 damage.\n(Reminder text.)"


# --- adapters against real bytes -------------------------------------------


def test_apitcg_adapter_drops_sealed_products():
    cards = adapt_apitcg(RAW / "apitcg")
    names = {c.name for c in cards}
    assert not any("Booster" in n or "Display" in n for n in names)
    assert len(cards) > 500


def test_apitcg_adapter_supplies_the_costs_the_engine_needs():
    cards = {c.riftbound_id: c for c in adapt_apitcg(RAW / "apitcg")}
    scorcher = cards["OGN-001"]
    assert scorcher.name == "Blazing Scorcher"
    assert scorcher.type == "unit"
    assert scorcher.energy == 5
    assert scorcher.power == 0
    assert scorcher.might == 5


def test_apitcg_adapter_flags_champions():
    cards = {c.riftbound_id: c for c in adapt_apitcg(RAW / "apitcg")}
    assert cards["OGN-027"].is_champion is True
    assert cards["OGN-001"].is_champion is False


def test_riftbound_tools_adapter_maps_no_numerics():
    """The regression guard for the 0.6%-agreement finding.

    This source's cost/energy/might are a different quantity wearing the name.
    If someone re-enables that mapping, this fails.
    """
    cards = adapt_riftbound_tools(RAW / "riftbound_tools")
    assert cards
    assert all(c.cost is None for c in cards)
    assert all(c.energy is None for c in cards)
    assert all(c.might is None for c in cards)
    assert all(c.power is None for c in cards)


def test_riftbound_tools_adapter_still_supplies_keywords_and_coverage():
    cards = adapt_riftbound_tools(RAW / "riftbound_tools")
    assert any(c.keywords for c in cards)
    assert any(c.set == "UNL" for c in cards), "sole source for the UNL set"


def test_riftbound_tools_has_no_opinion_on_champions():
    cards = adapt_riftbound_tools(RAW / "riftbound_tools")
    assert all(c.is_champion is None for c in cards), (
        "must be None (no opinion), not False (a claim) -- otherwise every "
        "champion becomes a phantom conflict"
    )


# --- the generated artefact ------------------------------------------------


def test_cards_json_is_keyed_by_canonical_id():
    cards = json.loads(CARDS_JSON.read_text())
    assert len(cards) > 800
    for card_id, card in cards.items():
        assert card_id == card["riftbound_id"]


def test_every_card_records_provenance_for_the_fields_it_has():
    cards = json.loads(CARDS_JSON.read_text())
    for card in cards.values():
        if card["energy"] is not None:
            assert card["provenance"].get("energy"), card["riftbound_id"]


def test_base_set_is_essentially_complete():
    """OGN/OGS are where Milestone 1's decks are expected to come from."""
    cards = json.loads(CARDS_JSON.read_text())
    from normalize import CanonicalCard

    base = [
        CanonicalCard(**{k: v for k, v in c.items()})
        for c in cards.values()
        if c["set"] in ("OGN", "OGS")
    ]
    complete = sum(1 for c in base if is_complete(c))
    assert complete / len(base) > 0.98, f"only {complete}/{len(base)} complete"
