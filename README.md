# riftbound-sim

A headless, deterministic Riftbound rules engine, built to answer one
question: *what is my field-weighted win rate, and how does it change if I
swap card X for card Y?*

## Status

**Playable.** The engine implements the structural rules of 1v1 Riftbound and
ships with a local web frontend, so a full game can be played end to end:
die roll, deck selection, battlefield selection, mulligan, turns, resources,
movement, contesting, combat, scoring, win.

Card text **executes** for every card in the starter decks: a DSL interpreter
runs 15 primitives drawn from the game's own Game Actions (413-444), with
player choices raised through `legal_actions()` rather than a side channel.

**The remaining caveat:** only the 24 cards in the two starter decks are
scripted. The other ~400 playable cards have no script, so their text is inert
and every affected card is stamped `inert` in the UI with a tooltip saying
why. Scripting more is mechanical work, not new architecture. See RQ-5 in
[`RULES_QUESTIONS.md`](RULES_QUESTIONS.md).

| Area | State |
| --- | --- |
| Card data + official rules, cached | done — 908 cards, Core Rules v1.4 |
| `RULES_SUMMARY.md`, `DSL.md` | done, fully cited |
| Frozen interface, replay harness, batch runner | done |
| Engine: setup, turns, resources, movement, combat, scoring | done |
| Frontend (local web UI) | done |
| Card effect DSL interpreter | done — 15 primitives, 24 cards scripted |
| Chain, priority, focus, showdowns, combat | done — RQ-10 closed |
| Card art in the UI | done — 907/908 cards carry Riot's own render |
| Scripting the rest of the card pool | **remaining work** |

**226 tests passing** — 32 one-per-card assertions and 30 covering the Chain,
priority, focus and showdowns — plus two full Riftbound games in the replay
harness and HTTP-level frontend tests (no browser dependency). 1,000 random games run in ~41s single-threaded.

## Play a game

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python pytest pypdf
.venv/bin/python decks/build_decks.py        # generate legal starter decks
.venv/bin/python -m frontend.server          # -> http://127.0.0.1:8000
```

Hot-seat: both players share one screen, and the **view as** selector switches
whose hand is shown. The server never reveals the other player's hand (128).

The table is laid out like the physical playmat, one per player, mirrored so
the two battlefield rows meet in the middle:

```
Battlefield Zone            | Legend | Champion
Base: Units + Gears         |        Main Deck
Rune Deck | Base: Runes     |        Trash
```

with the 0–8 score track down each edge. Trash is public (108.2.d), so
clicking a trash stack opens it. The **Chain** panel on the right shows what is
on the chain, which item resolves next, and who holds priority and focus; the
header carries the current timing state (Neutral/Showdown × Open/Closed).

Card art loads straight from Riot's CDN in your browser; if it is unreachable
the card falls back to a fully legible text face and the game plays normally.

The UI can only submit moves the engine already listed as legal — actions are
addressed by the engine's own `repr()`, resolved against `legal_actions()`
exactly the way the replay runner does. There is no rules logic in the
browser.

## Tests

```sh
.venv/bin/python -m pytest tests/ -q
```

## Data pipeline

Fetching and normalizing are separate: fetching touches the network and caches
to `data/raw/`; normalizing is a pure, offline, testable function over that
cache. Simulation never touches either — it reads only `data/cards.json`.

```sh
.venv/bin/python data/fetch.py --all            # cache every source
.venv/bin/python data/fetch.py --show-blocked   # the brief's original four
.venv/bin/python data/normalize.py              # -> cards.json + DISCREPANCIES.md
```

Verified reproducible: deleting `data/raw/` and re-running `--all` restores
every file byte-identically (sha256s in each `_meta.json`).

Adapters are the only source-shape-aware code and were written against the
real cached bytes, never against documentation. The npm source's numeric
fields are deliberately not mapped — see RQ-1.

## Layout

```
data/       raw/ cache, fetch.py, sources.py, normalize.py -> cards.json
engine/     rules: zones, state, actions, setup, combat, replay, interface
cards/      card database + keyword parsing; knows nothing about search
decks/      decklists + build_decks.py
agents/     consume the legal-action API only
analysis/   batch runs and aggregation
frontend/   stdlib HTTP server + single-page client (no rules logic)
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
