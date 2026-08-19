# Large-scale simulator validation

`TCG_AI_BUILD.md` section 10 asks for at least 10,000 automated matches
checked against four failure modes, and section 39 makes passing that run an
acceptance criterion. This is the record of that run.

Reproduce with:

    .venv/bin/python -m analysis.validate --games 10000 --check-every 20

Seeds are the game index, so the run is reproducible exactly. Agents
alternate seats by game, so neither policy is measured only on the play.
Every run prints its provenance stamp first (`engine/versions.py`), because a
number without one cannot be compared to a later number.

---

## Result — 10,000 games, clean

Engine **0.7.0**, card pool `0c977ae683a0`, rules `CR-v1.4-Vendetta`.

| Metric | Value |
| --- | --- |
| games | **10,000** |
| decisions | **1,946,760** |
| wall clock | 5,592 s (93 min) |
| throughput | **107 games/min**, 348 decisions/s |
| mean branching factor | 5.0 legal actions per decision |
| mean game length | 18.1 turns |

### The four failure modes

| Failure mode | Count |
| --- | --- |
| crashes | **0** |
| impossible states | **0** |
| unresolved games (3,000-action cap) | **0** |
| illegal actions | **0** |

Section 29 requires the illegal-action count to stay at 0. Over 1.95 million
decisions it did. That is checked twice per decision, not assumed: the runner
rejects an empty legal-action set in a non-terminal state, and separately
asserts that the action each agent returns is a member of `legal_actions()`.

### This run replaced an earlier one, and the reason matters

An earlier 10,000-game run on engine 0.4.0 was also clean: 1,521,459
decisions, zeros across the board, 173 games/min, **14.2** turns per game.

It was clean and it was measuring the wrong game. The 300- and 700-series
rules audits that followed found nine live bugs, one of which (323.6, control
without a garrison) moved mean game length from 14.2 turns to 18.1 here and
to 25.3 in a Greedy-first sample. Throughput fell from 173 to 107 games/min
for the same reason — the games got longer, not the engine slower.

**A clean validation run says the machine is sound. It says nothing about
whether the rules are right.** That distinction is the whole of
`CORRECTNESS.md`, and this pair of runs is the evidence for it.

---

## What "impossible states = 0" does and does not cover

**Does.** `engine/invariants.py` checks 13 structural truths, each citing its
rule: one zone per card (107-108), location and zone agreement, attachment
(718-719), designations (464 / 466.7.a), tokens (186), Stun (423), Buff
counters (426 / 702.3), Hidden (107.3 / 323.7 / 811), Control (190),
resources (163), points (194), chain (329), and card conservation.

**Does not.** In the headline run they were checked on the **final state of
1 game in 20** — 500 states, not 1.95 million. Checking after every action of
every game is roughly 3x slower and would have pushed a 93-minute run past
four hours.

That sampling is a real limitation and worth stating rather than glossing.
Two things bound it:

1. **A separate deep run** checks after *every action* of every sampled game.
   See below.
2. **The end-of-game check is not weak.** It found the one violation this
   work produced — a stranded facedown card on the last action of a 299-step
   game, where cleanup step 5 legitimately never runs because winning is
   step 1 (323.1). That was a bug in the *invariant*, not the engine, and
   only a large run surfaced it.

### Deep run — invariants after every action

    .venv/bin/python -m analysis.validate --games 1000 --check-every 2 --deep

| Metric | Value |
| --- | --- |
| games | 1,000 |
| games checked after **every action** | 500 |
| impossible states | **0** |
| crashes / unresolved / illegal | **0 / 0 / 0** |

The invariant checker has now caught three real defects across the project —
719.5 attachment survival, the stranded facedown card, and the Warmog's buff
landing on a gear — none of which reading the rules had found first. The
converse also holds: 719.3.a was found by reading and is *unreachable* by
fuzzing at this card coverage. Neither method subsumes the other.

---

## Performance against the plan's targets

Section 11 sets an initial target of **≥100 complete headless games/minute**
and a preferred later target of **≥1,000**.

- Initial target: **met** — 107 games/min.
- Preferred target: **not met**, and it is about 9x away.

Three things about that number before anyone optimises it:

- It is measured with a **Greedy agent on one side**, which clones the state
  once per legal action to score it. Random-vs-Random is far faster; the
  mixed figure is the honest one because it is what the analysis tools run.
- The engine already took a **3.8x speedup**, from replacing `copy.deepcopy`
  with a hand-written `__deepcopy__` after profiling showed **89% of runtime
  inside it** — 7.8 million object copies for 1,844 clones. The remaining
  cost is spread, not concentrated.
- **Correct games are longer games.** Part of the drop from 173 to 107 is the
  rules getting more right, and that part should not be optimised away.

The plan's rule 40.8 is "optimize correctness before performance", and with
6% of cards scripted (see `CORRECTNESS.md`), correctness is still where the
work belongs.

---

## What this run does **not** establish

Stated plainly, because a large clean number invites over-reading:

- It does not show the **rules are right**. It shows the machine never
  reaches a structurally impossible position. The 0.4.0 run above was equally
  clean while battlefields were free to hold forever.
- It does not cover **card abilities**, because 495 of 526 playable cards
  have none wired up. The run exercises costs, stats, movement, combat,
  scoring, buffs, the chain and showdowns.
- It uses **two decks**. A third exists; the two real Milestone 1 decklists
  have not arrived yet.
- It uses **Random and Greedy** policies. ISMCTS visits different positions;
  it is far too slow for 10,000 games and is covered by the benchmark suite
  instead.
