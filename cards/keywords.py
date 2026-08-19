"""Keyword parsing and the keywords the engine implements mechanically.

Keywords are printed in card text as a shouted name plus parenthetical
reminder text, optionally with a numeric parameter: `ASSAULT 3 (+3 Might while
it's an attacker.)`. Reminder text is redundant with the glossary (804-829) and
is stripped rather than interpreted, per DSL.md.

Only *mechanical* keywords are handled here -- ones that change movement,
damage assignment, or stats without needing the effect DSL. Everything else in
a card's text is still inert; see RQ-5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The full glossary (804-829), so the parser recognises every printed keyword
# even when the engine does not act on it.
ALL_KEYWORDS: tuple[str, ...] = (
    "Accelerate", "Action", "Assault", "Deathknell", "Deflect", "Ganking",
    "Hidden", "Legion", "Reaction", "Shield", "Tank", "Temporary", "Vision",
    "Equip", "Quick-Draw", "Repeat", "Weaponmaster", "Ambush", "Hunt", "Level",
    "Unique", "Backline", "Empower", "Empowered", "Flow",
)

# Keywords whose full effect the engine actually implements.
IMPLEMENTED: frozenset[str] = frozenset({
    "Assault",    # 807 -- +X Might while an attacker
    "Tank",       # 815 -- must be assigned lethal damage first
    "Backline",   # 826 -- must be assigned lethal damage last
    "Ganking",    # 810 -- standard move battlefield -> battlefield
    "Temporary",  # 816 -- dies at its controller's Beginning Phase
    "Unique",     # 825 -- one copy per deck by name (deck construction only)
    "Deflect",    # 809 -- taxes an opponent's spells that choose this
    "Weaponmaster",  # 821 -- play effect: equip an Equipment at a discount
    "Repeat",     # 820 -- optional additional cost: execute the effect twice
})

_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(ALL_KEYWORDS, key=len, reverse=True)) + r")\b(?:\s+(\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Keyword:
    name: str
    value: int | None = None

    def __repr__(self) -> str:
        return self.name if self.value is None else f"{self.name} {self.value}"


def strip_reminders(text: str) -> str:
    """Remove parenthetical reminder text (DSL.md: strip, do not interpret)."""
    return re.sub(r"\([^)]*\)", " ", text)


# Some cards print the parameter only in the reminder text -- e.g. Chemtech
# Enforcer reads "ASSAULT (+2 Might while I'm an attacker.)" rather than
# "ASSAULT 2". The rules say Assault is formatted "Assault [X]" (807.1.b), so
# the value is authoritative wherever it appears; these recover it.
_REMINDER_VALUE = {
    "Assault": re.compile(r"\+(\d+)\s*Might", re.IGNORECASE),
    "Shield": re.compile(r"Shield\s*(\d+)", re.IGNORECASE),
}


def parse(rules_text: str) -> tuple[Keyword, ...]:
    """Extract keywords printed on a card, outside reminder text.

    Deduplicates by name, keeping the highest parameter -- a card printing
    `ASSAULT 3` alongside a reminder mentioning Assault must yield one keyword.
    """
    if not rules_text:
        return ()
    body = strip_reminders(rules_text)
    found: dict[str, int | None] = {}
    for match in _PATTERN.finditer(body):
        raw_name, raw_value = match.group(1), match.group(2)
        canonical = next(k for k in ALL_KEYWORDS if k.lower() == raw_name.lower())
        value = int(raw_value) if raw_value else None
        if canonical in found:
            existing = found[canonical]
            if value is not None and (existing is None or value > existing):
                found[canonical] = value
        else:
            found[canonical] = value

    # Recover parameters printed only in the reminder.
    for name, pattern in _REMINDER_VALUE.items():
        if found.get(name, "missing") is None:
            match = pattern.search(rules_text)
            if match:
                found[name] = int(match.group(1))

    return tuple(
        Keyword(name, value) for name, value in sorted(found.items())
    )


def has(keywords: tuple[Keyword, ...], name: str) -> bool:
    return any(k.name == name for k in keywords)


def value_of(keywords: tuple[Keyword, ...], name: str, default: int = 0) -> int:
    for keyword in keywords:
        if keyword.name == name:
            return keyword.value if keyword.value is not None else default
    return 0


def unimplemented(keywords: tuple[Keyword, ...]) -> tuple[str, ...]:
    """Printed keywords the engine does not act on -- surfaced in the UI."""
    return tuple(k.name for k in keywords if k.name not in IMPLEMENTED)
