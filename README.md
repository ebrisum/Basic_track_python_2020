# riftbound-sim

A headless, deterministic Riftbound rules engine, built to answer one
question: *what is my field-weighted win rate, and how does it change if I
swap card X for card Y?*

## Status

**Session 1, partially complete.** Repo scaffolded and the data pipeline
built; card data and rules could not be retrieved.

| Session 1 step | State |
| --- | --- |
| 1. Scaffold the repo | done |
| 2. Pull and normalize card data | **blocked** — all four sources refused by the egress proxy |
| 3. Fetch rules, write `RULES_SUMMARY.md` | **blocked** — rules PDF unreachable |
| 4. Propose a DSL primitive list | **blocked** — derives from card text, which step 2 would have supplied |

See **BLOCKER-1** in [`RULES_QUESTIONS.md`](RULES_QUESTIONS.md) for the
evidence and three ways to unblock. No engine code has been written — Session
1 explicitly ends before that.

## Setup

Python 3.12, stdlib only, plus `pytest` for tests.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest tests/ -q
```

## Data pipeline

Fetching and normalizing are separate: fetching touches the network and caches
to `data/raw/`; normalizing is a pure, offline, testable function over that
cache. Simulation never touches either — it reads only `data/cards.json`.

```sh
.venv/bin/python data/fetch.py --all          # cache every source
.venv/bin/python data/fetch.py --enumerate-tabs   # list spreadsheet tabs
.venv/bin/python data/normalize.py            # -> cards.json + DISCREPANCIES.md
```

`fetch.py` runs today and fails cleanly with a per-source diagnostic.
`normalize.py`'s merge, precedence, and discrepancy reporting are implemented
and tested; its three per-source adapters raise `NotImplementedError` until a
real payload has been seen, because a guessed field mapping fails silently
rather than loudly.

## Layout

```
data/       raw/ cache, fetch.py, sources.py, normalize.py -> cards.json
engine/     rules; knows nothing about specific cards
cards/      data + effect scripts; knows nothing about search or agents
decks/      decklists
agents/     consume the legal-action API only
analysis/   batch runs and aggregation
tests/      unit tests; replays/ holds recorded game logs
```

Layer separation is strict and load-bearing — it is what lets an ISMCTS agent
drop in later without a rewrite.

## Agent-facing interface

Frozen, and mirrors OpenSpiel so its ISMCTS implementation can be used
directly:

```python
state.legal_actions() -> list[Action]
state.apply(action) -> None
state.observation(player_id) -> Observation   # hidden information stripped
state.is_terminal() -> bool
state.returns() -> tuple[float, float]
```

## Ground rules

- **Deterministic.** Same seed + same policies = byte-identical game log.
- **Every rule cites** the comprehensive rules or explicit card text.
- **Approximations are allowed; hidden approximations are not.** Anything
  unresolved goes in `RULES_QUESTIONS.md`.
- **Test first** for `engine/` and `cards/`. No card is done without a unit
  test asserting its effect on a constructed state.
- **No dependency beyond stdlib + pytest** without asking first.

## Documents

- [`RULES_SUMMARY.md`](RULES_SUMMARY.md) — implementation target for `engine/`
- [`DSL.md`](DSL.md) — effect primitive spec; the gate before bulk scripting
- [`RULES_QUESTIONS.md`](RULES_QUESTIONS.md) — open rules decisions and blockers
- [`DECISIONS.md`](DECISIONS.md) — architectural choices, one line each
- [`data/DISCREPANCIES.md`](data/DISCREPANCIES.md) — generated source conflicts
