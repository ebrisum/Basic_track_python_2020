# Where this project stands against `TCG_AI_BUILD.md`

An evaluation of the uploaded build plan against what is actually in this
repository, section by section. Written at the commit that closed RQ-14,
RQ-15 and RQ-16.

Two things are true at once and neither should be lost in the summary:

- On **rules fidelity, determinism, information-tightness and statistical
  rigour**, this project is *ahead* of what the plan asks for.
- On the **ML stack itself** — encoders, a neural policy/value model, PPO,
  trajectories — it is at zero. Nothing in sections 14, 16, 17, 18 or 22
  exists.

The plan's own section 10 says *"Do not begin model training until the
simulator passes the validation requirements."* That instruction is the most
important line in the document for this project, and the reason the gap
above is the right shape rather than the wrong one.

Legend: **done** / **partial** / **absent** / **n/a**.

---

## Part I — Simulator and adapter (sections 1-11)

| § | Item | Status | Detail |
| --- | --- | --- | --- |
| 1 | Repository discovery, `docs/ai-existing-architecture.md` | partial | Largely moot: the engine was built here, not adapted. The content lives in `README.md`, `DECISIONS.md`, `RULES_SUMMARY.md`; the named file does not exist. |
| 2 | Target architecture | partial | Engine → observation + legal actions → evaluation exists. `StateEncoder` → `PolicyValueModel` does not; a 13-feature linear evaluator stands in its place. |
| 3 | Canonical game state | **done** | `RiftboundState` / `RiftboundObservation`, Python rather than TypeScript. |
| 4 | Card definition vs card instance | **done** | Exactly the split the plan asks for: `CardData` (static, shared, immutable) and `CardRef` (mutable, per-instance). |
| 5 | Observation system | **done** | `RiftboundObservation.build`, plus the leakage suite the plan calls mandatory. See section 28. |
| 6 | Legal action interface | **done** | `engine/actions.py`; the agent may reach nothing else. |
| 7 | **Stable action encoding** | **absent** | No `ActionEncoder`, no factorized (type, source, target, option) representation, no action mask. This is the prerequisite for any policy head. |
| 8 | Headless simulator | **done** | The frozen interface *is* `SimulationEnvironment`. No graphics, no network, no delays; `clone` via a hand-written `__deepcopy__`. |
| 9 | Deterministic simulation | **done** | Seeded throughout; two committed replays hash every step of two full games. |
| 10 | Simulator validation | **done** | 673 tests; 14 structural invariants asserted after every action; the 10,000-match run in `VALIDATION.md`, re-run on the settled 1.2.0 engine. |
| 11 | Performance instrumentation | **done** | `analysis/validate.py` reports games/min, decisions/s, mean branching factor and mean game length, and stamps every run with its provenance. ~100 games/min on the current engine, down from 173 because the 323.6 fix made games 78% longer. Above the plan's initial target of 100, well below its preferred 1,000. |

**The one real hole in Part I is section 7.** Everything else is either done
or documented. Action encoding is small, pure-stdlib work, and nothing in
sections 17-22 can start without it.

---

## Part II — Agents, matches, data (sections 12-15)

| § | Item | Status | Detail |
| --- | --- | --- | --- |
| 12 | Baseline agents | **partial** | `RandomAgent` ✓, `GreedyAgent` ✓, and `ISMCTSAgent` beyond the plan's list. **`AggroAgent`, `ConservativeAgent` and `ObjectiveAgent` are absent.** They matter more than they look: section 23 wants them in the opponent pool, and three agent types is a thin field to train against. |
| 13 | Match runner | **partial** | `analysis/benchmark.py::duel` and `analysis/batch.py` run matches and aggregate; `Replay` carries actions and a final state hash. No worker-pool parallelism — everything is single-process. |
| 14 | **Trajectory format** | **absent** | The replays are a *determinism* artefact, not training data: they hold actions and hashes, no encoded observation, no legal-action mask, no per-transition reward, no action probability, no value estimate. |
| 15 | Reward function | **done** | `returns()` is win 1.0 / loss 0.0 / draw 0.5 — an affine remap of the plan's +1/-1/0. No shaping, which is what the plan asks for. |

---

## Part III — The model (sections 16-22)

This block is **absent in its entirety**, and it is where the plan and this
repository diverge hardest.

| § | Item | Status |
| --- | --- | --- |
| 16 | `StateEncoder` producing object tokens | **absent** — `features()` returns 13 scalars, not a token sequence |
| 17 | Embedding + transformer + policy/value heads | **absent** |
| 18 | Policy scored over legal actions | **absent** — ISMCTS has an unused `prior` hook, defaulted off |
| 19 | Supervised bootstrap | **absent** (the plan marks it skippable) |
| 20 | Baseline-generated games for pretraining | **partial** — games are generated, nothing consumes them as a dataset |
| 21 | Self-play | **partial** — `analysis/self_play_loop.py` runs generations, but saves *weights*, not per-decision trajectories |
| 22 | PPO | **absent** |

### The decision that blocks this block

A transformer policy/value network cannot be written against the standing
constraint. The project's rule from the first brief is **pure stdlib plus
pytest, and ask before adding any dependency** — and sections 16-18 and 22
need at minimum NumPy, realistically PyTorch.

So this is a question for you, not a task I should quietly decide:

1. **Add PyTorch** and build sections 16-22 as written.
2. **Add NumPy only** and build a small MLP by hand — enough to prove the
   pipeline, far short of the plan's transformer.
3. **Stay pure-stdlib** and keep improving the linear evaluator through the
   measurement machinery that already exists.

I am not going to add a dependency without you saying so.

### The more important reason not to start yet

**42 of 526 playable cards fully work — about 8%**, and the two decks the engine measures are both 15/15. A network trained
today would learn a game of costs, stats, movement and scoring, with almost
every printed ability inert. It would learn that game very well. That is
not Riftbound, and the learned weights would not transfer once the cards
work; the whole state distribution moves.

`CORRECTNESS.md` has the full ledger. The short version: **script the cards
before building the network.** That is also what the plan's own section 10
and rule 40.8 ("optimize correctness before performance") say.

---

## Part IV — League, evaluation, operations (sections 23-33)

| § | Item | Status | Detail |
| --- | --- | --- | --- |
| 23 | Opponent league | **partial** | `analysis/ladder.py::League`, a frozen gauntlet in `analysis/rate.py`, `analysis/league/gen_000.json`. The machinery is real; the pool is thin because section 12's agents are missing. |
| 24 | Checkpoint promotion | **done, and ahead of the plan** | The plan asks for "confidence criteria". This has SPRT (as fishtest uses), seat swapping, mirror matchups, and paired seeds. Pairing cut the standard deviation from 0.369 to 0.273. |
| 25 | Elo / rating | **done** | `analysis/ladder.py`: `elo_from_score`, `elo_interval`, `SPRT`. |
| 26 | Curriculum learning | **absent** | |
| 27 | Fixed evaluation scenarios | **absent** | No scenario suite. This is cheap, stdlib, and would catch strategic regressions the win-rate tests cannot localize. |
| 28 | **No-cheating tests** | **done** | `tests/test_no_cheating.py`, added for this evaluation. It found a leak on its first run — in the *opposite* direction: 128.4 grants a facedown card's face to its controller, and the observation showed it to nobody. |
| 29 | Logging | **partial** | The analysis tools print their metrics; only the league state is persisted. No per-run metrics file, no illegal-action counter in a log (it is 0, asserted by tests). |
| 30 | Configuration files | **absent** | CLI flags only; no `ai/config/*.yaml`. Note YAML itself is a dependency; JSON would do. |
| 31 | Unified CLI (`ai validate`, `ai train`, …) | **partial** | Twelve separate entry points under `analysis/`, plus `play.py`. Every capability the plan names has a command; there is no single `ai` front door. |
| 32 | Human-vs-AI interface | **done** | `python3 play.py` — local server, browser UI, hot-seat, human-vs-agent, and spectator mode. `analysis/evaluation.explain()` gives the per-feature contribution; it is not yet surfaced as action probabilities in the UI, because there is no policy to draw them from. |
| 33 | Matchup analysis API | **done** | `analysis/matchup.py`, with confidence intervals and seat-swapped results. |
| 34-35 | Card-impact analysis, deck optimizer | **n/a** | The plan says do not build these yet. They are not built. |

---

## Part V — Discipline (sections 36-37, 40)

| § | Item | Status |
| --- | --- | --- |
| 36 | Persistence layout | different, deliberately — `engine/ cards/ agents/ analysis/ frontend/` rather than `ai/*`, which the plan permits ("adapt to current project conventions") |
| 37 | **Versioning of datasets and replays** | **done** — `engine/versions.py`. The schema versions are *derived* from the dataclasses they describe, so they move whether anyone remembers or not; `card_pool_version` digests every card's gameplay fields; `rules_version` digests the cached rules text. A replay divergence now names which component drifted instead of only reporting a hash mismatch. |
| 40.12 | "Do not silently invent missing game rules" | **done, and it is the spine of the project** — `RULES_QUESTIONS.md` |
| 40.13 | "Mark unresolved rule dependencies" | **done** — 16 numbered entries, 4 now closed |
| 40.5 | "Never expose hidden game state to the agent" | **done** — section 28 |
| 40.6 | "Make every experiment reproducible by seed" | **done** |
| 40.8 | "Optimize correctness before performance" | **done** — that is the whole shape of this repository |
| 40.10 | "Avoid large models until smaller models demonstrate learning" | **done, uncomfortably** — see below |

### On rule 40.10, honestly

The plan says not to reach for a large model until a small one demonstrates
learning. **A small one has not demonstrated learning.** Every attempt to
improve the 13 weights was put through SPRT and *rejected*: SPSA cross-deck
0.487, SPSA mirror 0.485, a recovery run 0.507, a hybrid not established,
and a hill-climb screen whose accepted step failed confirmation. The model
in use today is still the hand-set prior — `fitted: false`.

The learner *can* learn: deliberately damaging a weight and letting the
tuner recover it produced **+205 Elo out of the hole**. So the machinery
works and the signal is real. What has not been shown is that it can improve
on the hand-set prior, which is a different and harder claim, and one that
feature collinearity (`analysis/collinearity.py`) partly explains — several
features are nearly linearly dependent, leaving flat directions where the
optimiser has nothing to grip.

Taking rule 40.10 seriously means: that is a reason to fix the *features*
and the *content*, not to jump to a transformer.

---

## Milestones

| Milestone | Status |
| --- | --- |
| **1 — AI adapter** | **nearly complete**. Canonical state ✓, observation ✓, legal actions ✓, deterministic wrapper ✓. Missing: action encoding (§7). Success criterion — 10,000 matches without error — measured in `VALIDATION.md`. |
| **2 — Baseline agents** | **partial**. 3 of the 5 named agents; batch runner and benchmarking ✓. |
| **3 — Training dataset** | **not started**. Throughput is not the obstacle: the 10,000-match run alone produces well over a million decisions. The obstacle is that no trajectory format exists to record them in. |
| **4 — Policy + value model** | **not started**, and gated on the dependency decision above. |
| **5 — Self play** | **partial**. The loop, league and promotion tests exist. Its success criterion — "a trained checkpoint consistently defeats RandomAgent" — is met only by the *hand-set* model (0.967, +585 Elo), never by a trained one. |
| **6 — League** | **machinery done, result not demonstrated**. |
| **7 — Competitive evaluation** | **partial**. Matchup matrix ✓, human-vs-AI ✓. Fixed scenarios ✗. Representative decks: 3 exist; the two real Milestone 1 decklists were to come from you and have not arrived. |

## Acceptance criteria (section 39)

- [x] Existing working game logic remains intact
- [x] AI can run games headlessly
- [x] Simulator supports deterministic seeds
- [x] Legal action generator never returns illegal actions
- [x] AI cannot observe hidden opponent information
- [x] Random matches complete without crashes
- [x] 10,000-match validation suite passes — see `VALIDATION.md`
- [x] Trajectories can be stored and replayed *(as replays; not as training trajectories — §14)*
- [~] Policy/value model can score positions *(a linear evaluator can; there is no policy)*
- [x] Model only chooses legal actions
- [ ] Self-play training runs end-to-end *(the loop runs; no learned checkpoint has been promoted)*
- [x] Checkpoints can be evaluated against old checkpoints
- [x] League training is operational
- [~] Metrics are persisted *(league state only)*
- [x] Rule / card-pool versions are recorded
- [x] Human-vs-AI debugging is possible

**12 of 16 clean, 2 partial, 2 not met.**

---

## What I would do next, in order

Everything in steps 1-3 is pure stdlib and needs no decision from you.

1. **Script cards** (RQ-5). 8% is the ceiling on every number this project
   can produce. Nothing else in this list changes that, and the rules work is
   now far enough along that scripting will not be invalidated the way it
   would have been a day ago.
2. **The last mechanical gaps**: Double, Prevent and Swap (7 cards, the only
   Game Actions any card still needs), then Repeat (820) and Weaponmaster
   (821), both unblocked by the targeting and trigger work.
3. **Section 7 — the action encoder**, then **§14 trajectory format**, then
   **§12's three missing agents**, then **§27 scenarios**, then **§30 config**.
   That order unblocks the most with the least.
4. **Then, and only with your answer on dependencies**, sections 16-22.

Section 37 (versioning) is done, and its timing turned out to matter: the
four rules fixes that followed it each invalidated the recorded replays, and
the provenance stamp is what separates "the representation changed" from
"the rules regressed".

The plan is a good plan. The disagreement I have with running it in its
stated order is section 41's step 15 ("add small policy/value network")
arriving before the cards work — and section 10's own "do not begin model
training until the simulator passes validation" is the plan agreeing with
me. The 323.6 fix is the argument in one line: a model trained a week ago
would have learned that battlefields are free to hold.
