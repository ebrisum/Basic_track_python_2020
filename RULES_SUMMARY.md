# Rules summary

**Status: not written. Blocked on BLOCKER-1 (see `RULES_QUESTIONS.md`).**

This document is meant to be the implementation target for `engine/` — turn
structure, resource system, showdown sequence, win condition, priority, under
two pages, every claim traceable to the official comprehensive rules.

It has deliberately not been written. The rules PDF could not be retrieved
(`riftbound.gg` is refused by this environment's egress proxy), and a summary
written from model recall would have no citations behind it while reading
exactly like one that did. Since this is the document the entire engine is
implemented against, a plausible-but-wrong version here is worse than an empty
one: the error would propagate silently into every rule, every card script,
and every win rate the system reports.

## What this file needs

Source: the current official Riftbound core/comprehensive rules PDF, cached at
`data/raw/core_rules/core_rules.pdf`.

A search-result listing points to
`https://riftbound.gg/wp-content/uploads/sites/67/2025/12/Riftbound-Core-Rules-March-30-2026.pdf`
(March 30, 2026 revision). That URL is recorded in `data/sources.py` but has
**not** been fetched, so both its validity and the version date are
unconfirmed.

## Sections to write, once the PDF is in hand

Each gets a section-number citation into the rules document.

1. **Game setup** — decks (main, rune, battlefield, legend), opening hand,
   mulligan, starting resources.
2. **Win condition** — points required, how points are scored and when they
   are checked.
3. **Turn structure** — phases in order, what is mandatory vs optional in
   each, when the turn passes.
4. **Resource system** — energy, runes, rune deck mechanics, what paying a
   cost consists of, and what happens when payment cannot be completed.
5. **Priority and response windows** — who holds priority, how it passes, what
   may be played in a response window, how a window closes.
6. **Showdown sequence** — the full ordered sequence from initiation to
   resolution, with every priority window marked.
7. **Battlefields** — contesting, holding, conquering, and the exact timing at
   which each scores.
8. **Combat resolution** — damage assignment, might/power comparison,
   destruction, and death triggers.
9. **Trigger and effect resolution** — ordering of simultaneous triggers,
   replacement effects, continuous/static effect layering.
10. **Champion rules** — deployment, death, recycling, re-deployment.

Anything the rules leave genuinely ambiguous goes to `RULES_QUESTIONS.md`
rather than being silently decided here.
