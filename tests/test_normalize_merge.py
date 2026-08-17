"""Tests for the source-merge logic in data/normalize.py.

The merge is deliberately shape-agnostic, so it is fully testable with
synthetic records before any real payload exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

from normalize import CanonicalCard, merge, render_discrepancies  # noqa: E402


def card(**kw) -> CanonicalCard:
    base = {"riftbound_id": "OGN-001", "name": "Test Card", "type": "unit"}
    base.update(kw)
    return CanonicalCard(**base)


def test_single_source_passes_through():
    merged, disc = merge({"riftcodex": [card(cost=3, might=4)]})
    assert disc == []
    assert merged["OGN-001"].cost == 3
    assert merged["OGN-001"].might == 4
    assert merged["OGN-001"].provenance["cost"] == "riftcodex"


def test_api_wins_over_sheet_and_conflict_is_logged():
    merged, disc = merge(
        {
            "community_sheet": [card(cost=5)],
            "riftcodex": [card(cost=3)],
        }
    )
    assert merged["OGN-001"].cost == 3
    assert merged["OGN-001"].provenance["cost"] == "riftcodex"

    (d,) = [x for x in disc if x.field_name == "cost"]
    assert d.chosen == 3
    assert d.chosen_from == "riftcodex"
    assert d.values == {"riftcodex": 3, "community_sheet": 5}


def test_riftcodex_outranks_riftscribe():
    merged, _ = merge(
        {"riftscribe": [card(might=9)], "riftcodex": [card(might=2)]}
    )
    assert merged["OGN-001"].might == 2


def test_agreement_is_not_a_discrepancy():
    _, disc = merge(
        {"riftcodex": [card(cost=3)], "community_sheet": [card(cost=3)]}
    )
    assert disc == []


def test_missing_field_is_filled_from_lower_precedence_source():
    """Omission is not disagreement: the sheet fills a gap without conflict."""
    merged, disc = merge(
        {
            "riftcodex": [card(cost=3)],
            "community_sheet": [card(cost=3, rules_text="Deal 2 damage.")],
        }
    )
    assert merged["OGN-001"].rules_text == "Deal 2 damage."
    assert merged["OGN-001"].provenance["rules_text"] == "community_sheet"
    assert disc == []


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_values_never_count_as_conflicts(empty):
    _, disc = merge(
        {
            "riftcodex": [card(keywords=["Tank"])],
            "community_sheet": [CanonicalCard(
                riftbound_id="OGN-001", name="Test Card", type="unit", keywords=empty or []
            )],
        }
    )
    assert [d for d in disc if d.field_name == "keywords"] == []


def test_list_fields_compare_by_value():
    merged, disc = merge(
        {
            "riftcodex": [card(keywords=["Tank", "Shield"])],
            "community_sheet": [card(keywords=["Tank"])],
        }
    )
    assert merged["OGN-001"].keywords == ["Tank", "Shield"]
    assert any(d.field_name == "keywords" for d in disc)


def test_cards_unique_to_one_source_are_kept():
    merged, _ = merge(
        {
            "riftcodex": [card()],
            "community_sheet": [card(riftbound_id="OGN-002", name="Other")],
        }
    )
    assert set(merged) == {"OGN-001", "OGN-002"}


def test_unranked_source_loses_to_ranked_one():
    merged, _ = merge(
        {"some_future_source": [card(cost=7)], "community_sheet": [card(cost=5)]}
    )
    assert merged["OGN-001"].cost == 5


def test_render_discrepancies_is_deterministic():
    _, disc = merge(
        {"riftcodex": [card(cost=3)], "community_sheet": [card(cost=5)]}
    )
    totals = {"riftcodex": 1, "community_sheet": 1}
    assert render_discrepancies(disc, totals) == render_discrepancies(disc, totals)


def test_render_discrepancies_handles_empty():
    out = render_discrepancies([], {"riftcodex": 12})
    assert "Disagreements: 0" in out
    assert "None." in out
