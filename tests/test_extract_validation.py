"""The extraction validator: the gate that keeps model output out of the engine.

An LLM writing card scripts is only safe if invented primitives fail loudly.
These tests run without an API key -- they exercise the schema and the
validator, not the model.
"""

from __future__ import annotations

import json

import pytest

from cards.database import load as load_db
from cards.dsl import TriggerKind
from cards.extract import (
    COST_KINDS,
    EFFECT_KINDS,
    GENERATED,
    SCHEMA,
    SYSTEM,
    ValidationError,
    build_prompt,
    provenance,
    validate,
    write,
)
from cards.keywords import ALL_KEYWORDS

DB = load_db()
CARD = DB["OGN-003"]  # Chemtech Enforcer: ASSAULT + "When you play me, discard 1."


def good(**over) -> dict:
    payload = {
        "card_id": CARD.card_id,
        "complete": True,
        "note": "",
        "keywords": [{"name": "Assault", "value": 2}],
        "abilities": [
            {
                "kind": "on_play",
                "text": "When you play me, discard 1.",
                "costs": [],
                "effects": [
                    {
                        "kind": "discard", "amount": None, "count": 1, "who": "you",
                        "duration": None, "keyword": None, "domain": None, "take": None,
                        "selector": {"scope": "self", "type": "any", "controller": "any",
                                     "location": "any", "max_might": None,
                                     "each_player": False},
                    }
                ],
            }
        ],
    }
    payload.update(over)
    return payload


# --- the vocabulary is closed ----------------------------------------------


def test_a_wellformed_extraction_passes():
    assert validate(good(), CARD)["card_id"] == CARD.card_id


def test_an_invented_effect_is_rejected():
    """The whole point: a hallucinated primitive must not reach the engine."""
    payload = good()
    payload["abilities"][0]["effects"][0]["kind"] = "mill_opponent_library"
    with pytest.raises(ValidationError, match="unknown effect"):
        validate(payload, CARD)


def test_an_invented_keyword_is_rejected():
    with pytest.raises(ValidationError, match="unknown keyword"):
        validate(good(keywords=[{"name": "Flying", "value": None}]), CARD)


def test_an_invented_trigger_is_rejected():
    payload = good()
    payload["abilities"][0]["kind"] = "on_upkeep"
    with pytest.raises(ValidationError, match="unknown trigger"):
        validate(payload, CARD)


def test_an_invented_cost_is_rejected():
    payload = good()
    payload["abilities"][0]["costs"] = [
        {"kind": "sacrifice_a_planet", "count": 1, "domain": None}
    ]
    with pytest.raises(ValidationError, match="unknown cost"):
        validate(payload, CARD)


def test_a_bad_selector_scope_is_rejected():
    payload = good()
    payload["abilities"][0]["effects"][0]["selector"]["scope"] = "everything_everywhere"
    with pytest.raises(ValidationError, match="selector scope"):
        validate(payload, CARD)


def test_a_mismatched_card_id_is_rejected():
    """Guards against a batch result being written to the wrong card."""
    with pytest.raises(ValidationError, match="card_id mismatch"):
        validate(good(card_id="OGN-999"), CARD)


def test_an_incomplete_extraction_must_explain_itself():
    """`complete: false` with no note would hide the gap."""
    with pytest.raises(ValidationError, match="explain itself"):
        validate(good(complete=False, note="  "), CARD)
    validate(good(complete=False, note="REPEAT cost not representable"), CARD)


# --- schema and prompt ------------------------------------------------------


def test_schema_enums_match_the_dsl():
    """The schema constrains the model; drift from the DSL would let it invent."""
    ability = SCHEMA["properties"]["abilities"]["items"]["properties"]
    assert set(ability["kind"]["enum"]) == {k.value for k in TriggerKind}
    effect = ability["effects"]["items"]["properties"]
    assert set(effect["kind"]["enum"]) == set(EFFECT_KINDS)
    cost = ability["costs"]["items"]["properties"]
    assert set(cost["kind"]["enum"]) == set(COST_KINDS)
    assert set(SCHEMA["properties"]["keywords"]["items"]["properties"]["name"]["enum"]) == set(ALL_KEYWORDS)


def test_schema_forbids_extra_properties_everywhere():
    """`additionalProperties: false` is what makes `strict: true` meaningful."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node.get("properties", {}).keys()
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(SCHEMA)


def test_the_prompt_states_the_rules_the_engine_relies_on():
    for phrase in ("EXHAUSTED", "ACCELERATE", "REACTION", "first person"):
        assert phrase in SYSTEM


def test_the_prompt_carries_the_cards_real_stats():
    prompt = build_prompt(CARD)
    assert CARD.name in prompt
    assert CARD.rules_text.strip() in prompt
    assert str(CARD.energy) in prompt


# --- provenance -------------------------------------------------------------


def test_provenance_records_the_text_it_was_derived_from():
    """If the card text is errata'd, the hash no longer matches and the
    extraction is known to be stale."""
    meta = provenance(CARD, "claude-opus-5")
    assert meta["model"] == "claude-opus-5"
    assert len(meta["card_text_sha256"]) == 64
    assert meta["reviewed"] is False and meta["tested"] is False


def test_written_files_are_not_loaded_by_the_engine(tmp_path, monkeypatch):
    """Generated scripts are candidates; the registry must ignore them."""
    from cards.scripts import registry

    monkeypatch.setattr("cards.extract.GENERATED", tmp_path)
    path = write(good(), CARD, "claude-opus-5")
    assert json.loads(path.read_text())["_provenance"]["reviewed"] is False
    # The live registry is hand-written and unaffected by anything generated.
    assert registry()[CARD.card_id].abilities, "hand-written script still in force"


def test_generated_directory_is_not_imported_anywhere():
    import cards.scripts as scripts

    source = (GENERATED.parent / "__init__.py").read_text()
    assert "generated" not in source, "generated scripts must not auto-load"
    assert scripts.registry(), "the hand-written registry is what ships"
