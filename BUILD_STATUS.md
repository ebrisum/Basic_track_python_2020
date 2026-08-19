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
| 7 | **Stable action encoding** | **done** | `learning/action_encoding.py`: a 4,507-wide space, slot-based so an index does not depend on a per-game `instance_id`, factorized into (type, slot, option) and recoverable by `factor()`. `mask()` makes section 29's illegal-action count structurally zero. 28 tests, including an exhaustive proof that the mulligan-subset enumeration is a bijection. |
| 8 | Headless simulator | **done** | The frozen interface *is* `SimulationEnvironment`. No graphics, no network, no delays; `clone` via a hand-written `__deepcopy__`. |
| 9 | Deterministic simulation | **done** | Seeded throughout; two committed replays hash every step of two full games. |
| 10 | Simulator validation | **done** | 778 tests; 14 structural invariants asserted after every action; the 10,000-match run in `VALIDATION.md`, re-run on the settled 1.2.0 engine. |
| 11 | Performance instrumentation | **done** | `analysis/validate.py` reports games/min, decisions/s, mean branching factor and mean game length, and stamps every run with its provenance. ~100 games/min on the current engine, down from 173 because the 323.6 fix made games 78% longer. Above the plan's initial target of 100, well below its preferred 1,000. |

**Part I is now closed.** Section 7 was its one real hole and it is filled,
in pure stdlib, in a new `learning/` package that may read observations and
actions but never `RiftboundState` -- an encoder with state access would hand
a model information the interface does not grant, and no leakage test would
catch it, because the leak would be in the encoder.

Writing it paid for itself immediately. The encoder needs every action's
`instance_id` to resolve to something in the observation, which turned a
silent gap into a hard failure: "look at the top 3 of your Main Deck" raised
`ChooseTarget` actions over cards the observation exposed nowhere, so every
agent -- and every human at the frontend -- had been choosing blind. That is
a third discovery method alongside reading the rules and fuzzing them:
requiring the interface to be *sufficient*, not merely tight.

---

## Part II — Agents, matches, data (sections 12-15)

| § | Item | Status | Detail |
| --- | --- | --- | --- |
| 12 | Baseline agents | **done** | `RandomAgent`, `GreedyAgent`, `ISMCTSAgent`, and now `AggroAgent` / `ConservativeAgent` / `ObjectiveAgent` in `agents/styles.py`. They diverge from Greedy on 54-55% of decisions and from each other on 39-73%, at win rates against Random of 0.933-0.967 against Greedy's 0.933 -- diversity without a weak pool member. Getting there took two discarded designs; see below. |
| 13 | Match runner | **partial** | `analysis/benchmark.py::duel` and `analysis/batch.py` run matches and aggregate; `Replay` carries actions and a final state hash. No worker-pool parallelism — everything is single-process. |
| 14 | **Trajectory format** | **done** | `learning/trajectory.py`: gzipped JSONL, one provenance header, one record per decision carrying the observation, the legal-index mask, the action index, the reward and optional policy/value, and a closing result record. 102 bytes per decision measured on a real 624-decision game, against 1,215 for the raw observation. A test replays a recorded file back through a fresh engine and matches the final state hash, which is the only real proof a trajectory is a record of what happened. |
| 15 | Reward function | **done** | `returns()` is win 1.0 / loss 0.0 / draw 0.5 — an affine remap of the plan's +1/-1/0. No shaping, which is what the plan asks for. |

### What building section 12 turned up about the evaluator

The three style agents were built twice and thrown away twice before the
version that shipped, and the reason is worth more than the agents.

**Attempt 1 — re-weighted evaluators.** `GreedyAgent` under a different weight
vector. It changed 4.8% of decisions for aggro, 0.9% for conservative, 0.3%
for objective. Weighting hard enough to diverge cost strength: an objective
agent with the board features zeroed diverged on 78% of decisions and fell
from 0.93 to 0.62 against Random.

**Attempt 2 — feature tie-breaks.** Measuring why produced the number that
matters: **77.4% of decisions are exact ties at the top of the evaluator, mean
tie size 4.2.** `GreedyAgent` is playing randomly on three quarters of its
decisions. Ranking the tied set by a style-specific function of the resulting
position's features discriminated on **0 of 816 plateaus**.

That is structural, not unlucky. `Model.score` is *linear* in the 13 features,
so two actions scoring exactly equal got there by producing exactly equal
features. Checked directly: on 398 plateaus, **100% consisted of actions whose
resulting positions had identical feature vectors.** The evaluator is blind
between 4.2 actions, three quarters of the time, and no re-weighting can fix
it — it is a property of the feature set.

**This is the strongest argument in the repository for section 16.** The plan
wants a `StateEncoder` producing object tokens rather than a handful of
scalars, and the concrete cost of not having one is now measured: the current
13 features cannot distinguish three quarters of the decisions the engine
poses. A test (`test_a_feature_tie_break_could_not_have_worked`) fails if a
future evaluator ever separates a plateau, which is the signal that the
feature set has become fine enough to matter.

**What shipped** is a preference over *action kinds* applied among the tied
actions — play a unit, move to a battlefield, channel a rune, decline — which
needs no feature the evaluator lacks and costs no strength, because every
action it chooses among is one the evaluator called equal.

---

## Part III — The model (sections 16-22)

This block is **absent in its entirety**, and it is where the plan and this
repository diverge hardest.

| § | Item | Status |
| --- | --- | --- |
| 16 | `StateEncoder` producing object tokens | **absent** — `features()` returns 13 scalars, not a token sequence, and section 12's work above measured what that costs: those 13 scalars are identical across every action in 100% of the evaluator's ties, which are 77% of all decisions |
| 17 | Embedding + transformer + policy/value heads | **absent** |
| 18 | Policy scored over legal actions | **absent** — ISMCTS has an unused `prior` hook, defaulted off |
| 19 | Supervised bootstrap | **absent** (the plan marks it skippable) |
| 20 | Baseline-generated games for pretraining | **done** — `cli.py generate --games N` writes a gzipped trajectory dataset from any pairing of the five baseline agents, seats alternating by game index, with the pairing recorded in the file. ~22 KB per game. Nothing *consumes* it yet, because sections 16-18 are blocked on the dependency decision |
| 21 | Self-play | **partial** — `analysis/self_play_loop.py` runs generations and saves *weights*; the per-decision trajectory format now exists (section 14) but the loop does not yet write one |
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
| 27 | Fixed evaluation scenarios | **done** | `analysis/scenarios/`: 8 hand-built positions with cited rationales, graded by `python -m analysis.scenarios --agent all`. Random scores 0.38, Greedy 0.62, the styles 0.38-0.50 — it discriminates and nobody passes it. The suite is itself tested: every position passes `invariants.check`, every accepted answer is legal, and every scenario rejects at least one legal action. |
| 28 | **No-cheating tests** | **done** | `tests/test_no_cheating.py`, added for this evaluation. It found a leak on its first run — in the *opposite* direction: 128.4 grants a facedown card's face to its controller, and the observation showed it to nobody. |
| 29 | Logging | **partial** | The analysis tools print their metrics; only the league state is persisted. No per-run metrics file, no illegal-action counter in a log (it is 0, asserted by tests). |
| 30 | Configuration files | **absent** | CLI flags only; no `ai/config/*.yaml`. Note YAML itself is a dependency; JSON would do. |
| 31 | Unified CLI (`ai validate`, `ai train`, …) | **done** | `cli.py` -- validate / simulate / benchmark / evaluate / generate / fit / selfplay / play, each delegating to the module that already owned the work. `train` exits 2 with the reason rather than printing a stub that looks like it worked; a test asserts that it does. |
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
