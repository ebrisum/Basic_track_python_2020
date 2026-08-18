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

## RQ-4 — `state.current_player` is a sixth interface call

**Status:** open, awaiting approval.

The brief specifies five agent-facing calls. Replays cannot verify turn or
priority order without knowing whose move it is, and OpenSpiel exposes
`current_player()` too, so the drop-in is unaffected. Implemented and flagged
rather than assumed. See `DECISIONS.md`.

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
