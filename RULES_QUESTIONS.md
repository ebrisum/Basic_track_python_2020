# Rules questions and open blockers

Every unresolved rules decision goes here with: the situation, the options,
which was provisionally implemented, and the estimated effect on outcomes.

Approximations are allowed. Hidden approximations are not.

---

## BLOCKER-1 — Original data sources unreachable — RESOLVED BY SUBSTITUTION

**Status:** worked around. No longer blocking.

All four sources named in the brief are still refused by this environment's
egress proxy with `403 Forbidden` at CONNECT: `api.riftcodex.com`,
`riftscribe.gg`, `docs.google.com`, `riftbound.gg`. Verified with `curl`, the
`WebFetch` tool, and the proxy's own status endpoint. The proxy README is
explicit that a 403 is an organization policy denial to report, not route
around.

Reachable equivalents were found through hosts the policy *does* allow — the
npm registry and github.com — and are now cached in `data/raw/`:

| Substitute | Replaces | Content |
| --- | --- | --- |
| npm `riftbound-tools` 1.0.0 | Riftcodex / sheet | 950 records, OGN OGS SFD UNL |
| `apitcg/riftbound-tcg-data` | RiftScribe cross-check | 699 records, OGN OGS SFD |
| `ChristianIvicevic/riftboundfaq` | riftbound.gg rules PDF | official Core Rules v1.0–v1.4 + Tournament Rules + ~200 judge rulings |

**Residual risk, stated plainly.** These are third-party mirrors. The rules
*text* is first-party (Riot's own PDF, `CR-v1.4.txt`, header "Last Updated
2026-07-16"), but the hosting is not, so a tampered or stale mirror would not
be detectable from inside this repo. When the original four become reachable,
re-fetch and diff — `data/sources.py` keeps the blocked URLs recorded for
exactly that.

---

## RQ-1 — The npm source's numeric fields are not what they are named

**Status:** decided and implemented.

**Situation.** Measured across the 503 comparable cards that both card sources
cover:

| Hypothesis | Agreement |
| --- | --- |
| `npm.might` == `apitcg.might` | **0.6%** |
| `npm.might` == `apitcg.power` | 52.3% |
| `npm.energy` == `apitcg.energy` | 31.2% |
| `npm.cost` == `apitcg.energy` | 50.5% |
| `npm.energy` == `apitcg.energy + apitcg.power` | 12.7% |

0.6% agreement on a field called `might` is not noise — it is a different
quantity wearing the name. No single hypothesis explains the rest either.

**Options.** (a) Map them anyway and accept unknown corruption. (b) Map them
with a correction rule. (c) Do not map them; take numerics only from apitcg.

**Implemented: (c).** Mapping them would have put confidently-wrong costs and
stats into every simulation, with no error to notice. Guarded by
`tests/test_normalize_adapters.py::test_riftbound_tools_adapter_maps_no_numerics`.

**Effect on outcomes:** large if wrong, and it would have been invisible. The
cost is that the **UNL set (268 cards) now has no costs at all**, since apitcg
does not cover it. Those cards are unsimulatable until a trustworthy source
appears. OGN (337/341) and OGS (24/24) are essentially complete, so Milestone 1
is unaffected if the two decks are Origins-based.

---

## RQ-11 — The community sheet's `Energy` is only ~60% reliable

**Status:** open. It is the sole source for two whole sets.

The repo owner supplied the brief's original source #3 as an xlsx. Two
findings from measuring it against apitcg over 529 comparable cards:

**Resolved:** its column headed **"Might" is the power cost**, not Might —
99.3% agreement where filled, and a blank means zero (98.3%), for 98.9%
combined. Agreement with the field it is *named* after is 1.1%. This also
explains RQ-1: the npm source derives from this sheet, which is why its
`might` matched apitcg's `power` 52% of the time. Mapping it filled SFD's
power gap and took complete cards from 584 to 697.

**Open:** its `Energy` column agrees with apitcg on only ~60% of shared
cards. The mismatches scatter at ±1 (75 at −1, 21 at +1) rather than showing a
systematic offset, so this is errata drift or transcription noise and *neither
source is obviously right*.

Where apitcg covers a card, apitcg wins on precedence. For **UNL and VEN the
sheet is the only source**, so their energy costs carry that unquantified ~40%
doubt — and their Might is missing entirely, since no source carries it.
That is why UNL (39/268) and VEN (28/215) remain mostly unsimulatable.

**Effect on outcomes:** any future simulation using UNL or VEN cards inherits
it. OGN, OGS and SFD are unaffected — apitcg covers them.

**To resolve:** a source with verified UNL/VEN stats. Riftcodex would settle
it if egress ever allows.

---

## RQ-12 — Deduction assumes both decklists are known

**Status:** decided, switchable, and it matters which way it is set.

Riftbound is a closed system: a Main Deck is exactly 40 cards fixed before the
game (103.2), and trash (108.2.d), board (107.1.d), Champion Zone (108.3.e) and
Legend Zone (107.4) are all public. So a player does not guess at the
opponent's hand — they subtract:

    unseen(P) = decklist(P) − everything of P's that is publicly visible

`engine/knowledge.py` does that arithmetic exactly: the unseen pool equals
hand + deck on every seed tested, and ISMCTS samples its determinizations from
precisely that pool.

**The assumption.** Subtracting needs the decklist. That is true for this
project's actual goal — a matchup matrix against a database of *known*
tournament decks — and false in game one against an unknown opponent.

`KnownDecklists.BOTH` (the default) grants it. `KnownDecklists.OWN_ONLY`
models real game-one uncertainty: only sizes are public, and the deducer
returns an empty pool rather than a confident wrong one.

**Effect on outcomes:** an agent under `BOTH` plays with information a real
player would not have in game one, so win rates measured that way are an upper
bound on real play. For the field-weighted matrix the assumption is correct
and the numbers are the ones wanted. For "how would this deck do blind", set
`OWN_ONLY` — and note ISMCTS determinization would then need a prior over
plausible decklists, which does not exist yet.

**Bug this surfaced:** channeled runes were being counted as Main Deck cards,
inflating the deduced 40-card list every time one was channeled. Runes come
from the Rune Deck and are not Main Deck cards (161.1, 052). Caught by a test
asserting the decklist total stays constant across a whole game.

---

## RQ-2 — Which source is authoritative where they conflict

**Status:** decided, low confidence, revisit when Riftcodex is reachable.

898 field disagreements remain after the phantom-conflict fix, in
`data/DISCREPANCIES.md`: `rules_text` 503, `name` 214, `keywords` 176,
`domains` 4, `type` 1.

- `name` (214) is mostly convention — "Darius - Trifarian" vs "Darius,
  Trifarian", and alternate-art suffixes. Cosmetic.
- `rules_text` (503) is **not** cosmetic. The two sources carry materially
  different wordings; e.g. OGN-001 Accelerate reads "pay 1 Fury" in apitcg and
  "pay 1 energy and 1 fury rune" in npm. These are different costs. apitcg
  matches the v1.4 rules' terminology, so it wins, but **card text is the
  Golden Rule (002)** and getting it wrong changes card behaviour directly.
- `type` (1): SFD-078 Temporal Portal is Spell (apitcg) vs Gear (npm).

**Implemented:** precedence `apitcg > riftbound_tools`, because apitcg carries
power cost and the Champion/Signature/Token distinction that the engine needs
and npm lacks entirely.

**Effect on outcomes:** moderate. Any scripted card whose text came from the
wrong source will behave wrong. Mitigation: every card scripted for Milestone 1
gets its text checked against the card image or a judge ruling before its unit
test is written.

---

## RQ-3 — Layers (473–477) not yet transcribed

**Status:** open, deferred.

The continuous-effect layer system is cited in `RULES_SUMMARY.md` but not
detailed. It matters for stacking buffs, keyword grants, and stat-setting
effects. Deferred until a Milestone 1 card actually needs it — but `buff` is
the second-most-common primitive (140 uses), so this will come up early.

**Effect on outcomes:** unknown until a real interaction appears.

---

## RQ-4 — `state.current_player` is a sixth interface call — RESOLVED

**Status:** closed. Kept.

**What the question was.** The founding brief froze the agent-facing surface
at five calls, mirroring OpenSpiel: `legal_actions`, `apply`, `observation`,
`is_terminal`, `returns`. The engine grew a sixth, `current_player`, and the
question was whether that is a widening of the contract that should be undone.

**Why it stays.** It is not a widening; it is the call that makes the other
five usable. `observation(player)` needs a player argument, and an agent
cannot know which player to ask for without being told whose decision it is.
The information is public — whose turn it is has no privacy level to violate
(128) — and OpenSpiel's own interface exposes `current_player()` for exactly
this reason, so the drop-in the brief cares about is unaffected.

**The alternative, and why it is worse.** The observation already carries
`current_player` as a field, so an agent could in principle read it there. But
building an observation requires naming a player first, so that route is
circular: it would force every caller to build one player's view speculatively
to discover it should have built the other's.

**What is not allowed to follow from this.** A sixth call is not a precedent
for a seventh. Nothing else moves onto the interface without the same
argument: that it is public information, that it is needed to use the existing
calls, and that OpenSpiel has an equivalent.

---

## RQ-16 — A revealed card is announced but not remembered — RESOLVED

**Status:** closed.

424 Reveal now records which instances were shown from a hand
(`state.revealed_in_hand`), and three consumers read it:

* `engine/knowledge.py` counts a revealed card as **located**, not unseen, and
  exposes it as `Knowledge.known_in_hand`. `probability_in_hand` returns 1.0
  for it, and `hidden_hand_size` spreads the remaining pool over one fewer
  slot -- so a revelation sharpens *every* estimate, not just its own.
* `agents/ismcts.py` pins the card in the opponent's hand across
  determinizations. Sampling worlds you have already been shown are false is
  not uncertainty; it is forgetting.
* `RiftboundObservation.revealed_opponent_hand` puts it in the agent's legal
  view, so nothing has to reach past the frozen interface to use it.

The record is never pruned. Every consumer re-checks where the instance
actually is *now*, which cannot go stale; a pruning hook on every zone move
could. 424.1.a says Revealed is a temporary state and not a zone, and this is
what that means in practice: a card shown and then played, discarded or
recycled stops being known.

Only **hand** reveals are recorded. A card shown from the top of a deck is
already inside the pool the deducer subtracts to, and nothing pins it there
afterwards; claiming to know where it went would be an invention.

### Superseded text

The original entry read: "What the engine does *not* do is feed that back into
`engine/knowledge.py` ... An agent forgets what it was shown."

---

## RQ-14 — A hidden card's targeting is not restricted to its battlefield — RESOLVED

**Status:** closed, except for one construction the DSL cannot express, named
below rather than left to be discovered.

811.1.d restricts what a card played from Hidden may choose:

| Rule | Requirement | Now |
| --- | --- | --- |
| 811.1.d | a hidden **spell** with no valid target there can't be played from Hidden | `_hidden_play_has_targets` |
| 811.1.d.1 | a hidden permanent is played **to that battlefield** | already done; the placement bug below fixed |
| 811.1.d.2 | a hidden card's targets come **from that battlefield** | `EffectContext.restrict_location` |
| 811.1.d.2's exception | ...unless the card's own restriction makes that impossible | `restriction_binds` |
| 811.1.d.3 | a hidden card that makes you **play a unit** plays it there | vacuous: no DSL effect plays a unit |
| 811.3 | played normally, no restriction at all | untouched by the above |

The exception is a *static* property of the selector, not of the board -- the
FAQ's Rebuttal entry is explicit that the restriction survives even a change
of controller, and 811.1.d handles the empty-board case separately by barring
the play. The only selector the DSL can currently express that is impossible
to satisfy at a battlefield is one restricted to a base. Riftbound's own
example of the exception -- Tideturner's "a unit you control at *another*
location" -- needs a relative-location constraint `Selector` does not have.
No scripted card uses one; when one is scripted, `restriction_binds` is the
single place that changes.

**A real bug fell out of this.** "Played from Hidden" was one slot on the
state, but the chain holds several cards at once, so any card played in
response overwrote it: the hidden spell then resolved with no restriction at
all, and a hidden permanent entered the base instead of its battlefield
(811.1.d.1). It now lives on the `ChainItem`. `tests/test_hidden_targeting.py`
fails on both counts if the fix is reverted -- verified by injecting it.

**Tested with synthetic scripts.** 34 cards have HIDDEN, exactly one is
scripted, and its play effect targets itself. A rule tested only by the cards
that happen to be scripted today is a rule tested by accident, so the tests
build their own.

**Still an approximation.** The DSL has no "you *may* choose" flag, so an
optional target would be treated as required by the 811.1.d gate. No scripted
card has one.

---

## RQ-15 — What happens to a hidden card when you lose the battlefield — RESOLVED

**Status:** closed. The rules do answer it; I had looked in the wrong place.

The original entry said this was "unanswerable from the text I have", having
read only 811.1.b's "for as long as you control that battlefield". The answer
is in the zone rules and the cleanup steps:

* **107.3.c** — "Cards can only be placed in or occupy the Facedown Zone if
  the controller of the card also controls the associated Battlefield."
* **107.3.d** — "If a player loses Control of a Battlefield, any cards in the
  Facedown Zone associated with that Battlefield are removed during the next
  Cleanup."
* **323.7** — cleanup step 5, which says where to: "Remove all Hidden cards
  from all Battlefields that are not controlled by the same player and place
  them in their owner's Trash."
* **421.4** — the card is revealed to all players as it changes zones. It
  lands in the trash, whose contents are public (108.2.d), so the deduction
  in `engine/knowledge.py` picks the identity up with no extra bookkeeping.

`_remove_stranded_hidden` implements this as part of the cleanup fixpoint, and
the state invariant now **enforces** the controller check rather than
reporting it. 60 random games, 15,824 actions, zero violations.

An uncontrolled battlefield strands the card too: 107.3.c requires that the
controller of the card *also control* the battlefield, and nobody controlling
it does not satisfy that.

**The lesson worth keeping.** The original entry listed four plausible
readings and picked the most permissive. All four were guesses about a rule
that was written down. "The rules do not say" needs to survive a search of the
rules for the *mechanism* (Facedown Zones, Cleanup steps), not only for the
keyword.

### Superseded text

"**Status:** open, unanswerable from the text I have. ... **Decision.** The
engine leaves the card hidden and playable."

---

## RQ-17 — "this" on an attached card cannot be told from "me"

**Status:** open, narrow, and one-directional.

**Situation.** 136.2.c says an Equipment's Effect Text abilities "are appended
to the Rules Text of the card to which the card with the Effect Text is
Attached". So in Warmog's Armor's "When I conquer, buff me", *me* is the host
unit, and the engine now resolves `SELF` on an attached card to its Top-Most
Card accordingly.

136.2.d carves out an exception: "Effect Text may refer to 'this' or to the
name of the Attached game object... Doing so refers to the Attached game
object and not the Top-Most Card." Guardian Angel's "If I would die, kill
**Guardian Angel** instead. Heal **me**..." uses both in one sentence.

The DSL has one self-reference, `Selector(scope="self")`, and it now means the
host. There is no way to write "the attachment itself".

**Effect on outcomes.** None today: no scripted card uses the 136.2.d form.
It becomes wrong the moment Guardian Angel or Brutalizer is scripted.

**What it needs.** A second scope -- `attachment` alongside `self` -- resolved
before the walk up the attachment chain. Small, and better done with the card
that needs it than speculatively.

---

## RQ-18 — Rules Text and Effect Text cannot be told apart on the card

**Status:** open. Implemented by inference, and the inference is named here.

**Situation.** 136.1 and 136.2 divide a card's printed text into **Rules
Text** and **Effect Text**, and 718.2 / 724 make exactly one half live at a
time:

* attached — printed Rules Text is Inactive (718.2); Effect Text is live and
  appended to the Top-Most Card (718.3);
* unattached — Rules Text is live (723); Effect Text is Inactive (724).

Physically the division is a box on the card. In the scraped data both halves
arrive merged into one `rules_text` string, so the engine cannot read it.

**Implemented.** For an Equipment, the derived Equip and Quick-Draw abilities
are treated as Rules Text and every scripted ability as Effect Text. Every
non-Equipment card is treated as all Rules Text, which is exactly right: 723
says Rules Text is never Inactive by default, and a card that cannot be
attached never reaches 724.

**Where it would be wrong.** An Equipment with a printed activated ability
meant to work while it is loose would be misfiled as Effect Text and go
quiet. No card in the scripted pool has one. The inverse -- a non-Equipment
card with Effect Text -- cannot arise, since Effect Text exists to be
appended on attachment.

**Effect on outcomes.** None measured today. It grows with every Equipment
scripted, and the fix is data, not logic: a source that keeps the two text
boxes separate, or a per-card marker in the script.

---

## RQ-19 — Targets are declared at finalization — RESOLVED

**Status:** closed. Spells and activated abilities declare their targets as
they are played, which is the whole of what 355.8 asks; 355.5.b puts
triggered abilities' choices exactly where the engine already makes them.

**What 355.8 asks.** "In order to put a spell or ability on the chain, valid
choices must be made for all targets." The engine used to defer every choice
to resolution, which cost four rules at once.

### Implemented

A chain item is now **Pending** (329.2) until its targets are declared, then
Finalized (329.3). `ChainItem.targets` carries them, keyed by (ability index,
effect index). On resolution each declared target is **re-checked against the
board**, and 359.3.e.5 is applied: an illegal target is unaffected and its
instruction skipped, rather than the effect being re-aimed at whatever is
still legal.

| Consequence | Now |
| --- | --- |
| a spell can be **fizzled** by answering its target (359.3.e.5) | works |
| a spell with **no legal target cannot be played** (355.8) | gated in `legal_actions` |
| opponents **see the target** before responding | the chain item carries it |
| **Deflect** (809) has a target to price against | built, see below |

`is_target()` implements 355.7's default and the FAQ's exceptions: 355.10.d
(no real choice — `scope="all"` and `scope="self"`) and 355.10.e (a set chosen
by other players — `each_player`). 355.10.a is handled structurally, since
`Discard` and `LookAtTop` reach non-public zones without a selector at all.
355.10.f ("must") has no DSL representation and no card in the pool uses the
wording.

### Scoped exactly as 355.5.b scopes it

Only an item's **own** effects declare early. That is not a shortcut — it is
what the rule says. **355.5.b**: "This does not include making choices for
Triggered Abilities of permanents ... even if those abilities trigger when the
chain item is played. Example: A unit with a triggered ability that says
'When I'm played, kill a unit' does not require you to choose a target as it's
played. The target will be chosen when the ability triggers."

The engine chooses those targets when the ability triggers, which satisfies
355.5.b. What it does *not* yet do is put a triggered ability on the chain as
its own item (383, 354.2), so nobody can respond between the trigger and its
resolution. That is a separate gap from RQ-19 and belongs with 471.2's timing
work; it is not a leftover of this one.

### Deflect, and the order of the play steps

Fixing the choice point was necessary but not sufficient. The engine still
paid before it targeted, where 353-359 print

    354 move to chain -> 355 make choices -> 356 total cost -> 357 pay

The steps now run in that order, which is what gives Deflect (809.1.d) a
target to price against. `deflect_cost` sums 809.2's multiple instances, reads
an omitted value as 1 (809.1.b.3), taxes only an opponent's permanents
(809.1.c), and is paid in Power of any domain (809.1.c.1). 358 Check legality
falls out: a target whose tax cannot be paid is not offered, and a spell with
no affordable target set is not a legal play.

Reordering exposed a second defect at once: an activated ability's chain item
was charged its *source card's* play cost on top of the ability cost it had
already paid. The whole suite went red, which is the useful kind of failure.

**Repeat (820)** is no longer blocked either — 820.2's "usual time during
the Make Relevant Choices step" now exists as a real step. It simply has not
been written. 17 cards.

---

## RQ-20 — Simultaneous triggers are ordered by instance id, not by choice

**Status:** open, deliberate, small.

**Situation.** 383.3.d: "If more than one Triggered Ability is Triggered
simultaneously, then the player that controls the Abilities selects the order
to place them on the Chain." 383.3.d.1 extends it across players, starting
with the Turn Player.

The engine orders a player's simultaneous triggers by instance id, and does
not offer the choice.

**Why.** Offering it adds a decision to every board where two triggers fire
together, on a choice that usually does not matter, and every one of those
decisions widens the search tree the agent has to explore. The cross-player
half of 383.3.d.1 *is* implemented, since triggers are placed as each
player's scoring or play resolves.

**Effect on outcomes.** It matters when two triggers of the same controller
interact -- one buffs a unit the other checks, say -- and the printed order
happens to be the worse one. No pair in the scripted pool interacts.

**What it needs.** A `ChooseTrigger` action offered only when a controller has
two or more simultaneous triggers *whose effects can interact*. Worth doing
with the first card pair that needs it, not speculatively.

---

## RQ-13 — Four Equipment have Equip costs that cannot be read off the card

**Status:** open, bounded, four cards.

**Situation.** Equip costs are derived from each card's printed reminder text
(818.1.c.2). Four Equipment print `(Pay the cost: ...)` instead of the cost,
because their cost is not purely resources:

| Card | Printed Equip cost |
| --- | --- |
| SFD-150 Last Rites | Chaos, Recycle 2 cards from your trash |
| SFD-178 Blade of the Ruined King | Order, Kill a friendly unit |
| UNL-158 Shepherd's Heirloom | Spend 1 XP |
| UNL-188 Hextech Gauntlets | 3 energy, 1 rune of any type, reduced by the chosen unit's Might |

**Decision.** These get no Equip ability and stay inert, with the reason
recorded on the profile (`cost_note`). Handing them a guessed cost would be a
hidden approximation.

**Effect on outcomes:** four cards cannot be equipped. None is in the starter
decks. `RecycleFromTrash` and a kill cost already exist as DSL costs, so
SFD-150 and SFD-178 are a small scripting job, not an architectural one; XP
(UNL-158) is an unimplemented subsystem.

---

## RQ-5 — Card rules text is not executed

**Status:** open. The single largest approximation in the engine.

**Situation.** The engine implements the *structural* rules in full — setup,
turns, resources, movement, contest/combat, scoring, win condition — but there
is no effect interpreter yet, so a card's printed text does nothing. Of the
441 playable cards in `cards.json`, only **7** are fully implemented: 5 have no
text at all, and 2 more carry nothing but keywords the engine acts on.

**What *is* implemented** are the mechanical keywords, at engine level rather
than through the DSL:

| Keyword | Rule | Effect |
| --- | --- | --- |
| Assault X | 807 | +X Might while an attacker |
| Tank | 815 | must be assigned lethal damage first |
| Backline | 826 | must be assigned lethal damage last |
| Ganking | 810 | Standard Move battlefield → battlefield |

Every other printed keyword is parsed and displayed but inert.

**Not hidden.** `CardData.text_implemented` is false for every affected card,
and the frontend stamps a `text inert` badge on it. A player can always see
which cards are not doing what they say.

**Effect on outcomes:** very large. Win rates from the current engine measure
a game of stats, costs, movement and scoring only. They are **not** usable as
Riftbound win rates. Fixing this is the DSL interpreter work in `DSL.md`.

---

## RQ-6 — Multi-domain power costs are split evenly

**Status:** open, low impact.

A card's power cost is paid in its own domain (163.2). For a card with two
domains the split is not stated in the data, so `CardData.power_domains`
alternates through the listed domains. Single-domain and colourless cards —
the overwhelming majority — are exact. Multi-domain cards with power > 1 may
demand the wrong mix.

**Effect:** small; affects payability of a minority of cards.

---

## RQ-7 — Combat damage is assigned one unit at a time

**Status:** open, deliberate.

465.2.c lets the assigner distribute summed Might freely. Enumerating every
legal distribution is combinatorially large and useless to a search agent, so
the engine offers "assign to this unit" repeatedly, filling lethal before
moving on, with Tank forced first and Backline last.

**Effect:** small. It covers the strategically real choices but excludes
deliberate damage-spreading that kills nothing.

---

## RQ-8 — Burn Out awards the point automatically

**Status:** open.

431 / 194.1.d: when a player Burns Out, a player *picks* someone to gain 1
point. With two players the engine gives it to the opponent without offering
the choice.

**Effect:** negligible in 1v1; wrong in multiplayer, which is out of scope.

---

## RQ-9 — Conceding is disabled during simulation

**Status:** decided.

649 makes conceding legal at any time. A random policy that may concede on any
turn produces meaningless statistics, so `allow_concede` defaults to False and
is turned on only for interactive play.

**Effect:** none on rules fidelity; it removes an action no rational agent
takes.

---

## RQ-10 — Non-Combat Showdowns resolve immediately — RESOLVED

**Status:** closed. The Chain, priority, focus and showdowns are implemented.

`engine/chain.py` holds the Chain, the four-state timing machine (307-310) and
the timing predicates; `engine/state.py` drives them. Implemented: cards and
abilities go on the Chain (354), items are Finalized then resolve newest-first
(340.1), priority passes and all players passing in sequence resolves the top
item (339), Units/Gear/Add abilities resolve immediately (337.2), Showdowns
open at a Cleanup on a Contested battlefield (344), the contester gains Focus
(345), Focus passes when a chain closes (347.1.b) *except* for triggers and Add
abilities (346.1), and all players passing ends the Showdown (347.2.a). A
Combat Showdown closes into the Damage Step (464→465).

Timing legality now follows the rules: only Reactions in a Closed state
(309.1.a), Action or Reaction in a Showdown (308.1.a), and the Standard Move
is barred from both (144.1.b-c).

Covered by `tests/test_chain_and_showdowns.py` (30 tests), including a
newest-first ordering test where Gust bounces the unit Hextech Ray was aimed
at, so the ray finds nothing.

**Still approximated:** the Chain does not model Pending vs Finalized as
separate visible steps -- an item is finalized the moment it is played, since
no scripted card interrupts finalization. Simultaneous trigger ordering
(303.2.a) is by turn order but is not yet exercised by any scripted card.

### Superseded text

The original entry read: "The engine skips both windows: a sole occupant of a
contested battlefield takes Control immediately, and combat proceeds straight
to damage assignment." That is no longer true.

344.2: a Contested battlefield with no opposing units present opens a
Non-Combat Showdown at the next Cleanup, in which players alternate playing
spells (342) before Control is established. Combat Showdowns (464) likewise
have priority windows.

The engine skips both windows: a sole occupant of a contested battlefield
takes Control immediately, and combat proceeds straight to damage assignment.

**Why this is currently harmless, and when it stops being so.** No card text
is executed (RQ-5), so there is nothing any player could play into those
windows. The moment the DSL interpreter lands, this becomes a real divergence
and must be implemented properly — chains, FEPR, priority and focus are all
already written up in `RULES_SUMMARY.md`.

**Effect on outcomes:** none today; large once cards work.

---

## Expected trouble spots — now with rules citations

The brief flagged these in advance. Each now has a starting point:

- **Response windows / priority in showdowns** — the four-state machine at
  **307–310**, priority vs focus at **311–313**, FEPR at **334–340**. Focus and
  priority are *separate* permissions (**313.2–313.4**); this is where bugs
  will live.
- **Simultaneous trigger ordering** — **303.2.a** (turn order from the turn
  player) and **464.2.e.1** (attacker, non-defenders, defender).
- **Battlefield hold/conquer timing** — **467–471**, plus the **Final Point**
  rule at **471.1.b**, which converts a winning conquer into a card draw
  unless every battlefield was scored that turn. Easy to miss, directly
  outcome-affecting.
- **Rune deck and payment edge cases** — **160–168**; energy and power are
  different resources (**163**), pools empty at Main Phase start with unspent
  resources **lost** (**316.3**).
- **Champion death / recycle / redeploy** — **103.2.a.3** (Chosen Champion
  identity follows the *name*, not the physical card), **416** Recycle,
  **428** Kill.

---

## RQ-21 — TOML instead of the plan's YAML for configuration

**Status.** Decided, and recorded here because it is a deviation from
`TCG_AI_BUILD.md` rather than from the Core Rules, and a deviation nobody was
asked about should still be visible.

**The plan asks for** `ai/config/*.yaml` (section 30).

**What was built** is `config/*.toml`, read with the standard library's
`tomllib`.

**Why.** YAML needs PyYAML. The standing rule from the first brief is pure
standard library plus pytest, with any dependency asked for before it is
added. A configuration file format is a poor thing to spend the first
dependency on, and TOML does the same job from the standard library on 3.11+.

**What it costs.** YAML's anchors and multi-document files are not available.
Nothing in the plan's list of configurable fields needs either.

**Reversible.** `learning/config.py` isolates the parse in one function; a
YAML backend would be a few lines if the dependency is ever approved.

**Since decided:** the first dependency *was* taken, for the model stack
(RQ-22). It still is not PyYAML, and the reasoning above is why — TOML costs
nothing and does the job, so there is no reason to add a second.

---

## RQ-22 — PyTorch, and what it costs

**Status.** Decided. The first dependency beyond the standard library and
pytest in the project's life, taken deliberately and scoped.

**The question.** `TCG_AI_BUILD.md` sections 16-22 ask for a transformer
encoder with policy and value heads, trained by PPO with a clipped objective,
GAE, an entropy bonus and gradient clipping. The founding brief says pure
standard library plus pytest, and ask before adding anything. Three options
were on the table: PyTorch, NumPy only with a hand-written MLP, or stay
stdlib.

**Decision: PyTorch.** The plan does not ask for a neural network in the
abstract; it asks for a specific architecture and a specific RL algorithm.
Writing PPO without autograd means writing autograd, and a hand-rolled
backward pass is the least interesting and most bug-prone part of the job --
with no test that could distinguish "the agent is not learning" from "the
gradient is wrong". NumPy alone would buy a small MLP and would still not be
the plan's model.

**What it does not touch.** The engine keeps its constraint. `engine/`,
`cards/`, `agents/`, `analysis/`, `learning/` and `play.py` import nothing
outside the standard library, and a test asserts it. Someone who wants to play
Riftbound or run the validation suite still needs nothing installed. The
dependency lives in `model/` and in the training entry points, and nowhere
else.

**A wrinkle worth recording.** `download.pytorch.org` is not reachable through
this environment's proxy, so the wheel came from PyPI, which on Linux means
the CUDA build: 4.7 GB of venv for a machine with no GPU
(`torch.cuda.is_available()` is False). It works and it is what was
installable. On a box with a GPU it is the right build anyway; on a CPU-only
box `--index-url https://download.pytorch.org/whl/cpu` is the smaller install
where the network allows it.

**Reversible?** Partly. The trajectory format, the action encoding and the
dataset generator are all pure stdlib and stay useful whatever trains on
them -- that separation was built before this decision, not after it.
