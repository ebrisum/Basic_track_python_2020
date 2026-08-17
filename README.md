# riftbound-sim

A headless, deterministic Riftbound rules engine, built to answer one
question: *what is my field-weighted win rate, and how does it change if I
swap card X for card Y?*

## Status

Repo scaffolded, data pipeline built, and all **rules-agnostic** Milestone 1
infrastructure implemented and tested. Everything that depends on card data or
the rules text is blocked.

| Session 1 step | State |
| --- | --- |
| 1. Scaffold the repo | done |
| 2. Pull and normalize card data | **blocked** — all four sources refused by the egress proxy |
| 3. Fetch rules, write `RULES_SUMMARY.md` | **blocked** — rules PDF unreachable |
| 4. Propose a DSL primitive list | **blocked** — derives from card text, which step 2 would have supplied |

Built since, none of it rules-dependent:

| Component | State |
| --- | --- |
| Frozen agent-facing interface (`engine/interface.py`) | done, contract-tested |
| Replay format + runner (`engine/replay.py`) | done, 3 synthetic replays checked in |
| Random agent (`agents/random_agent.py`) | done |
| Batch runner + aggregation (`analysis/batch.py`) | done |
| Determinism guarantees | done, enforced by tests |
| `engine/state.py`, `turn.py`, `combat.py`, `cards/` | **blocked** — needs the rules |

**83 tests passing.** See **BLOCKER-1** in
[`RULES_QUESTIONS.md`](RULES_QUESTIONS.md) for the evidence and three ways to
unblock.

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
directly. Defined as runtime-checkable protocols in `engine/interface.py`:

```python
state.legal_actions() -> list[Action]
state.apply(action) -> None
state.observation(player_id) -> Observation   # hidden information stripped
state.is_terminal() -> bool
state.returns() -> tuple[float, float]
state.current_player -> int                   # ADDED — see DECISIONS.md
```

`current_player` is one call beyond the five the brief specifies; it is
needed to verify turn and priority order in replays, and OpenSpiel exposes it
too. Flagged for approval rather than assumed.

State hashing is deliberately *not* on this interface — `replay.state_hash()`
derives it from the calls above, so what agents see stays exactly this list.

## Replay harness

`tests/replays/` holds JSON game logs: an ordered list of
`(player, action, expected_state_hash)` plus an initial hash and final
returns. The runner fails at the first divergent step and names it.

```sh
.venv/bin/python tests/replays/generate_synthetic.py   # only when the FORMAT changes
```

Never regenerate a replay to make it pass. A divergence means the engine
changed, and that change is what needs justifying — regenerating destroys the
only signal the harness produces. Real Rift Atlas games drop in as-is and are
never regenerated.

## Running a batch

```python
from agents.random_agent import RandomAgent
from analysis.batch import run_batch

batch = run_batch(build_state, (lambda s: RandomAgent(s),
                                lambda s: RandomAgent(s + 1000)),
                  games=1000, base_seed=0)
print(batch.summary())   # win rate, mean turns, point-differential histogram
```

Game `i` uses seed `base_seed + i` and agents are rebuilt per game, so any
single game reproduces standalone from its own seed.

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
