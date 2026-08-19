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
runs 31 effect primitives and 5 cost primitives drawn from the game's own
Game Actions (413-444), with player choices raised through `legal_actions()`
rather than a side channel.

Gear splits the way the physical game does: only the 27 gear carrying the
**Equipment** tag can be attached to a unit (150.1), and they attach by paying
an **Equip** cost as a separate activated ability (818.1) — not for free when
played. The other 50 gear can never be equipped; they sit in the Base and use
their own abilities. Equip costs, Might Bonuses, Quick-Draw and Weaponmaster
are all *derived from printed card text*, so 25 of those 27 are equippable
from their own card face with no per-card Python at all. The two exceptions
have Equip costs that are not a resource payment (RQ-13).

**The remaining caveat:** 42 of 526 playable cards fully work — about 8%. The
rest have no script, so their text is inert and every affected card is stamped
`inert` in the UI with a tooltip saying why. Scripting more is mechanical work,
not new architecture. See RQ-5 in [`RULES_QUESTIONS.md`](RULES_QUESTIONS.md).
Both decks the engine actually measures are 15 of 15 distinct cards complete;
the 8% is the ceiling on simulating an *arbitrary* deck.

| Area | State |
| --- | --- |
| Card data + official rules, cached | done — 908 cards, Core Rules v1.4 |
| `RULES_SUMMARY.md`, `DSL.md` | done, fully cited |
| Frozen interface, replay harness, batch runner | done |
| Engine: setup, turns, resources, movement, combat, scoring | done |
| Frontend (local web UI) | done |
| Card effect DSL interpreter | done — 31 effect primitives, 42 cards executable |
| Chain, priority, focus, showdowns, combat | done — RQ-10 closed |
| Gear: Equipment, Equip costs, Might Bonuses, attachment | done — derived from card text |
| Scoring: fitted value heuristic + calibration | done — see [`SCORING.md`](SCORING.md) |
| Card art in the UI | done — 907/908 cards carry Riot's own render |
| Core Rules audited rule by rule: 300s, 400s, 700s, 800s | done — 20 live bugs found and fixed |
| State invariants checked after every action | done — 14 checks, each citing its rule |
| Agents: Random, Greedy, ISMCTS, Aggro, Conservative, Objective | done |
| Action encoding + trajectory format + dataset generator | done — `learning/` |
| Fixed benchmark scenarios | done — `python3 cli.py evaluate --agent all` |
| One CLI for everything | done — `python3 cli.py --help` |
| A neural policy/value model | **blocked** — needs a dependency; see [`BUILD_STATUS.md`](BUILD_STATUS.md) |
| Scripting the rest of the card pool | **remaining work** |

**786 tests passing** — 152 on the core rules, turn structure and the Chain;
125 on card scripts, gear, targeting, buffs, Deflect, Repeat and death
replacement; 85 on information tightness in both directions (`test_no_cheating`
that nothing leaks in, `test_choice_visibility` that nothing a player is
entitled to see is missing); 62 on determinism, cloning, replay and the
provenance stamp; 28 on the action encoding; 25 on the frozen interface; 25
driving the frontend over HTTP — plus two full Riftbound games replayed action by action
against a committed hash. The suite runs in under two minutes.

A 10,000-game acceptance run — 2.19 million decisions, zero crashes, zero
impossible states, zero illegal actions — is recorded in
[`VALIDATION.md`](VALIDATION.md), and what a clean run does *not* prove is in
[`CORRECTNESS.md`](CORRECTNESS.md).

## Play a game on your own machine

The engine is **pure standard library**, so there is nothing to install and no
virtual environment to create. Clone it and run:

```sh
git clone <this repo> && cd riftbound-sim
python3 play.py
```

That builds the starter decks if they are missing, starts the local server and
opens <http://127.0.0.1:8000>. Python 3.10+ is enough (verified on 3.11 and
3.12); `pytest` is needed only to run the tests, not to play.

```sh
python3 play.py --watch            # two agents play, you watch
python3 play.py --seat1 ismcts     # you are P0, the search plays P1
python3 play.py --port 9000 --no-open
```

Hot-seat: both players share one screen, and the **view as** selector switches
whose hand is shown. The server never reveals the other player's hand (128).

### Watching the agents play

Hand either seat to an agent and watch it move on the same board a human
plays on:

```sh
.venv/bin/python -m frontend.server --seat0 greedy --seat1 ismcts --watch
```

or set it live from the **Spectate** bar: pick an agent per seat, **Play** /
**Pause**, **Step** one decision at a time, and drag the speed slider. The
Agent panel reports what the agent just played, how many legal options it had,
how long it thought, and its own win estimate for the position it created —
so the search can be watched changing its mind.

Deliberately the *same* UI, not a separate viewer: if the agent's move and the
rendered board ever disagreed, the UI would be lying. An agent seat is never
offered clickable actions, and `concede` is withheld unless both seats are
human (649) — otherwise a random policy resigns on turn one.

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

Under each battlefield your mat shows a **forecast** — whether you can take it
this turn, whether you can lose it, the Might each side has committed there,
what each could still march in, and an upper bound on what they could deploy
from hand. See [Reading the board](#reading-the-board) below.

Card art loads straight from Riot's CDN in your browser; if it is unreachable
the card falls back to a fully legible text face and the game plays normally.

The UI can only submit moves the engine already listed as legal — actions are
addressed by the engine's own `repr()`, resolved against `legal_actions()`
exactly the way the replay runner does. There is no rules logic in the
browser.

## Reading the board

Battlefields change hands. Moving a unit onto one you do not control Contests
it (450); at the next Cleanup that opens a Showdown, and whoever is left
standing there Establishes Control (466.5) — **including the defender**
(466.5.e), and nobody at all if both sides wipe out (466.5.b). Control is a
lease, not a deed.

Whether an attack works is arithmetic, not a guess. **465.2.c** has each side
assign damage equal to its *summed Might* among the other's units, lethal in
full before moving on, so a side wipes the other exactly when its Might covers
what the other still has standing. `engine/threat.py` computes that directly:

```python
from engine.threat import forecast_all
for view in forecast_all(state, player):
    print(view.name, view.can_take, view.can_lose, view.committed)
```

The board is public (107.1.d), so committed Might and reinforcements are
**exact** for both sides — including theirs. Only the hand is hidden, and it is
bounded rather than unknown: **103.1.b.2** fixes a deck's Domain Identity from
its Champion Legend, and every card in the deck must abide by it (103.1.b.3-4),
so the worst thing the opponent can be holding is the best card in their
domains they can currently pay for. That is `hidden_threat`, and because a unit
played this way enters exhausted (143.4), it is pressure on the *next* turn —
which is why `can_lose` (this turn, exact) and `can_lose_next_turn` (includes
the bound) are reported separately.

Might is not just the printed number: **807** gives an attacker +Assault,
**814** gives a defender +Shield, and attached Equipment adds its Might Bonus
(718.4). `projected_might` applies a designation a unit does not yet hold, so a
forecast fights the unit at the Might it *would* have.

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
cli.py      one front door: validate, benchmark, evaluate, generate, play
config/     *.toml run settings; defaults < file < flags
data/       raw/ cache, fetch.py, sources.py, normalize.py -> cards.json
engine/     rules: zones, state, actions, setup, combat, replay, interface
cards/      card database + keyword parsing; knows nothing about search
decks/      decklists + build_decks.py
agents/     consume the legal-action API only (random, greedy, ismcts, styles)
analysis/   batch runs, evaluation heuristic, weight fitting, scenarios/
learning/   action encoding, trajectory format, dataset generator, config
frontend/   stdlib HTTP server + single-page client (no rules logic)
tests/      unit tests; replays/ holds recorded game logs
```

Layer separation is strict and load-bearing — it is what lets an ISMCTS agent
drop in later without a rewrite. `learning/` has a rule of its own: it may
read a `RiftboundObservation` and a list of `Action`s, and may never touch
`RiftboundState`. An encoder with state access would feed a model information
the interface does not grant, and no leakage test would catch it, because the
leak would be in the encoder rather than the observation.

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

There is one narrower case that is not that, and it has its own tool:

```sh
.venv/bin/python tests/replays/repair.py            # inspect
.venv/bin/python tests/replays/repair.py --apply    # update moved hashes only
```

`repair.py` handles a *justified* engine change where the recorded actions are
all still legal, the game still ends the same way, and only stored hashes
moved. It refuses everything else — an action that is no longer legal, or an
outcome that changed, is a real regression and it says so instead of papering
over it.

Regenerating is worse than it looks in this case. When the rune-pool fix (167)
changed which actions were legal, regenerating from the seed turned a
352-step recorded game into a *different* 290-step one, discarding the longer
fixture; repairing moved exactly one hash in each file and kept both games
intact.

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

## Scoring the agent

The reward is **winning and nothing else** — `returns()` is win/loss/draw,
derived from 194.2. The position heuristic that guides search lives separately
in `analysis/evaluation.py`, is fitted to self-play outcomes rather than
asserted, and is measured on held-out games.

```sh
.venv/bin/python analysis/fit_weights.py --games 300   # fit, with a held-out split
.venv/bin/python analysis/calibrate.py --games 120     # does it predict winning?
.venv/bin/python analysis/benchmark.py --games 60      # does it cause winning?
.venv/bin/python analysis/nulltest.py --games 400      # is the harness unbiased?
.venv/bin/python analysis/self_play_loop.py --generations 5 --field   # train
.venv/bin/python analysis/rate.py --table              # did it actually improve?
```

**How to tell whether training worked** is its own problem, and a harder one
than running the training: see [`TRAINING.md`](TRAINING.md). Short version —
every generation is kept in a league and rated against a *frozen* gauntlet,
because "beat the previous generation" cannot distinguish improvement from
walking in a circle; and promotion uses SPRT rather than a fixed game count,
because a 40-game gate can only resolve a 0.65 edge and real improvements are
0.53–0.58.

`agents/ismcts.py` is the answer to the conditional judgments a weighted sum
cannot make — sweep when behind, deny at 7 but not at 1, hold a Reaction for
something worth answering. Search plays the consequences out; the evaluation
only scores leaves. It determinizes hidden information rather than reading the
opponent's hand.

The greedy agent beats random **0.967 (116-4)** — about +585 Elo — so the
heuristic captures something real.

**Read every cross-deck number here with the decks in mind.** With identical
agents on both sides, `volibear_body_fury` beats `jinx_chaos_fury` about
**85-15**. A better agent is worth 0.52–0.55; a luckier deck is worth 0.85. So
agent-vs-agent comparisons run in **mirrors** — each deck against itself — and
the deck cancels exactly. See [`TRAINING.md`](TRAINING.md).

**Search was worse than the evaluation it wraps, because of a bug.** Every
node was backed up from the *searching* player's point of view, so at nodes
where the opponent chooses, the search picked whatever helped the searcher —
it planned against a cooperating opponent, and more iterations bought a more
confident wrong plan. ISMCTS(60) measured **0.350 (14-26)** against the greedy
agent it is built on; with the perspective fixed it went to **0.433 (26-34)**.
Full account in [`SCORING.md`](SCORING.md).

But the headline result is a warning: **fitting improved prediction and made
play worse.** Brier went 0.1815 → 0.1666 and accuracy 0.687 → 0.727, and the
fitted model then *lost* to the hand-set prior 14-26 head-to-head (95%
interval 0.202–0.498). The weights were learned from random self-play, so they
captured correlations rather than causes — `hand_diff` came out negative
because random agents hoard cards they cannot play.

So the promotion gate is the benchmark, not Brier: `fit_weights.py` writes a
candidate and installs nothing. Full write-up in [`SCORING.md`](SCORING.md).

The same gate applies to new features. `takeover_edge` — battlefields each
side could take this turn, from the exact combat arithmetic — scores **0.537
(43-37, interval 0.428–0.647)** against the identical agent with that one
weight zeroed, so it is *indistinguishable*, not an improvement. It ships
because the fitter can only learn weights for features it can see, and because
the same computation drives the board forecast in the UI; it is not claimed to
make the agent stronger.

## Scripting cards with an LLM

Hand-scripting ~900 cards is the bottleneck. `cards/extract.py` asks Claude to
read a card's printed text and emit a `CardScript` as **data**, constrained by
a JSON schema whose enums are the DSL primitives.

```sh
.venv/bin/python cards/extract.py --card OGN-003 --dry-run   # inspect prompts, no API call
.venv/bin/python cards/extract.py --set OGN --limit 25        # extract a slice
.venv/bin/python cards/extract.py --set OGN --batch           # Batches API, 50% cost
```

The safety property is unchanged from the brief: **the model never emits
code.** It fills a fixed schema, and `validate()` rejects anything outside the
vocabulary, so a hallucinated primitive fails loudly instead of executing.
Extractions land in `cards/scripts/generated/` with provenance (model, prompt
version, sha256 of the card text) and are **not loaded by the engine** —
promotion is a human decision and every card still needs a test.

Needs the `anthropic` package and credentials; neither is required to run the
engine or the tests.

## Documents

- [`TRAINING.md`](TRAINING.md) — how to see improvement, and what to adjust
- [`SCORING.md`](SCORING.md) — reward vs heuristic, and what the numbers say
- [`RULES_SUMMARY.md`](RULES_SUMMARY.md) — implementation target for `engine/`
- [`DSL.md`](DSL.md) — effect primitive spec; the gate before bulk scripting
- [`RULES_QUESTIONS.md`](RULES_QUESTIONS.md) — open rules decisions and blockers
- [`DECISIONS.md`](DECISIONS.md) — architectural choices, one line each
- [`data/DISCREPANCIES.md`](data/DISCREPANCIES.md) — generated source conflicts
