"""LLM-assisted extraction of card text into DSL scripts.

    .venv/bin/python cards/extract.py --card OGN-003          # one card
    .venv/bin/python cards/extract.py --set OGN --limit 50    # a slice
    .venv/bin/python cards/extract.py --set OGN --batch       # Batches API, 50% cost

Scripting ~900 cards by hand is the bottleneck. This asks Claude to read the
printed text and emit a `CardScript` as **data**, then validates it against the
DSL vocabulary before anything is written.

## The safety property

The model never emits code. It fills a fixed JSON schema whose enums are the
DSL primitives from `cards/dsl.py`, and `validate()` rejects anything outside
them. A hallucinated primitive fails loudly instead of executing. This is the
brief's "no escape hatch that executes arbitrary Python per card", preserved
even though a model is now writing the scripts.

## Output is a candidate, not a script

Extractions land in `cards/scripts/generated/` with provenance (model, prompt
version, sha256 of the card text). Nothing in `cards/scripts/__init__.py`
loads them. Promotion is a human decision, and the project rule still stands:
no card is done without a unit test asserting its effect on a constructed
state.

Requires `anthropic` (not a runtime dependency -- imported lazily) and
credentials. See `--dry-run` to inspect prompts without calling the API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cards.database import load as load_db  # noqa: E402
from cards.dsl import Duration, TriggerKind, Who  # noqa: E402
from cards.keywords import ALL_KEYWORDS, IMPLEMENTED  # noqa: E402

GENERATED = Path(__file__).resolve().parent / "scripts" / "generated"
PROMPT_VERSION = "2026-08-18.1"
DEFAULT_MODEL = "claude-opus-5"

# The primitives the interpreter implements. The schema below allows exactly
# these; anything else is a validation failure, not a new feature.
EFFECT_KINDS: tuple[str, ...] = (
    "draw", "discard", "deal", "kill", "buff", "grant_keyword",
    "add_energy", "add_power", "return_to_hand", "look_at_top",
    "attach", "exhaust", "ready", "gain_points", "units_enter_ready",
)
COST_KINDS: tuple[str, ...] = (
    "exhaust_self", "discard", "recycle_from_trash", "pay_power", "pay_energy",
)
DOMAINS: tuple[str, ...] = ("Fury", "Calm", "Mind", "Body", "Chaos", "Order", "Universal")


def _selector_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope": {"type": "string", "enum": ["self", "choose", "all"]},
            "type": {"type": "string", "enum": ["unit", "spell", "gear", "any"]},
            "controller": {"type": "string", "enum": ["any", "friendly", "enemy"]},
            "location": {"type": "string", "enum": ["any", "battlefield", "base"]},
            "max_might": {"type": ["integer", "null"]},
            "each_player": {"type": "boolean"},
        },
        "required": ["scope", "type", "controller", "location", "max_might", "each_player"],
    }


SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "card_id": {"type": "string"},
        "complete": {
            "type": "boolean",
            "description": "True only if every sentence of the printed text is "
                           "expressed by the abilities below. False if any part "
                           "is not representable.",
        },
        "note": {
            "type": "string",
            "description": "If complete is false, say exactly which clause is "
                           "not represented and why. Empty otherwise.",
        },
        "keywords": {
            "type": "array",
            "description": "Keywords printed on the card, with their numeric "
                           "parameter where one applies (e.g. Assault 3).",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": list(ALL_KEYWORDS)},
                    "value": {"type": ["integer", "null"]},
                },
                "required": ["name", "value"],
            },
        },
        "abilities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [k.value for k in TriggerKind],
                        "description": "on_play: 'When you play me'. on_resolve: a "
                                       "spell's own effect. activated: 'Cost: effect'. "
                                       "on_conquer / on_death: triggers.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The exact printed sentence this ability implements.",
                    },
                    "costs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {"type": "string", "enum": list(COST_KINDS)},
                                "count": {"type": "integer"},
                                "domain": {"type": ["string", "null"], "enum": list(DOMAINS) + [None]},
                            },
                            "required": ["kind", "count", "domain"],
                        },
                    },
                    "effects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {"type": "string", "enum": list(EFFECT_KINDS)},
                                "amount": {"type": ["integer", "null"]},
                                "count": {"type": ["integer", "null"]},
                                "who": {"type": ["string", "null"], "enum": [w.value for w in Who] + [None]},
                                "duration": {"type": ["string", "null"], "enum": [d.value for d in Duration] + [None]},
                                "keyword": {"type": ["string", "null"], "enum": list(ALL_KEYWORDS) + [None]},
                                "domain": {"type": ["string", "null"], "enum": list(DOMAINS) + [None]},
                                "take": {"type": ["integer", "null"]},
                                "selector": _selector_schema(),
                            },
                            "required": ["kind", "amount", "count", "who", "duration",
                                         "keyword", "domain", "take", "selector"],
                        },
                    },
                },
                "required": ["kind", "text", "costs", "effects"],
            },
        },
    },
    "required": ["card_id", "complete", "note", "keywords", "abilities"],
}

SYSTEM = f"""You convert Riftbound TCG card text into a fixed data structure.

You are transcribing, not designing. Express only what the card prints. If a
clause cannot be expressed with the available primitives, set `complete` to
false and name the clause in `note` — never approximate it with a different
primitive, and never invent one.

Rules that matter for this game (Core Rules v1.4):

- Units enter the board EXHAUSTED (143.4). ACCELERATE (805) is an optional
  additional cost of 1 energy + 1 power of the unit's domain; paying it makes
  the unit enter ready. Emit ACCELERATE as a keyword, not as effects — the
  engine implements it.
- ACTION (806) and REACTION (813) are timing permissions, not effects. Emit
  them as keywords. A REACTION can be played while another item is on the
  chain, including in answer to another REACTION.
- Assault, Tank, Backline and Ganking are implemented by the engine
  ({', '.join(sorted(IMPLEMENTED))}). Emit them as keywords with their value.
  Other keywords are recorded but currently inert — still emit them.
- Cards refer to themselves in the first person (053): "me"/"I" on units,
  "this" on gear and spells, "here" on battlefields. Use selector scope
  "self" for those.
- "a unit" means the controller chooses one — scope "choose". "all units"
  means every match — scope "all". "Each player ..." sets each_player true.
- Reminder text in parentheses restates a keyword. Do not emit effects for it.
- Damage is dealt, not subtracted from Might. Lethal damage is damage >= Might.

Set `complete` honestly. A script that claims to cover text it does not is
worse than one that admits the gap."""


def build_prompt(card) -> str:
    return (
        f"Card: {card.name}\n"
        f"ID: {card.card_id}\n"
        f"Type: {card.type}\n"
        f"Cost: {card.energy} energy, {card.power} power\n"
        f"Might: {card.might}\n"
        f"Domains: {', '.join(card.domains) or 'none (colorless)'}\n\n"
        f"Printed text:\n{card.rules_text}\n"
    )


class ValidationError(ValueError):
    """The model returned something outside the DSL vocabulary."""


def validate(payload: dict, card) -> dict:
    """Reject anything the interpreter could not execute.

    This is the gate that keeps a hallucinated primitive from reaching the
    engine. It duplicates the schema's enums on purpose: the schema constrains
    the model, this constrains the file on disk, and only the second one is
    still true after someone hand-edits a generated script.
    """
    if payload.get("card_id") != card.card_id:
        raise ValidationError(f"card_id mismatch: {payload.get('card_id')} != {card.card_id}")
    if not payload.get("complete") and not (payload.get("note") or "").strip():
        raise ValidationError("incomplete extraction must explain itself in `note`")

    for keyword in payload.get("keywords", []):
        if keyword["name"] not in ALL_KEYWORDS:
            raise ValidationError(f"unknown keyword {keyword['name']!r}")

    valid_kinds = {k.value for k in TriggerKind}
    for ability in payload.get("abilities", []):
        if ability["kind"] not in valid_kinds:
            raise ValidationError(f"unknown trigger {ability['kind']!r}")
        for cost in ability.get("costs", []):
            if cost["kind"] not in COST_KINDS:
                raise ValidationError(f"unknown cost {cost['kind']!r}")
        for effect in ability.get("effects", []):
            if effect["kind"] not in EFFECT_KINDS:
                raise ValidationError(f"unknown effect {effect['kind']!r}")
            selector = effect.get("selector") or {}
            if selector and selector.get("scope") not in ("self", "choose", "all"):
                raise ValidationError(f"bad selector scope {selector.get('scope')!r}")
    return payload


def provenance(card, model: str) -> dict:
    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "card_text_sha256": hashlib.sha256(card.rules_text.encode()).hexdigest(),
        "reviewed": False,
        "tested": False,
    }


def write(payload: dict, card, model: str) -> Path:
    GENERATED.mkdir(parents=True, exist_ok=True)
    path = GENERATED / f"{card.card_id}.json"
    path.write_text(
        json.dumps({"_provenance": provenance(card, model), **payload}, indent=2) + "\n"
    )
    return path


TOOL = {
    "name": "emit_card_script",
    "description": "Record the card's printed text as DSL data.",
    "input_schema": SCHEMA,
    "strict": True,
}


def extract_one(client, card, model: str) -> dict:
    """One card, one request. Uses strict tool use so the shape is guaranteed."""
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "emit_card_script"},
        messages=[{"role": "user", "content": build_prompt(card)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return validate(dict(block.input), card)
    raise ValidationError(f"{card.card_id}: model returned no tool call")


def submit_batch(client, cards, model: str):
    """Batches API -- half price, which matters across ~900 cards."""
    from anthropic.types.messages.batch_create_params import Request
    from anthropic.types import MessageCreateParamsNonStreaming

    requests = [
        Request(
            custom_id=card.card_id,
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=16000,
                system=SYSTEM,
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "emit_card_script"},
                messages=[{"role": "user", "content": build_prompt(card)}],
            ),
        )
        for card in cards
    ]
    return client.messages.batches.create(requests=requests)


def select(db, args) -> list:
    cards = [
        c for c in db.cards.values()
        if c.type in ("unit", "spell", "gear") and c.rules_text.strip()
    ]
    if args.card:
        cards = [c for c in cards if c.card_id in set(args.card)]
    if args.set:
        cards = [c for c in cards if c.set == args.set.upper()]
    cards.sort(key=lambda c: c.card_id)
    return cards[: args.limit] if args.limit else cards


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--card", action="append", default=[], help="card id (repeatable)")
    ap.add_argument("--set", default=None, help="restrict to a set code, e.g. OGN")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"default {DEFAULT_MODEL}; a smaller model such as "
                         f"claude-haiku-4-5 costs less across the full pool")
    ap.add_argument("--batch", action="store_true", help="submit via the Batches API (50%% cost)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print prompts and exit; makes no API call")
    args = ap.parse_args(argv)

    db = load_db()
    cards = select(db, args)
    if not cards:
        print("no cards matched")
        return 1
    print(f"{len(cards)} card(s) selected; model={args.model}")

    if args.dry_run:
        print(f"\n--- system prompt ({len(SYSTEM)} chars) ---\n{SYSTEM}\n")
        print(f"--- example user prompt ---\n{build_prompt(cards[0])}")
        print(f"--- schema: {len(EFFECT_KINDS)} effect kinds, "
              f"{len(COST_KINDS)} cost kinds, {len(ALL_KEYWORDS)} keywords ---")
        return 0

    try:
        import anthropic
    except ImportError:
        print("`anthropic` is not installed: uv pip install --python .venv/bin/python anthropic")
        return 1

    client = anthropic.Anthropic()

    if args.batch:
        batch = submit_batch(client, cards, args.model)
        print(f"submitted batch {batch.id} ({len(cards)} cards)")
        print("poll: client.messages.batches.retrieve(id).processing_status")
        return 0

    written = failed = 0
    for card in cards:
        try:
            payload = extract_one(client, card, args.model)
        except ValidationError as exc:
            print(f"  REJECTED {card.card_id}: {exc}")
            failed += 1
            continue
        path = write(payload, card, args.model)
        flag = "" if payload["complete"] else "  (incomplete: " + payload["note"][:60] + ")"
        print(f"  {card.card_id} -> {path.name}{flag}")
        written += 1

    print(f"\n{written} written, {failed} rejected")
    print("Nothing is loaded by the engine until it is reviewed and given a test.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
