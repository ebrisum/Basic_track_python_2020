"""Provenance stamps: what produced this data, and is it safe to mix?

`TCG_AI_BUILD.md` section 37 asks that every dataset and replay record the
game, card-pool, rules, observation-schema and action-schema versions, so that
"training data generated under incompatible rule versions must not silently
mix."

The word carrying the weight is **silently**. A version constant that has to
be remembered and bumped by hand will eventually not be, and the failure is
invisible: two datasets merge, one of them was generated under a different
card cost, and the model learns the average of two games.

So the schema versions here are **derived, not declared**. They are digests
over the dataclasses they describe. Add a field to the observation and
`observation_schema_version` moves whether anyone thought about it or not.
That is not a nicety: this project's replay hashes moved twice under
justified representation changes, and both times distinguishing "the
representation changed" from "the rules regressed" was hand work.

Two things are deliberately *not* in the digests:

* **Presentation.** Image URLs and alt text are excluded from the card-pool
  version. A dataset is not incompatible because a picture moved.
* **Card scripts.** They live in `cards/scripts/` and change constantly while
  the card pool is being filled in. Folding them in would invalidate every
  replay on every script added, which would train everyone to ignore the
  stamp. `ENGINE_VERSION` is the manual dial for behaviour changes that
  matter; scripts land under it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

DIGEST_LEN = 12

# Bumped by hand when engine *behaviour* changes in a way that makes older
# generated data unsafe to mix -- a rules fix, a new Game Action, a scripted
# card that changes how positions are reached. Not bumped for refactors.
ENGINE_VERSION = "1.0.0"

# The rules text every citation in this repo points at.
RULES_VERSION = "CR-v1.4-Vendetta"

RULES_PATH = Path(__file__).resolve().parents[1] / "data/raw/core_rules/CR-v1.4.txt"

# Fields of a card that determine how it plays. Everything else is
# presentation and does not make two datasets incompatible.
GAMEPLAY_FIELDS = (
    "card_id", "type", "energy", "power", "might", "domains",
    "keywords", "rules_text", "is_champion", "set",
)


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode())
        h.update(b"\x1f")
    return h.hexdigest()[:DIGEST_LEN]


def _fields_of(cls) -> str:
    """A stable rendering of a dataclass's field names.

    Names only, not types: a type annotation tightening from `int` to
    `int | None` does not change what a consumer must parse, and churn in the
    stamp is what teaches people to ignore it.
    """
    return f"{cls.__name__}({','.join(sorted(cls.__dataclass_fields__))})"


def rules_version() -> str:
    """The rules identifier, plus a digest of the cached text if present.

    The identifier alone would not notice a swapped or truncated rules file.
    Since every rule citation in this repo is checked against that file, a
    silent change to it is exactly the kind of thing worth catching.
    """
    if not RULES_PATH.exists():
        return RULES_VERSION
    return f"{RULES_VERSION}+{_digest(RULES_PATH.read_text(errors='replace'))}"


def card_pool_version(db) -> str:
    """A digest over every card's gameplay-relevant fields.

    Sorted by card id, so it does not depend on load order.
    """
    parts: list[str] = []
    for card_id in sorted(db.cards):
        card = db.cards[card_id]
        for name in GAMEPLAY_FIELDS:
            value = getattr(card, name, None)
            if isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)
            parts.append(f"{name}={value}")
    return _digest(*parts)


def observation_schema_version() -> str:
    """A digest over the shape of everything an agent is handed."""
    from engine.observation import (
        RiftboundObservation,
        VisibleBattlefield,
        VisibleCard,
    )

    return _digest(
        _fields_of(RiftboundObservation),
        _fields_of(VisibleCard),
        _fields_of(VisibleBattlefield),
    )


def action_schema_version() -> str:
    """A digest over every action class and its fields.

    Replays store actions by `repr()`, so an action gaining a field or being
    renamed invalidates every replay containing it. This makes that visible
    instead of leaving it to be discovered at the first mismatched hash.
    """
    from engine import actions

    parts = []
    for name in sorted(dir(actions)):
        obj = getattr(actions, name)
        if isinstance(obj, type) and hasattr(obj, "__dataclass_fields__"):
            if issubclass(obj, actions.Action):
                parts.append(_fields_of(obj))
    return _digest(*parts)


def provenance(db) -> dict[str, str]:
    """The full stamp, for a replay, a dataset, or a metrics run."""
    return {
        "engine_version": ENGINE_VERSION,
        "rules_version": rules_version(),
        "card_pool_version": card_pool_version(db),
        "observation_schema_version": observation_schema_version(),
        "action_schema_version": action_schema_version(),
    }
