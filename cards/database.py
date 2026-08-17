"""Card database: loads data/cards.json and answers questions about cards.

This is the only place that reads `cards.json`. `engine/` imports the
`CardData` shape but never the file, keeping the rules layer ignorant of where
card data comes from.

Cards whose rules text has not been scripted are still *playable* -- their
stats, costs and types are real -- but their text does nothing. That is a
visible approximation, not a hidden one: `CardData.text_implemented` says so
and the frontend surfaces it on every affected card.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from cards import keywords as kw

CARDS_JSON = Path(__file__).resolve().parents[1] / "data" / "cards.json"

# Card types that can be in a Main Deck (103.2).
MAIN_DECK_TYPES = frozenset({"unit", "spell", "gear"})


@dataclass(frozen=True)
class CardData:
    """Immutable card definition. Shared by every instance of that card."""

    card_id: str
    name: str
    type: str
    energy: int
    power: int
    might: int
    domains: tuple[str, ...]
    keywords: tuple[str, ...]
    rules_text: str
    is_champion: bool
    set: str
    image_url: str = ""
    image_alt: str = ""
    # Keywords parsed from the printed text (804-829).
    parsed_keywords: tuple = ()

    @property
    def has_text(self) -> bool:
        return bool(self.rules_text.strip())

    @property
    def assault(self) -> int:
        """807 -- +X Might while an attacker."""
        return kw.value_of(self.parsed_keywords, "Assault")

    @property
    def has_tank(self) -> bool:
        return kw.has(self.parsed_keywords, "Tank")  # 815

    @property
    def has_backline(self) -> bool:
        return kw.has(self.parsed_keywords, "Backline")  # 826

    @property
    def has_ganking(self) -> bool:
        return kw.has(self.parsed_keywords, "Ganking")  # 810

    @property
    def has_action(self) -> bool:
        """806 Action -- playable on your turn or in showdowns (308.1.a)."""
        return kw.has(self.parsed_keywords, "Action")

    @property
    def has_reaction(self) -> bool:
        """813 Reaction -- playable any time, including a Closed state (309.1.a)."""
        return kw.has(self.parsed_keywords, "Reaction")

    @property
    def unimplemented_keywords(self) -> tuple[str, ...]:
        return kw.unimplemented(self.parsed_keywords)

    @property
    def text_implemented(self) -> bool:
        """Whether this card's rules text is fully executed by the engine.

        True when the card is vanilla, when its whole text is keywords the
        engine implements, or when a card script covers it (`complete=True`).
        Anything else is still partly inert -- visibly so: the UI marks it.
        See RQ-5 in RULES_QUESTIONS.md.
        """
        if not self.has_text:
            return True

        # A script that declares itself complete covers the printed text.
        from cards.scripts import script_for

        script = script_for(self.card_id)
        if script is not None and script.complete and script.abilities:
            return True

        residue = kw.strip_reminders(self.rules_text)
        for keyword in self.parsed_keywords:
            residue = residue.replace(keyword.name.upper(), " ")
            residue = residue.replace(keyword.name, " ")
            if keyword.value is not None:
                residue = residue.replace(str(keyword.value), " ")
        if residue.strip():
            return False
        return not self.unimplemented_keywords

    @property
    def script_note(self) -> str:
        """Why a card is only partly implemented, for the UI tooltip."""
        from cards.scripts import script_for

        script = script_for(self.card_id)
        return script.note if script is not None else ""

    @property
    def power_domains(self) -> list[str]:
        """The domains a power cost must be paid in (163.2).

        A card's power cost is paid in its own domain. Multi-domain cards are
        assumed to split evenly across their domains in listed order; single
        and colourless cases -- which are the overwhelming majority -- are
        exact. See RQ-6.
        """
        if self.power <= 0:
            return []
        if not self.domains:
            return ["Universal"] * self.power
        out = []
        for i in range(self.power):
            out.append(self.domains[i % len(self.domains)])
        return out


@dataclass
class CardDatabase:
    """Immutable, shared card definitions.

    `CardData` is frozen and nothing mutates this after load, so a deep copy is
    pure waste. That matters a lot: search clones the game state once per
    candidate action, and copying 908 cards each time dominated the cost --
    16.1 ms per clone before this, and the database was almost all of it.
    """

    cards: dict[str, CardData] = field(default_factory=dict)

    def __deepcopy__(self, memo):
        # Shared by reference on purpose; see the class docstring.
        return self

    def __copy__(self):
        return self

    def __getitem__(self, card_id: str) -> CardData:
        return self.cards[card_id]

    def get(self, card_id: str) -> CardData | None:
        return self.cards.get(card_id)

    def __contains__(self, card_id: str) -> bool:
        return card_id in self.cards

    def __len__(self) -> int:
        return len(self.cards)

    def playable(self) -> list[CardData]:
        """Cards complete enough for the engine to use."""
        return [c for c in self.cards.values() if c.type in MAIN_DECK_TYPES]

    def battlefields(self) -> list[CardData]:
        return [c for c in self.cards.values() if c.type == "battlefield"]

    def legends(self) -> list[CardData]:
        return [c for c in self.cards.values() if c.type == "legend"]


def _coerce(raw: dict) -> CardData | None:
    """Build a CardData, or None if the record is too incomplete to simulate."""
    card_type = (raw.get("type") or "").lower()
    energy = raw.get("energy")
    power = raw.get("power")
    might = raw.get("might")

    # Runes, legends and battlefields have no costs; playables must have both
    # halves of an (energy, power) cost or the engine cannot price them.
    if card_type in MAIN_DECK_TYPES:
        if energy is None or power is None:
            return None
        if card_type == "unit" and might is None:
            return None

    return CardData(
        card_id=raw["riftbound_id"],
        name=raw.get("name") or raw["riftbound_id"],
        type=card_type,
        energy=int(energy or 0),
        power=int(power or 0),
        might=int(might or 0),
        domains=tuple(raw.get("domains") or ()),
        keywords=tuple(raw.get("keywords") or ()),
        rules_text=raw.get("rules_text") or "",
        is_champion=bool(raw.get("is_champion")),
        set=raw.get("set") or "",
        image_url=raw.get("image_url") or "",
        image_alt=raw.get("image_alt") or "",
        parsed_keywords=kw.parse(raw.get("rules_text") or ""),
    )


@functools.lru_cache(maxsize=1)
def load(path: str | None = None) -> CardDatabase:
    """Load and cache the card database. Pure; safe to call anywhere."""
    source = Path(path) if path else CARDS_JSON
    if not source.exists():
        raise FileNotFoundError(
            f"{source} not found -- run `python data/normalize.py` first"
        )
    raw = json.loads(source.read_text())
    db = CardDatabase()
    for record in raw.values():
        card = _coerce(record)
        if card is not None:
            db.cards[card.card_id] = card
    return db
