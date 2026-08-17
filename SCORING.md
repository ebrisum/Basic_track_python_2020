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
save it. `analysis/benchmark.py` runs the head-to-head, swapping seats and
sharing seeds so first-player advantage cannot be mistaken for skill, and
reports a 95% interval so a 55% result over 40 games is not read as evidence.

## Where this goes next

The heuristic is the value function an ISMCTS agent will need. Two things make
it usable there: it is antisymmetric (negate, don't recompute), and cloning a
state is cheap — the card database is now shared by reference rather than deep
copied, which took a clone from 16.1 ms to 2.15 ms.

The loop to close is the standard one: better agents produce better training
data, which produces a better heuristic, which produces better agents. It only
works if each turn of the loop is measured against held-out games, which is
what `calibrate.py` and `benchmark.py` are for.
