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

## Session 2 -- rules-agnostic infrastructure

Built while BLOCKER-1 stands. Everything here is independent of Riftbound's
rules and card pool, so none of it is blocked and none of it presumes a rule.

- **`state.current_player` added to the agent-facing interface** -- NEEDS
  APPROVAL. The brief lists five calls; a replay cannot verify turn or
  priority order without knowing whose move it is, and OpenSpiel's interface
  has `current_player()` too. Flagged rather than assumed silently.
- **State hashing is derived from the frozen interface, not added to it** --
  `replay.state_hash()` hashes both players' observations plus terminal state
  and returns. Keeps the agent-facing contract at exactly the specified calls
  and forces both players' hidden information into the hash, so opponent-side
  drift cannot slip past.
- **Replays record actions by `repr()`** -- readable and hand-diffable, which
  matters because a human reads these when one breaks. Makes `repr()`
  load-bearing: changing it invalidates replays containing that action. That
  is intended -- it forces the change to be noticed.
- **Action reprs must be unique within a state** -- the replay runner rejects
  ambiguity rather than picking a match. Silently choosing would make a
  replay pass while replaying a different game.
- **`Observation.to_canonical_bytes()` rather than hashing objects** --
  Python's `hash()` is salted per process, so it cannot back a stored replay.
- **Toy game lives in `tests/fixtures/`, not in the package** -- it exists to
  prove the harness and validate the interface, and must never be importable
  by `engine/`, `cards/`, or `analysis/`.
- **Batch seeds game `i` with `base_seed + i` and rebuilds agents per game** --
  any single game re-runs standalone from its own seed, so an outlier is
  investigable without replaying the whole batch.
- **`play_game` reads point differential via `getattr(state, "scores", None)`**
  -- optional, since scoring is not part of the frozen interface. Revisit
  once the Riftbound state exists and its scoring shape is known.
- **Runaway games raise rather than hang** -- a `max_steps` ceiling turns a
  rules loop into a named failure instead of a wedged batch.
