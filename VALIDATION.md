# Large-scale simulator validation

`TCG_AI_BUILD.md` section 10 asks for at least 10,000 automated matches
checked against four failure modes, and section 39 makes passing that run an
acceptance criterion. This is the record of that run.

Reproduce with:

    .venv/bin/python -m analysis.validate --games 10000 --check-every 20

Seeds are the game index, so the run is reproducible exactly. Agents
alternate seats by game, so neither policy is measured only on the play.

---

## Result — 10,000 games, clean

| Metric | Value |
| --- | --- |
| games | **10,000** |
| decisions | **1,521,459** |
| wall clock | 3,472.7 s (57.9 min) |
| throughput | **173 games/min**, 438 decisions/s |
| mean branching factor | 4.7 legal actions per decision |
| mean game length | 14.2 turns |

### The four failure modes

| Failure mode | Count |
| --- | --- |
| crashes | **0** |
| impossible states | **0** |
| unresolved games (3,000-action cap) | **0** |
| illegal actions | **0** |

Section 29 requires the illegal-action count to stay at 0. Over 1.52 million
decisions it did. That is checked twice per decision, not assumed: the runner
rejects an empty legal-action set in a non-terminal state, and separately
asserts that the action each agent returns is a member of `legal_actions()`.

---

## What "impossible states = 0" does and does not cover

**Does.** `engine/invariants.py` checks 12 structural truths, each citing its
rule: one zone per card (107-108), location and zone agreement, attachment
(718-719), designations (464 / 466.7.a), tokens (186), Stun (423), Hidden
(107.3 / 323.7 / 811), Control (190), resources (163), points (194), chain
(329), and card conservation.

**Does not.** In the headline run they were checked on the **final state of
1 game in 20** — 500 states, not 1.52 million. Checking after every action of
every game is roughly 3x slower and would have pushed a one-hour run past
three.

That sampling is a real limitation and worth stating rather than glossing.
Two things bound it:

1. **A separate deep run** checks after *every action* of every sampled game.
   See below.
2. **The end-of-game check is not weak.** It found the one violation this
   session's work produced — a stranded facedown card on the last action of a
   299-step game, where cleanup step 5 legitimately never runs because
   winning is step 1 (323.1). That was a bug in the *invariant*, not the
   engine, and only a large run surfaced it.

### Deep run — invariants after every action

    .venv/bin/python -m analysis.validate --games 2000 --check-every 4 --deep

| Metric | Value |
| --- | --- |
| games | 2,000 |
| games checked after **every action** | 500 |
| impossible states | **0** |
| crashes / unresolved / illegal | **0 / 0 / 0** |

Together with the earlier 60-game random fuzz (15,824 actions, every action
checked, zero violations), the structural claim rests on continuous checking
across roughly a hundred thousand actions, plus end-state checking across
another 500 whole games.

---

## Performance against the plan's targets

Section 11 sets an initial target of **≥100 complete headless games/minute**
and a preferred later target of **≥1,000**.

- Initial target: **met** — 173 games/min.
- Preferred target: **not met**, and it is about 6x away.

Two things about that number are worth knowing before anyone optimises it:

- It is measured with a **Greedy agent on one side**, which clones the state
  once per legal action to score it. A pure Random-vs-Random run is far
  faster; the mixed figure is the honest one because it is what the analysis
  tools actually run.
- The engine already took a **3.8x speedup** this project, from replacing
  `copy.deepcopy` with a hand-written `__deepcopy__` after profiling showed
  **89% of runtime inside it** — 7.8 million object copies for 1,844 clones.
  The remaining cost is spread, not concentrated, so the next 6x is real work
  rather than one more hot spot.

The plan's rule 40.8 is "optimize correctness before performance", and with
6% of cards scripted (see `CORRECTNESS.md`), correctness is still where the
work belongs.

---

## What this run does **not** establish

Stated plainly, because a large clean number invites over-reading:

- It does not show the **rules are right**. It shows the machine never
  reaches a structurally impossible position. A card resolving the wrong way
  and leaving a legal board behind is invisible here. `CORRECTNESS.md` is the
  document for that question.
- It does not cover **card abilities**, because 495 of 526 playable cards
  have none wired up. The run exercises costs, stats, movement, combat,
  scoring, the chain and showdowns.
- It uses **two decks**. A third exists; the two real Milestone 1 decklists
  have not arrived yet. Deck diversity would widen the state distribution
  the run visits.
- It uses **Random and Greedy** policies. ISMCTS visits different positions;
  it is far too slow for 10,000 games and is covered by the benchmark suite
  instead.
