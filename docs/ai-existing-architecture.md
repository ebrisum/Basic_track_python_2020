# Architecture map

`TCG_AI_BUILD.md` section 1 asks for this file before anything else: an
inventory of what exists, what is missing, what needs an adapter, and what
must not be touched.

It is written from an unusual position. The plan assumes an existing game
engine that an AI stack is being bolted onto. Here the engine was built in
this repository, for this purpose, so "reusable components" is nearly the
whole list and "components requiring adapters" is nearly empty. The section
that earns its keep is the last one: **what must not be modified**, and why.

---

## Where each thing the plan names actually lives

| The plan's name | Here | Notes |
| --- | --- | --- |
| `Game` | `engine/state.py::RiftboundState` | There is no separate `Game` object. The state *is* the game; rules are methods on it. |
| `GameState` | `engine/state.py::RiftboundState` (2,357 lines) | The one large module in the project, and deliberately so — the rules interact, and splitting them across files would move the coupling rather than remove it. |
| `PlayerState` | `engine/zones.py::PlayerState` | Zones as lists of instance ids, plus `RunePool`. |
| `Card` | `cards/database.py::CardData` (static) and `engine/zones.py::CardRef` (per-instance) | The split section 4 asks for, and it predates the plan. |
| `Deck` | `decks/*.json` + `engine/setup.py::load_deck` | Decks on disk are fixtures; `build_decks.py` refuses to overwrite without `--force`. |
| zones | `engine/zones.py` | Hand, Main Deck, Rune Deck, trash, banishment, Champion Zone, Legend Zone, Base, battlefields. |
| turn / phase management | `engine/state.py` (`_run_automatic_phases`, `Phase`) | 300-series rules. |
| resource system | `engine/zones.py::RunePool`, `engine/state.py` channel/tap/recycle | 163-165. |
| combat | `engine/state.py` (showdowns, combat, `deal_damage`) | 460s. No separate module; combat reads and writes the same state as everything else. |
| targeting | `cards/primitives.py::_targets`, `engine/state.py::_advance_targeting` | 355.7-355.10; targets are declared at finalization (355.8). |
| effect resolution | `cards/primitives.py` (31 effect + 5 cost primitives), `cards/dsl.py` | Data-driven. No per-card Python anywhere, by rule. |
| RNG | `RiftboundState._rng`, seeded from the game seed | Every source of randomness routes through it. |
| win/loss detection | `engine/state.py` (`is_terminal`, `returns`, `_score`) | 194 / 471.1.b. |
| legal move generation | `engine/state.py::legal_actions` | The only way an agent learns what it may do. |
| serialization | `engine/observation.py::to_canonical_bytes`, `engine/replay.py` | Byte-stable; backs replay hashing. |
| match logging | `engine/replay.py`, `analysis/validate.py --metrics` | Replays hash every step; metrics files carry a provenance stamp. |
| existing bots | `agents/` — random, greedy, ismcts, styles | Six policies. |
| tests | `tests/` — 790 | Test-first for `engine/` and `cards/`, by the founding brief. |

---

## Reusable as-is

Everything under `engine/`, `cards/`, `agents/` and `analysis/`. The engine
already exposes exactly the interface the plan's section 6 and 8 ask for, and
it was frozen before any of this work began:

    legal_actions()   apply(action)   observation(player)
    is_terminal()     returns()       current_player

Nothing else is reachable from an agent. That is not a convention — the
observation is built by removing hidden information (`engine/observation.py`),
and 35 tests in `tests/test_no_cheating.py` assert nothing leaks through it.

## Missing

- A **`StateEncoder`** producing object tokens (section 16). `features()`
  returns 13 scalars. The cost of that is measured rather than argued: on
  100% of sampled evaluator ties — which are 77% of all decisions — every tied
  action produces an *identical* feature vector, so the current features
  cannot distinguish three quarters of the choices the engine poses.
- A **policy/value network** and the loop that trains it (17, 18, 22). These
  need a dependency beyond the standard library; the decision is open and is
  recorded in `BUILD_STATUS.md`.
- **Card scripts** for most of the pool: 42 of 526 playable cards have working
  text (RQ-5). This bounds everything, not just the model.

## Needing an adapter — and the adapters that now exist

The engine speaks in `Action` objects and `RiftboundObservation` dataclasses;
a model speaks in integers and vectors. `learning/` is that boundary:

- `learning/action_encoding.py` — a 4,507-wide slot-based action space with a
  mask, so an illegal action is unrepresentable rather than merely rejected.
- `learning/trajectory.py` — the on-disk record of a decision.
- `learning/generate.py` — datasets from the baseline agents.
- `learning/config.py` — run settings, defaults < file < flags.

**`learning/` has one rule and it is load-bearing: it may read a
`RiftboundObservation` and a list of `Action`s, and may never touch
`RiftboundState`.** An encoder with state access would feed a model
information the interface does not grant, and no leakage test would catch it,
because the leak would be in the encoder rather than the observation.

## Must not be modified

1. **The six-call interface.** Adding a seventh call is how an agent starts
   reading the true state. `state.current_player` was added as the sixth and
   is flagged in `DECISIONS.md` as needing sign-off (RQ-4) rather than assumed.
2. **`returns()`.** Win 1.0 / loss 0.0 / draw 0.5, and nothing else.
   `analysis/evaluation.py` is a *prior over winning*, not a payout;
   `learning/config.py` raises if a config tries to enable reward shaping.
3. **The card-effect boundary.** Effects are data built from DSL primitives.
   No per-card Python, and no arbitrary-code escape hatch — the founding
   brief's hardest constraint and the reason the card layer stays auditable.
4. **`engine/observation.py`'s privacy rules.** Every field is there because a
   numbered rule grants it to that player. The suite tests both directions:
   nothing leaks in, and nothing a player is entitled to see is missing.
5. **Provenance stamps.** `engine/versions.py` derives its schema versions
   from the dataclasses, so they cannot be forgotten. Three validation runs
   have been discarded because a stamp moved mid-run; that is the mechanism
   working, not failing.

## Performance, for headless simulation specifically

Measured, not estimated:

| | Games/min |
| --- | --- |
| serial, invariants after every action | 95 |
| serial, no checking | 103 |
| **4 workers, invariants after every action** | **379** |

- The single largest win already taken: replacing `copy.deepcopy` with a
  hand-written `__deepcopy__` after profiling showed **89% of runtime inside
  it**. 3.8x, and the remaining cost is spread rather than concentrated.
- `GreedyAgent` clones the state once per legal action, so agent choice
  dominates throughput. The figures above are Random-vs-Greedy, which is what
  the analysis tools actually run.
- Dataset generation is slower than validation (~69 games/min) because it
  serialises an observation per decision. Building the slim record directly
  from `fields()` instead of `asdict()` bought 7% of that; the rest is JSON.
- **Correct games are longer games.** Part of the fall from 173 games/min in
  engine 0.4.0 to 95 today is rules getting more right, and that part is not
  a regression to optimise away.
