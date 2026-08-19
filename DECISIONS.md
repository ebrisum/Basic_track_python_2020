# Decisions

One line each, with reasoning. Architectural choices fixed by the project
brief are not repeated here -- only choices made on top of it.

## Session 1

- **Project lives at the repo root, not in a nested `riftbound-sim/`** -- the
  repo *is* the project; a single-child top directory buys nothing.
- **Python 3.12 via a local `.venv`** -- the container's default `python3` is
  3.11 and its 3.12 is PEP-668 externally-managed, so `uv venv --python 3.12`
  is the only way to hit the specified version with pytest available.
- **`pytest` is the only non-stdlib dependency** -- as sanctioned by the brief.
  `data/fetch.py` uses `urllib` rather than `requests` to keep it that way.
- **Fetching is split from normalizing** (`data/fetch.py` vs
  `data/normalize.py`) -- caching is a network concern with retry/provenance
  needs; normalizing is a pure function over `data/raw/`. Split means
  normalize is re-runnable offline and unit-testable.
- **Every cached file gets a `_meta.json` sibling** recording URL, status,
  byte count, and sha256 -- so a later re-fetch can be diffed against exactly
  what the current `cards.json` was built from, and a silently-changed
  upstream is detectable.
- **Source precedence is data, not code** (`sources.PRECEDENCE`) -- the merge
  is shape-agnostic, so re-ranking sources or adding a fourth is a one-line
  change plus one adapter.
- **Per-field provenance is stored on every canonical card** -- when a
  simulation result looks wrong, the first question is "where did this card's
  stats come from", and that must be answerable without re-fetching.
- **Omission is not disagreement** -- a source that lacks a field defers to one
  that has it, and this is not logged as a discrepancy. Otherwise
  `DISCREPANCIES.md` fills with noise and the real conflicts get lost.
- **Source adapters raise `NotImplementedError` rather than guessing field
  names** -- a guessed mapping fails silently, producing a plausible
  `cards.json` full of `None`s instead of an error. See BLOCKER-1.

## Session 2 -- rules-agnostic infrastructure

Built while BLOCKER-1 stands. Everything here is independent of Riftbound's
rules and card pool, so none of it is blocked and none of it presumes a rule.

- **`state.current_player` added to the agent-facing interface** -- NEEDS
  APPROVAL. The brief lists five calls; a replay cannot verify turn or
  priority order without knowing whose move it is, and OpenSpiel's interface
  has `current_player()` too. Flagged rather than assumed silently.
- **State hashing is derived from the frozen interface, not added to it** --
  `replay.state_hash()` hashes both players' observations plus terminal state
  and returns. Keeps the agent-facing contract at exactly the specified calls
  and forces both players' hidden information into the hash, so opponent-side
  drift cannot slip past.
- **Replays record actions by `repr()`** -- readable and hand-diffable, which
  matters because a human reads these when one breaks. Makes `repr()`
  load-bearing: changing it invalidates replays containing that action. That
  is intended -- it forces the change to be noticed.
- **Action reprs must be unique within a state** -- the replay runner rejects
  ambiguity rather than picking a match. Silently choosing would make a
  replay pass while replaying a different game.
- **`Observation.to_canonical_bytes()` rather than hashing objects** --
  Python's `hash()` is salted per process, so it cannot back a stored replay.
- **Toy game lives in `tests/fixtures/`, not in the package** -- it exists to
  prove the harness and validate the interface, and must never be importable
  by `engine/`, `cards/`, or `analysis/`.
- **Batch seeds game `i` with `base_seed + i` and rebuilds agents per game** --
  any single game re-runs standalone from its own seed, so an outlier is
  investigable without replaying the whole batch.
- **`play_game` reads point differential via `getattr(state, "scores", None)`**
  -- optional, since scoring is not part of the frozen interface. Revisit
  once the Riftbound state exists and its scoring shape is known.
- **Runaway games raise rather than hang** -- a `max_steps` ceiling turns a
  rules loop into a named failure instead of a wedged batch.

## Session 3 -- real data and rules

- **Substituted reachable sources for the brief's four blocked ones** -- npm
  `riftbound-tools`, github `apitcg/riftbound-tcg-data`, and github
  `ChristianIvicevic/riftboundfaq` (which mirrors the official Core Rules
  PDFs). The blocked URLs stay recorded in `sources.py` so the substitution is
  visible and reversible.
- **Numerics come only from apitcg** -- the npm source's `might` agrees with
  the real might 0.6% of the time. Not mapped at all rather than mapped
  wrongly. See RQ-1.
- **apitcg outranks riftbound_tools** -- it carries power cost and the
  Champion/Signature/Token distinction, both of which the engine needs and the
  npm source does not have.
- **Canonical id is `<SETCODE>-<NUMBER>`, variant suffix preserved** --
  "OGN-007A" stays distinct from "OGN-007"; alternate arts are distinct
  printings and must not silently collapse.
- **Boolean card fields are tri-state (`bool | None`)** -- `None` means "this
  source has no opinion", `False` means "this source says no". Defaulting to
  False generated 527 phantom conflicts.
- **Coverage is computed per card type** -- spells have no Might and Colorless
  cards no domain, so a flat required-field list reported correct data as
  missing and buried the real gaps.
- **The rules PDFs are not committed; the extracted text is** -- they are
  24-43 MB each and mostly images. `data/raw/core_rules/CR-v1.4.txt` is the
  citable artefact, with its sha256 in `_meta.json`.
- **DSL primitives are the game's own Game Actions (rules 413-444), adopted
  verbatim rather than designed** -- rule 411.4 keys triggers to game actions,
  so any vocabulary that is not one-to-one with them cannot express "when you
  move an enemy unit" correctly. The rules define 32; the brief predicted
  30-40.
- **pypdf added as a dev-only dependency** -- used once, by `data/fetch.py`,
  to extract rules text. Nothing in `engine/` or `cards/` imports it and
  simulation never reads a PDF. Flagged because the brief requires asking
  before adding dependencies.

## Session 4 -- playable engine and frontend

- **Engine covers structural rules only; card text is a separate layer** --
  setup, turns, resources, movement, combat and scoring are implemented and
  cited; effects wait on the DSL interpreter. Lets the game be played and
  tested now without pretending card text works. See RQ-5.
- **Mechanical keywords live in the engine, not the DSL** -- Assault, Tank,
  Backline and Ganking change movement, damage assignment and stats rather
  than producing effects, so they need no interpreter.
- **Keyword parameters are recovered from reminder text when absent** --
  Chemtech Enforcer prints "ASSAULT (+2 Might...)" with no number on the
  keyword. 807.1.b says Assault is "Assault [X]", so the value is
  authoritative wherever it appears.
- **`allow_concede` defaults off** -- conceding is a real rule (649) but a
  random policy that concedes makes statistics meaningless. On for interactive
  play, off for batches. See RQ-9.
- **Frontend is a thin client over the engine** -- it renders
  `observation(player)` and posts actions by their `repr()`, resolved against
  `legal_actions()`. No rules logic in the browser, so the UI physically
  cannot desync from the engine or submit an illegal move.
- **stdlib `http.server`, no web framework** -- keeps the no-dependency rule.
  One game in memory; this is a local play tool, not a service.
- **Hot-seat rather than two sessions** -- the server still refuses to reveal
  the other player's hand, so the privacy rules (128) are exercised properly
  rather than bypassed for convenience.
- **Cards missing either half of their (energy, power) cost are dropped at
  load** -- the engine cannot price them, and a card that cannot be paid for
  cannot be played. 441 of 908 survive; the rest are mostly UNL (RQ-1).
- **Starter decks are generated, not hand-written** -- `decks/build_decks.py`
  builds legal decks from the real pool so the game is playable before the
  Milestone 1 lists arrive. Each deck gets a distinct battlefield trio.

## Session 5 -- DSL interpreter and playfield

- **Effects that need a decision return a `ChoiceRequest` rather than acting**
  -- the engine parks the effect, offers the options through
  `legal_actions()`, and resumes on the answer. Keeps every player decision
  inside the frozen interface instead of becoming a side channel an agent
  cannot see.
- **`ChoiceRequest` is skipped when there is exactly one option** -- offering a
  "choice" of one inflates the action space for no decision.
- **Card scripts are data, with the printed wording attached** -- `Ability.text`
  carries the sentence it implements, so a failing test names the card text
  rather than a class.
- **Scripts declare `complete`** -- a card whose script covers only part of its
  text stays flagged as inert in the UI, with `note` saying which part is
  missing. Partial implementation must not read as full implementation.
- **The replay state hash is derived from the observation, so enriching the
  observation invalidates replays even when no rule changed.** That happened
  twice this session: once for a real rules change (card text started
  executing) and once for a pure presentation change (exposing trash, legend
  and champion zones). The harness cannot tell them apart, so the justification
  has to come from the commit message. Worth revisiting if replay churn becomes
  noisy -- a hash over game state rather than observation would separate the
  two, at the cost of no longer proving hidden information is stable.
- **Card art comes from two different sources on purpose** -- `image_url` from
  Riot's CDN (the real card face) via the npm source, `image_alt` from
  TCGPlayer via apitcg. Different fields from different sources means they can
  never conflict in the merge.
- **The browser loads card art directly from those CDNs** -- the images are not
  proxied or cached locally, so the frontend needs internet access to show
  them and degrades to a text card when a fetch fails.

## Session 6 -- the Chain and the playmat

- **The Chain lives in `engine/chain.py`, the state machine in `state.py`** --
  the data structures and the pure timing predicates are separable and
  unit-testable without a game; only the mutation needs a state.
- **One `_window_actions` builder serves Main Phase, Chain and Showdown** --
  the rules make these the same window with different timing filters (310),
  so three builders would have drifted apart.
- **An ability's printed timing beats its card's** -- the rune-seal cycle
  reads "Exhaust: REACTION - ADD ..." so the ability is legal in a Closed
  state even though the gear itself is not.
- **`_normalize_window` keeps `phase` consistent with the chain and showdown**
  -- a chain emptying through an unusual path otherwise left the turn Closed
  with nothing on it, a dead position whose only action was to pass forever.
  Random play found it within a dozen turns.
- **Focus does not pass for triggers or Add abilities (346.1)** -- tracked with
  `ChainItem.from_trigger`. Without it, tapping a rune seal mid-showdown handed
  the window to the opponent. Caught by its own test, not by playing.
- **Conceding stays Neutral-Open only** -- it is a Discretionary Action, not
  something to offer inside someone else's chain.
- **The playmat is two mirrored mats** -- the opponent's rows run in reverse so
  the battlefield rows meet, as they do on a table. Each mat shows only that
  player's units at the shared battlefields, which is how the physical zones
  actually work.

## Session 7 -- scoring and the value heuristic

- **The reward stays win/loss; the heuristic is a separate module** -- shaping
  `returns()` would optimise the proxy, and Riftbound has a concrete trap for
  that: the Final Point rule (471.1.b) makes a greedy conquer strictly worse
  than passing. See SCORING.md.
- **Every evaluation feature is a difference between the players** -- that
  makes the model antisymmetric, so `evaluate(s,0) + evaluate(s,1) == 1`
  exactly. Search can negate instead of recompute, and the invariant catches
  one-sided features. It caught two: an absolute `point_progress`, and a
  learned bias term.
- **The fitted bias is pinned to zero** -- a constant favouring one seat breaks
  antisymmetry. Genuine seat advantage (485.7) belongs in an antisymmetric
  feature instead.
- **Weight fitting splits by game, never by position** -- positions inside a
  game share a label, so a position-level split leaks the outcome and reports
  a flattering score.
- **A fit that does not improve held-out Brier is not written** -- `--force`
  exists but says so loudly.
- **Brier is a diagnostic, not the promotion gate; the benchmark is** -- a fit
  that improved held-out Brier (0.1815 -> 0.1666) and accuracy (0.687 -> 0.727)
  then LOST head-to-head to the hand-set prior 14-26, interval 0.202-0.498.
  Prediction quality is not playing strength. `fit_weights.py` now writes
  `weights.candidate.json` and installs nothing; promotion requires `--promote`
  after a benchmark win.
- **The hand-set prior ships; the fitted candidate is kept as evidence** -- it
  is in the repo so the finding is reproducible, not because it is used.
- **`CardDatabase` is shared by reference in clones** (`__deepcopy__` returns
  self) -- it is immutable and nothing mutates it after load, but copying 908
  cards per candidate action dominated search cost. 16.1 ms -> 2.15 ms per
  clone, which is what makes one-ply search, and later ISMCTS, viable.
- **The greedy agent breaks ties with a seeded RNG, not list order** --
  otherwise it inherits a bias from `legal_actions()` sort order and looks
  stronger than the heuristic has earned.
- **The benchmark swaps seats and shares seeds across a pairing** -- first-player
  advantage is real here (485.7), and would otherwise be read as agent skill.

## Session 8 -- the community sheet and LLM extraction

- **The sheet's "Might" column is the power cost** -- 99.3% match with
  apitcg's powerCost where filled, blank meaning zero (98.3%), 98.9% combined,
  against 1.1% agreement with actual might. Mapped as power; that is what
  filled SFD and raised complete cards from 584 to 697. It also explains RQ-1:
  the npm source derives from this sheet, so the mislabelling propagated.
- **The sheet's `Energy` is mapped but ranked below apitcg** -- only ~60%
  agreement, scattered at +/-1 rather than a systematic offset, so neither
  source is obviously right. For UNL and VEN it is the only source, and that
  doubt is unresolved (RQ-11).
- **Source `kind="manual"`** for the sheet -- `fetch.py` cannot refresh it
  because docs.google.com is blocked; it is cached from a supplied xlsx with
  sha256s, so the pipeline stays reproducible from `data/raw/`.
- **ACCELERATE is a separate action, not a follow-up prompt** -- `play:N` and
  `play:N+accel` are both offered when affordable, so an agent sees two
  distinct lines rather than a hidden sub-decision.
- **Accelerate is tracked per instance, not per card** (`state.accelerated`) --
  805.2.b makes it a delayed replacement effect that survives the unit losing
  the keyword during finalization.
- **The LLM extractor emits data, never code** -- a JSON schema whose enums are
  the DSL primitives, plus a `validate()` gate that re-checks on disk. The
  schema constrains the model; the validator constrains the file after a human
  edits it. This keeps the brief's "no arbitrary Python per card" rule intact
  even though a model now writes the scripts.
- **Generated scripts are candidates, not scripts** -- written to
  `cards/scripts/generated/` with provenance and never loaded by the registry.
  A test asserts the registry cannot auto-load them.
- **Extraction records a sha256 of the card text** -- if a card is errata'd the
  hash stops matching and the extraction is known to be stale.

## Session 9 -- search, threat, and self-play

- **The hard tactical judgments go to search, not to the evaluation** -- sweep
  when behind, deny at 7 but not at 1, hold a Reaction: all conditional, and a
  weighted sum has no product term. ISMCTS represents them by playing the
  consequences out.
- **ISMCTS determinizes rather than reading hidden state** -- each iteration
  samples a world consistent with 128 Privacy. Statistics divide by
  *availability*, not visits, so an action legal in only some determinizations
  is not punished for iterations where it never appeared.
- **Leaves are evaluated, not rolled out** -- Riftbound turns run hundreds of
  steps; full rollouts would spend the whole budget on one line.
- **`victory_pressure` measures urgency in turns, not points** -- a threshold
  at "one point from winning" said the same thing about 7 points with an empty
  board as 7 points holding everything. Rate = battlefields controlled, since
  Hold scores one per controlled battlefield per turn (469.2). 5 points with
  the board (0.400) now outranks 7 without it (0.333).
- **Exhausted cards rotate 90 degrees** (414.1.a), not the 7 degrees the first
  UI used; the slot reserves room so a rotated card does not overlap.
  (Corrected in Session 10 -- the reserving margins were on the wrong axes,
  narrowing the footprint instead of widening it, and the rule only matched
  cards directly under `.box .items`, so units on a battlefield overflowed
  their cell by 30px. Caught by measuring rects in the browser, not by eye.)
- **ABCD is Awaken/Beginning/Channel/Draw (315.1-315.4)** -- already
  implemented in that order; verified rather than rebuilt.
- **The hand-set features are generation 0, not the design** --
  `analysis/self_play_loop.py` replaces them with weights fitted on the agent
  own games, gated on winning a head-to-head whose 95% interval clears even.
  Fitting on random play produced a negative `hand_diff` (random agents hoard
  unplayable cards); self-play data does not contain that artefact.

## Session 10 -- watching the games run

- **Spectator mode reuses the play UI rather than adding a viewer** -- a
  separate renderer could drift from the one humans play on, and a divergence
  between the agent's move and the drawn board is exactly the bug worth
  catching. Agents drive the same `legal_actions()`/`apply()` path.
- **A daemon thread steps the game; the client only polls** -- keeping the
  loop server-side means playback survives a page reload and the browser
  holds no game state. The thread stops itself when a human seat is to move
  or the game ends, rather than spinning.
- **`concede` is offered only when both seats are human (649)** -- a random
  policy picks it uniformly, so a spectated game would end on turn one.
- **`legal` is returned only for human seats** -- the UI must not be able to
  play an agent's turn for it, or what is on screen stops being what the
  agent decided.
- **Each decision reports the agent's own evaluation** -- watching the number
  move is the cheapest available window into whether search is reasoning or
  drifting; it is the same `evaluate()` the agent scores leaves with.
- **ISMCTS(60) is not yet better than greedy: 0.458, 11-13, interval
  0.259-0.658** -- recorded as a null result rather than filed away. It does
  beat random 0.917 (22-2), so the search works; it just has not paid for
  itself against the heuristic at this budget.

## Session 11 -- gear, equipment, and late arrivals at a combat

- **Equip is derived from card text, not scripted per card** -- 818.1.c.2
  defines "Equip [Cost]" as "[Cost]: Attach this gear to a unit you control",
  so `cards/gear.py` builds an ordinary activated `Ability` from the printed
  reminder text. 23 more cards became playable with no per-card Python.
- **The cost is read from the reminder text, not the `[EQUIP ...]` marker** --
  measured, not preferred: the marker is printed six different ways across
  sets, the reminder's grammar is identical on all of them.
- **Four Equipment print `(Pay the cost: ...)` and stay inert** -- a
  non-resource Equip cost is not derivable, and a guessed cost is exactly the
  hidden approximation the brief forbids. Reported with a note instead.
- **Gear's `might` field is NOT its Might Bonus** -- B.F. Sword prints
  "+3 Might" and carries `might: 0`; the two disagree on most Equipment.
  `might_of` had been adding the field, so attached gear contributed the
  wrong number. The bonus is parsed as the last signed Might token outside
  reminder text, which is principled: 137.1 puts it in the lower-right corner,
  so it is the last one printed. A test pins the disagreement so the shortcut
  cannot come back.
- **Playing Equipment does not attach it** -- `origins.py` had scripted the
  attach as ON_PLAY for Warmog's Armor and Doran's Ring, equipping them free
  the moment they were played. Only Quick-Draw attaches on play (819.1.d),
  and neither card has it. The hand-written script got the timing wrong where
  the derivation gets it right, which is the argument for derivation.
- **150.1 is what makes most gear unequippable** -- only the 36 Equipment-
  tagged gear can attach at all; `equipment_profile` returns None for the
  other 75 rather than an empty profile, so "cannot be equipped" is a
  distinguishable answer rather than a missing one.
- **Attached cards ride their host** (719.3.a) and have no move of their own
  (718.5.c); 457.1 recalls an un-attached gear stranded at a battlefield at
  the next Cleanup. `_kill` used to send attachments straight home, skipping
  the window 719.5 leaves them standing in.
- **Late arrivals at a combat gain designations at Cleanup** (464.2.c.3.a) --
  designations were only stamped when Attacker and Defender were established,
  so a unit moved in mid-combat never became an attacker, and ASSAULT/SHIELD
  silently missed it. `_resolve_designations` now reconciles both directions
  each Cleanup, which also implements 323.2.c.
- **The showdown flow itself was already right** -- 450 contest, 344 open at
  Cleanup, 464.2.c.1 attacker = whoever applied Contested, 464.2.d attacker
  gains Focus first, 347 act-or-pass. Verified against the rules rather than
  rewritten.
- **UI facts live on the server, not the observation** -- `is_equipment`,
  `might_bonus` and `ability_labels` are derivable from the card database the
  frontend already holds. Putting them on `observation()` widened the frozen
  agent-facing interface and rewrote every replay hash; the replay harness
  caught it on the first run, which is precisely what it is for.

## Session 12 -- taking battlefields, and forecasting whether you can

- **Control is a lease, not a deed** -- 450 lets a unit move onto a
  battlefield the opponent holds, which Contests it; the Showdown that opens
  at the next Cleanup hands Control to whoever is left standing. Most of this
  already worked; three rules did not.
- **466.5.e: the defender can establish Control too** -- the code only ever
  handed the battlefield to the attacker. A defender who wipes out an attack
  on an uncontrolled battlefield takes it, and the rule says so explicitly:
  "This does not have to be the player that applied Contested."
- **466.5.b: a mutual wipe leaves it Uncontrolled** -- it used to stay with
  the previous holder, which quietly rewarded losing a fight you started.
- **814 Shield was missing** -- Assault (+X while attacking) was implemented
  and Shield (+X while defending) was not, so five units fought at the wrong
  Might whenever they defended, and survived or died wrongly as a result.
- **"Can I take it" is arithmetic, not search** -- 465.2.c has each side
  assign damage equal to its summed Might, lethal in full before moving on,
  so a side wipes the other exactly when its Might covers what the other has
  standing. `engine/threat.py` computes the answer directly rather than
  rolling out.
- **The opponent's hand is bounded, not unknown** -- 103.1.b.2 fixes Domain
  Identity from the Champion Legend and 103.1.b.3-4 constrain every card in
  the deck to it, so the worst case is "the best card in their domains they
  can currently pay for". That is an upper bound, deliberately generous; it
  is not a guess at their hand.
- **`can_lose` and `can_lose_next_turn` are separate** -- a unit played from
  hand enters exhausted (143.4) and cannot move that turn, so a hidden card
  is next-turn pressure. Collapsing the two would have overstated the danger
  every time.
- **Search gets a cheaper path than the UI** -- `forecast` scans the card
  pool for the hidden bound; `takeable_count` uses public information only.
  Feeding the full forecast to `features()` made `evaluate` nine times
  slower, since search calls it at every leaf. The pool is also memoized on
  the database's identity, which is sound because `CardDatabase` is immutable
  and shared (it returns itself from `__deepcopy__`).
- **`takeover_edge` is written as a difference, like every other feature** --
  an uncontrolled battlefield is takeable by *both* players at once, so a
  single signed count would have broken the antisymmetry that makes
  `evaluate(s,0) + evaluate(s,1) == 1`. Verified to floating-point epsilon.
- **Playing locally needs no installation** -- the runtime is pure stdlib, so
  `python3 play.py` on a fresh clone builds the decks, starts the server and
  opens a browser. Verified against a fresh clone on bare `python3` 3.11.
  pytest is a test dependency, not a runtime one.

## Session 13 -- measuring whether training worked

- **"Beat the previous generation" cannot answer "did we improve"** --
  strength is not transitive, so a loop gated on the incumbent can walk a
  circle for ten generations and report ten real improvements while ending up
  no stronger. Replaced with a frozen gauntlet (`analysis/rate.py`) plus Elo,
  so every generation is scored against the same opponents.
- **Every promoted generation is kept** (`analysis/league/`) -- the loop used
  to overwrite one weights file, so once generation 3 was installed
  generation 2 no longer existed to compare against. A ladder is impossible
  without the rungs.
- **SPRT instead of a fixed game count** -- a fixed-N test needs ~385 games to
  resolve a 0.55 edge, and the old gate was 40 games, which can only resolve
  0.65. So a genuine +35 Elo generation would have been rejected every time.
  Borrowed from computer-chess testing rather than invented.
- **Patience, not a hard stop** -- the loop halted on its first
  non-promotion, which is why it never reached generation 2. Training is
  noisy; `--patience 3` retries with fresh data.
- **Training rotates through a field of decks** (`--field`) -- one matchup
  teaches the matchup, and the weights cannot tell "strong in Riftbound" from
  "strong against Volibear". 3 decks gives 9 matchups.
- **The field is capped by card scripting, which makes scripting a training
  concern, not a cosmetic one** -- only 3 legends can field a deck where >=85%
  of card text executes; the rest come in at 52-68%, and a deck where a third
  of the cards are inert teaches a different game than the printed one.
- **Decks on disk are fixtures, not outputs** -- `build_decks.py` now refuses
  to overwrite without `--force`. Improving Equipment changed which cards
  counted as implemented, which changed the builder's ordering, which changed
  the starter decks under the committed replays. The harness caught it on the
  next run; a test pins it now.
- **The harness was verified unbiased before trusting it** -- the identical
  model against itself scores 0.480 (192-208) over 400 games, interval
  0.431-0.529. Worth checking rather than assuming: agent A's tie-breaking RNG
  is seeded from the game seed while B's is offset, so A's randomness is
  correlated with the shuffle. `analysis/nulltest.py` keeps the check runnable.
- **Promotions report which weights moved** -- a promotion with no story is
  unauditable, and a feature flipping sign is exactly how the `hand_diff`
  artefact was caught the first time.

## Choices a player cannot see

- **The observation now carries `choice_options`, and the reason it did not
  is worth writing down** -- the leakage suite (`test_no_cheating`, 35 tests)
  pins one direction: nothing may appear in a player's observation that the
  privacy rules do not grant them. Nothing tested the other direction, so a
  gap of the opposite shape survived for the whole project. When Stacked Deck
  or Called Shot says "look at the top 3", 431.1.c.1's reminder keeps those
  cards *in the Main Deck*, whose privacy is Secret (108.4.d) -- so the
  observation showed nothing at all, while `legal_actions()` offered
  `ChooseTarget(instance_id=3)`. The agent picked between cards it could not
  see, and it was a blind pick, not a hard one: the frontend's prompt read
  "click a highlighted card" with nothing highlighted anywhere on screen.
- **The rule allows it, so this is a fix rather than an approximation** --
  128.2.a makes Privacy follow the zone only "unless specified otherwise by
  the state of the card". Being looked at by a specific player is such a
  state, which is why the field is filled in for that player and empty for
  the other, the same treatment `facedown_card` already gets under 128.4.
- **It is excluded from `to_canonical_bytes`** -- the replay hash covers both
  players' observations, so a field only one player may read would put
  something in the hash that the other side can never reproduce.
  `choice_prompt` is excluded for a different reason (wording), and it would
  have been easy to fold the two into one line and lose the distinction.
- **Found by writing an encoder, not by reading the rules** -- the action
  encoder needs every action's `instance_id` to resolve to something in the
  observation, so building it made the gap a hard failure instead of a
  quiet one. That is a third discovery method alongside reading and fuzzing:
  requiring the interface to be *sufficient*, not merely tight.
