# Decisions

One line each, with reasoning. Architectural choices fixed by the project
brief are not repeated here -- only choices made on top of it.

## Session 1

- **Project lives at the repo root, not in a nested `riftbound-sim/`** -- the
  repo *is* the project; a single-child top directory buys nothing.
- **Python 3.12 via a local `.venv`** -- the container's default `python3` is
  3.11 and its 3.12 is PEP-668 externally-managed, so `uv venv --python 3.12`
  is the only way to hit the specified version with pytest available.
- **`pytest` is the only non-stdlib dependency** -- as sanctioned by the brief.
  `data/fetch.py` uses `urllib` rather than `requests` to keep it that way.
- **Fetching is split from normalizing** (`data/fetch.py` vs
  `data/normalize.py`) -- caching is a network concern with retry/provenance
  needs; normalizing is a pure function over `data/raw/`. Split means
  normalize is re-runnable offline and unit-testable.
- **Every cached file gets a `_meta.json` sibling** recording URL, status,
  byte count, and sha256 -- so a later re-fetch can be diffed against exactly
  what the current `cards.json` was built from, and a silently-changed
  upstream is detectable.
- **Source precedence is data, not code** (`sources.PRECEDENCE`) -- the merge
  is shape-agnostic, so re-ranking sources or adding a fourth is a one-line
  change plus one adapter.
- **Per-field provenance is stored on every canonical card** -- when a
  simulation result looks wrong, the first question is "where did this card's
  stats come from", and that must be answerable without re-fetching.
- **Omission is not disagreement** -- a source that lacks a field defers to one
  that has it, and this is not logged as a discrepancy. Otherwise
  `DISCREPANCIES.md` fills with noise and the real conflicts get lost.
- **Source adapters raise `NotImplementedError` rather than guessing field
  names** -- a guessed mapping fails silently, producing a plausible
  `cards.json` full of `None`s instead of an error. See BLOCKER-1.
