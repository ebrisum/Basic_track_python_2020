# Scoring and reward

How the agent is told what "good" means, and why the reward and the heuristic
are deliberately different things.

## The reward is winning. Only winning.

`state.returns()` gives `1.0` for a win, `0.0` for a loss, `0.5` for a draw.
Nothing else. It is derived from rule **194.2** — points at or above the
Victory Score *and* strictly more than the opponent, checked in a cleanup — and
it does not change.

No points-per-turn bonus, no reward for board presence, no partial credit.

### Why not shape it

Shaping the reward optimises the proxy. Two concrete ways it would go wrong
here:

- **Points are not monotonically good.** The Final Point rule (**471.1.b**)
  turns a conquer into a wasted card draw unless you have scored every
  battlefield that turn. An agent paid per point learns to take the point that
  does nothing.
- **Board presence is not the goal.** An agent paid for units at battlefields
  learns to park units at battlefields, including when moving them there feeds
  a losing combat.

The Riftbound-specific version of the trap is worth stating plainly: the game
rewards *territory held at the right moment*, not territory held. A shaped
signal cannot tell those apart; the terminal signal can.

## The heuristic is a separate, measured thing

`analysis/evaluation.py` estimates the probability that a player will win from
a non-terminal position. Search needs a value for positions it cannot roll out
to the end; that is the only job.

Ten features, all **differences** between the two players:

| feature | why |
| --- | --- |
| `point_diff` | the win condition |
| `point_progress` | nonlinear — the Victory Score is a threshold, so the last points are worth more |
| `battlefield_diff` | control converts to Hold points every turn (469.2) |
| `unit_count_diff`, `might_diff` | the means of taking and keeping battlefields |
| `bf_presence_diff` | units standing where points come from |
| `hand_diff`, `rune_diff` | options and resources |
| `deck_diff` | burn-out risk (431) |
| `tempo` | ready units can still act |

### Two invariants

**Terminal positions return the true result.** The heuristic never overrides an
actual outcome.

**`evaluate(s, 0) + evaluate(s, 1) == 1`, exactly.** Every feature is a
difference, so the model is antisymmetric. Search relies on this — it lets a
value be negated for the opponent instead of recomputed — and it catches any
feature accidentally written from one player's point of view. It caught two
during development: `point_progress` was originally absolute (the two views
summed to 1.05), and a fitted bias term reintroduced the same problem, which is
why the bias is now pinned to zero.

Real seat advantage — **485.7** gives the player going second an extra rune —
belongs in an antisymmetric feature, not in a constant.

## The result that matters: prediction is not control

A fit improved held-out prediction and then **lost when it actually played**.

| | hand-set prior | fitted (220 games) |
| --- | --- | --- |
| Brier (lower better) | 0.1815 | **0.1666** |
| accuracy | 0.687 | **0.727** |
| **vs random, head-to-head** | **0.925** (37-3) | 0.825 (33-7) |
| **fitted vs prior, head-to-head** | — | **0.350** (14-26) |

95% interval on that last row is 0.202–0.498, so the loss is real, not noise.
Both greedy agents crush random (0.925 and 0.825 against a 0.525 random-vs-random
baseline), so the heuristic is doing real work — but the *better-calibrated*
model is the *worse player*.

Two causes, both worth remembering:

- **The labels come from random self-play.** The weights learn what correlates
  with winning among random agents, not what a player should steer toward.
  `hand_diff` was fitted **negative**, because random agents that cannot play
  their cards accumulate them. A greedy agent maximising that actively dumps
  its hand — it optimises the correlation and loses the game. This is the same
  Goodhart failure that shaping the reward would have caused, arriving through
  the back door.
- **Distribution shift.** A greedy agent immediately moves play off the
  random-play distribution the model was fitted on, into positions it has never
  scored.

### What changed because of it

The promotion gate moved. `fit_weights.py` now writes
`weights.candidate.json` and installs nothing; a candidate is promoted only
after `benchmark.py` shows it beating the incumbent head-to-head. Brier is a
useful diagnostic and a bad acceptance test.

The hand-set prior is what ships. The fitted candidate is kept in the repo as
evidence, not as the model.

Closing this properly needs the training data to come from the agents being
trained — iterated self-play — rather than from random rollouts.

## The weights are fitted, not asserted

`analysis/fit_weights.py` runs self-play, labels every sampled position with
"did this player go on to win", and fits by logistic regression (pure stdlib).

Two guards against self-deception:

- **Split by game, never by position.** Positions inside one game share a
  label, so a position-level split leaks the outcome across the boundary and
  reports a flattering score.
- **Nothing is written unless held-out Brier improves.** A fit that makes the
  heuristic worse is discarded, loudly.

## What the measurements actually say

`analysis/calibrate.py`, 120 fresh games, seeds disjoint from training:

| | hand-set prior | fitted (220 games) |
| --- | --- | --- |
| Brier (lower better; 0.25 = always guessing 0.5) | 0.1815 | **0.1666** |
| accuracy | 0.687 | **0.727** |
| early-game Brier | 0.2506 | 0.2580 |
| early-game accuracy | 0.511 | 0.550 |
| late-game Brier | 0.0733 | **0.0502** |

Read this alongside the head-to-head table above: the fitted model wins on
every prediction metric here and still loses the games.

**The honest headline: the early game is not predictable.** Early Brier is
*above* 0.25 for both models, meaning that in the opening the heuristic is
worse than simply saying "50/50". It earns its keep in the mid and late game
and nowhere else.

That is a real limit, not a tuning problem. Fitted on random self-play, the
opening genuinely does not determine the outcome, so there is no early signal
to learn. Two things would change it: stronger agents generating the data (so
early decisions actually matter), and features that capture potential rather
than present state — curve, domain consistency, reach.

Calibration after fitting is close to the diagonal — positions called 0.9+ win
93% of the time, 0.1–0.2 win 17% — which is what makes the number usable as a
search prior rather than just a ranking.

## Using it

`agents/greedy_agent.py` applies each legal action to a clone and keeps the
best-evaluated result. It exists as a measuring stick: if the heuristic is
worth anything, this beats random; if not, no amount of search on top will
save it. It does beat random decisively — **0.925 (37-3)** against a
0.525 random-vs-random baseline — which is the evidence that the heuristic
captures something real. `analysis/benchmark.py` runs the head-to-head, swapping seats and
sharing seeds so first-player advantage cannot be mistaken for skill, and
reports a 95% interval so a 55% result over 40 games is not read as evidence.

## The judgments a weighted sum cannot make

The repo owner raised a set of real tactical questions: deny the opponent's
point, but not at the cost of resources needed to score later; letting them
score is often fine; sweeping the board is good when behind; when to hold a
Reaction; when recycling a rune for power costs more than it gains.

**Most of these cannot be expressed by any linear evaluation**, and no amount
of weight-fitting will change that, because they are *conditional*:

| judgment | why a weighted sum fails |
| --- | --- |
| Sweep the board when behind, not when ahead | An interaction between two features. A sum has no product term. |
| Deny the point at 7, ignore it at 1 | Same action, opposite valuation depending on the score. |
| Hold a Reaction for something worth answering | A fact about the future, not the present position. |
| Recycle a rune now vs keep the resource | A tradeoff across turns, invisible in a single-position score. |

Search represents all four for free, because it plays the consequences out.
That is why `agents/ismcts.py` exists and why the evaluation's job is
deliberately small: score leaves, and let the tree find the tactics.

### The one part that *is* a feature

"How urgent is the opponent's threat" is a property of a position, so it
belongs in the evaluation. The first attempt made it a threshold at one point
from victory — which was wrong, as the repo owner pointed out: it says the
same thing about a player at 7 with an empty board as one at 7 holding
everything.

`victory_pressure` measures it in **turns, not points**: points still needed
divided by points per turn, where the rate is battlefields controlled, since
Hold scores one per controlled battlefield per turn (469.2).

| position | pressure |
| --- | --- |
| 7 points, no battlefields | 0.333 |
| 5 points, both battlefields | **0.400** |
| 7 points, both battlefields | 0.667 |
| 0 points, both battlefields | 0.200 |

Five points with the board is more urgent than seven without it, which is the
correct reading and is what a score-only threshold got wrong.

## The arithmetic the closed system allows

A 40-card deck fixed before the game, with trash, board, Champion Zone and
Legend Zone all public, means the opponent's hand is *bounded*, not guessed:

    unseen(opponent) = their decklist − everything of theirs publicly visible

`engine/knowledge.py` computes it, and the identity holds exactly — the unseen
pool equals their hand plus their deck on every position tested. From there:

- **`answer_risk`** — expected Reactions in each hand, deduced. Whether to
  *hold* a Reaction is a timing question for search; how likely the opponent is
  to *have* one is a property of the position, so it is a feature.
- **Draw odds** — hypergeometric, exact rather than estimated, because the
  population is a known 40 and the sample is a known number of draws.
- **Determinization** samples from exactly this pool, and a test asserts every
  sampled world is consistent with public information.

The assumption underneath is that both decklists are known. True for the
field-weighted matrix this project is built for; false in game one against an
unknown opponent. `KnownDecklists.OWN_ONLY` models the latter. See RQ-12.

## Hidden information: ISMCTS

Plain search would cheat by reading the opponent's hand. `agents/ismcts.py`
determinizes instead — each iteration samples one world consistent with what
the searching player can legally see (128), searches it, and shares statistics
across iterations keyed by action sequence. Averaged over many samples the
agent plans against the *distribution* of possible hands.

Statistics divide by **availability**, not visits, so an action legal in only
some determinizations is not punished for the iterations where it never
appeared. Leaves are scored with the evaluation rather than played out, which
is what makes the search affordable on turns that run hundreds of steps.

## The destination: no hand-set anything

The features and weights here are scaffolding for generation 0. They are not
meant to survive. `analysis/self_play_loop.py` is the mechanism that replaces
them:

```
generation N agent -> plays games -> fit weights on those outcomes
                   -> benchmark candidate vs incumbent
                   -> promote ONLY if the 95% interval clears even
```

Two properties make this honest rather than a treadmill:

- **Self-play data has no random-play artefacts.** The `hand_diff` weight came
  out negative when fitted on random games, because random agents accumulate
  cards they cannot play. An agent that plays properly does not generate that
  correlation, and the positions it visits are the positions it will face —
  closing the distribution shift by construction.
- **Every generation has to win to be promoted.** A generation that only
  predicts better is discarded, loudly, with its numbers written to
  `analysis/generations.json`.

Exploration (`--epsilon`) is on while generating data, because an agent that
only plays its preferred lines never learns what the alternatives were worth.

## Where this goes next

The heuristic is the value function an ISMCTS agent will need. Two things make
it usable there: it is antisymmetric (negate, don't recompute), and cloning a
state is cheap — the card database is now shared by reference rather than deep
copied, which took a clone from 16.1 ms to 2.15 ms.

The loop to close is the standard one: better agents produce better training
data, which produces a better heuristic, which produces better agents. It only
works if each turn of the loop is measured against held-out games, which is
what `calibrate.py` and `benchmark.py` are for.
