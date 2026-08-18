# Training the agent, and knowing whether it worked

Two questions, and they need different machinery:

1. **How do we see improvement?** → a frozen gauntlet, Elo, and a league that
   keeps every generation. `analysis/rate.py`.
2. **What do we adjust to train over and over?** → the seven levers at the
   bottom, in rough order of how much they are currently costing us.

None of the method here is invented. Elo on a logistic scale and the
Sequential Probability Ratio Test are what computer-chess testing (Stockfish's
fishtest) has used for years; a frozen reference pool and a league of past
selves are the standard answers to self-play's failure modes.

---

## Part 1 — Seeing improvement

### The trap: "beat the previous generation" cannot answer the question

Strength is not transitive. A beats B, B beats C, C beats A is an ordinary
result in games. A loop gated only on the incumbent will walk that circle for
ten generations and report ten improvements, every one of them real, and end
up no stronger than it started.

The fix is a **frozen gauntlet**: a fixed set of opponents that never changes,
so every generation's score is measured on the same scale.

```sh
.venv/bin/python analysis/rate.py --table      # every generation vs the gauntlet
```

```
| opponent | score | W-L-D | Elo | 95% interval |
| --- | --- | --- | --- | --- |
| random | 0.958 | 23-1-0 | +545 | +343 to +1200 |
| gen0   | 0.625 | 15-9-0 |  +89 |  -48 to +262 |
```

The random agent anchors the scale, because it is the one opponent that cannot
drift. Every promoted generation joins the gauntlet, so the table grows a
column per generation and the left-hand columns stay comparable forever.

### The league: stop overwriting the past

The loop used to write one weights file and overwrite it on promotion. That
made the ladder impossible to build — once generation 3 was installed,
generation 2 no longer existed to play against. Every promoted generation is
now kept in `analysis/league/gen_NNN.json`. It costs a few hundred bytes and
it is the difference between "we improved" and "we changed".

### Sample size: why the old gate could never fire

A fixed-N test needs roughly `(1.96 × 0.5 / (p − 0.5))²` games:

| true score | Elo | games to resolve at 95% |
| --- | --- | --- |
| 0.55 | +35 | **385** |
| 0.60 | +70 | 97 |
| 0.65 | +107 | 43 |

The old gate was 40 games, which can only resolve an edge of ~0.65 — far
larger than any real improvement. So a genuine +35 Elo generation would have
been rejected every time, and the loop stopped dead on its first rejection.

**SPRT** fixes this. It watches the result arrive and stops the moment the
evidence is decisive, spending the long runs only on genuinely marginal
candidates:

```sh
.venv/bin/python analysis/self_play_loop.py --generations 5 --field \
    --games 300 --bench 400 --elo0 0 --elo1 20
```

`--bench` is now a *maximum*, not a target. `--elo0 0 --elo1 20` says "promote
only what is at least ~20 Elo stronger". Note that SPRT with narrow bounds is
deliberately patient even about disasters: an 0-40 candidate needs ~50 games
to reject, because both hypotheses predict roughly even play and a 0-40 result
is unlikely under either. Fifty is still much cheaper than four hundred.

### Every measurement is confounded if the harness is biased

Before trusting any of the above: the identical model played against itself
must score 0.500. `duel` swaps seats every other game and gives both agents
the same shuffles, but agent A's tie-breaking RNG is seeded from the game seed
while B's is offset — a correlation worth ruling out rather than assuming
away. Run the null test whenever the harness changes:

```sh
.venv/bin/python analysis/nulltest.py --games 400
```

Measured: **0.480 (192-208-0) over 400 games, 95% interval 0.431–0.529**, and
again after the engine was rewritten for speed: **0.480 (144-156) over 300**.
Both intervals contain 0.500, so the harness is unbiased at this resolution.

Note what this test does *not* catch: it swaps seats, so it is blind to the
deck imbalance below. An unbiased harness and a low-variance one are different
properties, and this project had the first without the second.

---

## Part 1b — Does it learn at all? A test with a known answer

Before any of the levers below are worth pulling, one question has to be
settled: **does the training loop learn, or does it just move numbers?**

Every result had been of the form "the candidate scored 0.51, the interval
straddles even" — equally consistent with a learner that works weakly and one
that is broken. The problem is there is no ground truth: nobody knows the best
weight vector, so "did it get closer" is unanswerable.

`analysis/learning_check.py` manufactures a ground truth. It takes weights
known to play well, **damages one on purpose**, and asks whether training
climbs back:

```sh
.venv/bin/python analysis/learning_check.py --feature point_diff --how flip
```

| match | score | W-L-D | Elo | 95% interval |
| --- | --- | --- | --- | --- |
| damaged vs healthy | 0.330 | 66-134-0 | −123 | 0.265–0.395 |
| **trained vs damaged** | **0.765** | **153-47-0** | **+205** | **0.706–0.824** |

**The loop learns.** Flipping `point_diff` costs 123 Elo, and 400 SPSA
iterations recover 205 of it. That is not a marginal result that could be
noise — it is the machinery demonstrably working.

Which also settles a different question. Two SPSA runs on the *healthy*
weights produced candidates that validated at 0.487 and 0.485 — no
improvement. Before this test that was ambiguous: broken learner, or weights
already near a local optimum? Now it is the second. The learner can climb; it
just has nowhere obvious to climb *to* from the hand-set prior.

Run this whenever the training path changes. A learner that cannot escape a
hole someone dug for it will certainly not find improvements nobody knows
about, and any weak positive result from it is noise.

## Part 2 — What to adjust, in order of what it is costing

### 1. Deck variety — the biggest lever, and it is capped by card scripting

Training on one matchup teaches the matchup. The weights have no way to
separate "this is strong in Riftbound" from "this is strong against
Volibear", and only the first transfers.

`--field` rotates through every deck in `decks/`, giving *n²* matchups.
Today that is 3 decks → 9 matchups, up from 1.

The cap is card text. `build_decks.py --count 12 --min-implemented 0.85`
finds only three legends that can field a deck where ≥85% of the cards
actually execute; the rest come in at 52–68%, and training on a deck where a
third of the cards are inert teaches a different game than the printed one.

**So scripting more cards is not cosmetic — it is what widens the training
field.** That is the single highest-value thing to work on.

> Decks on disk are **fixtures, not outputs.** Replays are recorded against
> exact decklists, so `build_decks.py` refuses to overwrite an existing deck
> without `--force`. This is not hypothetical: improving Equipment changed
> which cards counted as implemented, which changed the builder's ordering,
> which changed the starter decks under the committed replays. The harness
> caught it; a test now pins it.

### 2. Throughput — everything downstream is rate-limited by this

Every measurement above is priced in games, so speed is not a comfort, it is
how many experiments fit in an afternoon. Two profiling passes moved it:

| agent | before | after | |
| --- | --- | --- | --- |
| greedy | 0.66 games/sec | **2.45** | 3.7× |
| ismcts(60) | 0.03 games/sec | **0.05** | 1.6× |

**89% of a greedy game was `copy.deepcopy`** — 7.8 million object copies for
1,844 clones, because search clones the state once per legal action and the
generic deepcopy walked every card, every zone list and every string in the
log. Every mutable part of the state holds only scalars, strings, tuples and
ints, so `RiftboundState.__deepcopy__` now copies each in one shallow pass and
shares the immutable card database. Written as `__deepcopy__` rather than a
`clone()` method so agents get the fast path without changing.

Then legal-action generation rose to the top, and inside it `equip_ability`
was running **34,718 regex parses across six searches** — re-parsing printed
card text every time a card was asked for its abilities. Memoized on the card
face.

Hand-written cloning is exactly the code that silently shares one mutable
field and corrupts every search that touches it, so `tests/test_cloning.py`
mutates every mutable field of a clone and asserts the original is untouched,
checks the RNG stream is not shared and the database *is*, and asserts every
`CardRef` field is of an immutable type. The committed replays hash every
state in two full games and pass unchanged.

Still available: parallelism across cores (games are independent), and the
`_window_actions` loop, which still scans every card in the game rather than
only the ones on the board.

### 3. Measure in mirrors — the decks are louder than the agents

This is the one that had been quietly ruining every comparison.

Run identical greedy agents against each other with agent A pinned to seat 0
and it scores **0.142 (17-103)**. Flip which deck sits in seat 0 and the score
flips with it — 0.087 one way, 0.787 the other. It is not a seat effect. It is
the decks: **volibear_body_fury beats jinx_chaos_fury about 85-15 with the
same agent on both sides.**

Now compare the sizes. A genuinely better agent is worth 0.52–0.55. A luckier
deck assignment is worth 0.85. Any test that lets deck assignment vary is
measuring mostly decks, and needs an enormous sample before the agent signal
rises out of it — which is a large part of why every comparison in this
project has come back "the interval straddles 0.5".

**So agent-vs-agent comparisons run in mirrors**: each deck against itself,
both sides. The deck cancels exactly and what is left is how the two agents
play. It is the same move as fixing the opening book in engine testing —
delete the variance you are not trying to measure.

Cross-deck play is still right for *generating* training data, where variety
is the point. It is only the comparisons that go to mirrors.

Two smaller measurement bugs found alongside it, both now fixed and tested:
sequential (one-game-at-a-time) tests were not swapping seats at all, because
the rotation was keyed on a loop index that was always 0; and `duel` now takes
a `start_index` so those callers can rotate properly.

### 4. Patience — one bad generation is not the end of the run

Training is noisy. The loop used to stop on the first non-promotion, which is
why it never got past generation 1. `--patience 3` lets it retry with fresh
data before giving up.

### 5. Report what moved, not just whether it moved

Each promotion now records its largest weight changes. A number with no story
is unauditable; a feature flipping sign is visible immediately, which is how
the `hand_diff` artefact was caught the first time (random agents hoard cards
they cannot play, so fitting on random data made holding cards look bad).

### 6. The model's ceiling is low, and that is fine for now

The evaluation is logistic regression over 13 features. It will plateau, and
no amount of iteration gets past that. When it does plateau, the options are
more features, feature crosses, or a small non-linear model — but the plateau
should be *measured* first, not assumed.

### 7. Discarded games bias the data

`play_and_sample` throws away any game that hits `MAX_STEPS`. Those are the
grindy, stalled positions — exactly the ones where evaluation matters most.
Worth labelling them as draws instead of dropping them.

### 8. Exploration is already there — keep it

`--epsilon 0.15` while generating data. Without it the agent only ever sees
the lines it already prefers, and learns nothing about the alternatives.

---

## The rule that does not change

**Prediction is not control.** The first version of this loop accepted a
candidate on held-out Brier: it predicted better (0.1815 → 0.1666, accuracy
0.687 → 0.727) and then *lost* 14-26 when it actually played. Every gate here
is a game-playing gate. A model that only predicts better is discarded, loudly,
with its numbers written to `analysis/generations.json`.

The same standard applies to hand-added features. `takeover_edge` is a sound
idea that measured at 0.540 (162-138, interval 0.484–0.596) against the
identical agent with that one weight zeroed — consistent with a small gain,
not established, and reported that way rather than assumed.
