# Is the rules engine correct?

Short answer: **no, and it is not close to "complete".** It is correct in
several specific senses that can be stated and checked, and incomplete in
several others that can also be stated. This file is the honest ledger. It
exists because "the engine is correct" is the single easiest claim to make
and the single most expensive one to be wrong about — everything trained on
top of a wrong engine learns whatever the bug rewards.

Written after closing RQ-14, RQ-15 and RQ-16. Numbers are from this commit.

---

## What "correct" would even mean

Four different claims hide inside the word. They have very different
evidence behind them.

| Claim | Status |
| --- | --- |
| **Structurally sound** — no impossible states, no lost cards, no illegal actions | strong evidence |
| **Deterministic and reproducible** — same seed, same game, byte for byte | strong evidence |
| **Information-tight** — no player can see what the rules forbid | strong evidence |
| **Rules-complete** — every printed rule and every card does what it says | **plainly false** |

The first three are properties of the *machine*. They are testable by
running it a great many times and asserting things that must never happen.
The fourth is a property of the *content*, and no amount of running proves
it — only reading the rules, one at a time, against the code.

---

## 1. Structurally sound — strong evidence

`engine/invariants.py` asserts 12 structural truths after **every action of
every game**, each citing the rule it comes from: one zone per card
(107-108), location and zone agreement, attachment (718-719), designations
(464 / 466.7.a), tokens (186), Stun (423), Hidden (107.3 / 323.7 / 811),
Control (190), resources (163), points (194), chain (329), and card
conservation.

Evidence:

- 619 tests pass.
- 200 games with invariants checked after *every action* (87,765 decisions)
  — zero violations, on the current engine.
- The 10,000-match validation run (BUILD.md section 10) — see `VALIDATION.md`.

**Why this is worth more than the test count.** Spot-checking cannot find a
state that is wrong only in a position nobody wrote a test for. Continuous
assertion over thousands of random games can, and has: the invariant
checker has now caught two things reading the rules had missed, most
recently a stranded facedown card on the last action of a 299-step game.

**The limit of it.** An invariant catches a state that is *impossible*. It
cannot catch a state that is possible but reached for the wrong reason —
a card that resolves the wrong way but leaves a legal board behind.

## 2. Deterministic — strong evidence

Two full games are committed as replays (137 and 352 steps) with a state
hash at every step, derived from the frozen interface and covering *both*
players' views. Any change to setup, shuffling, action legality, resolution
order or the observation diverges them.

They have caught real regressions. They are also why representation changes
are visible: this session moved the hashes twice, and both times
`tests/replays/repair.py` had to prove that every recorded action was still
legal and both outcomes unchanged before the stored hashes were touched.
Regenerating a replay to make it pass destroys the only signal it produces.

## 3. Information-tight — strong evidence

`tests/test_no_cheating.py` changes what a player cannot legally know and
asserts nothing they can see moves: the opponent's hand order, the identity
of a card in their hand, either deck's order, and the face of a facedown
card — five seeds each — while requiring the *owner's* view to still move,
so a test cannot pass by observing nothing. It also pins that the
legal-action set does not depend on the opponent's hidden cards, a leak the
observation test alone would miss.

This suite found a leak in the opposite direction on its first run: 128.4
grants a facedown card's face to its controller, and the observation showed
it to nobody.

## 4. Rules-complete — plainly false

This is where the honest part lives.

### Card text: 31 of 526 playable cards are scripted

Approximately **6%**. Five of those have no printed text at all. Every
unscripted card is inert — it has correct costs, stats, types, domains and
keywords, and moves and fights correctly, but its printed ability does
nothing. `CardData.text_implemented` is false for each, and the frontend
stamps a visible `text inert` badge, so this is never hidden from a player.
This is RQ-5, and it is the single largest approximation in the project.

The mechanical keywords the engine acts on directly — Assault (807), Tank
(815), Backline (826), Ganking (810), Accelerate (805), Shield (814),
Deflect (809), Hidden (811), and Equip/Quick-Draw derived from printed
reminder text (818/819) — do work, which is why 27 of 77 gear are equippable
with no per-card Python at all.

**What this means for any number produced today:** win rates from this
engine measure a game of costs, stats, movement, combat and scoring. They
are not Riftbound win rates and must not be quoted as such.

### Three Game Actions are unimplemented

From the systematic audit of Game Actions 410-444 in `RULES_SUMMARY.md`,
ordered by how many cards need them:

| Action | Cards | Status |
| --- | --- | --- |
| 432 Double | 4 | not implemented |
| 437 Prevent | 2 | not implemented |
| 433 Swap | 1 | not implemented |

Every other Game Action any card needs is implemented. Four (436 Predict,
440 Burn, 441 Empower, 443 Skip) are implemented by nothing because no card
in the pool asks for them.

### Four series audited, eighteen live bugs

The Game Actions audit walked 410-444 one rule at a time and found **five
live bugs**, not merely missing features. The turn-structure audit walked
300-348 and found **four more**, including the largest rules gap the project
has had. The attachment-and-keywords audit walked 700-732 and found **four
more**, and the keyword glossary 800-829 found **three** after that:

| Rule | What was wrong | Effect |
| --- | --- | --- |
| **323.6 / 190.4.c** | control never required units to hold it | a battlefield taken once was free forever and scored every turn |
| **337.4** | the opponent got the first priority window after a play | a caster could never stack on their own item first |
| **347.2.a** | a play did not break the showdown pass sequence | showdowns ended one pass early after any spell |
| **317.2.b** | units were never healed at the end of a turn | non-combat damage accumulated until it killed |
| **426 / 701-705** | Buff counters were modelled as a Might modifier | 53 cards reference a mechanic that did not exist |
| **136.2.c** | an attached card's "me" is its host | an Equipment buffed itself, which 702 forbids |
| **718.2 / 724** | Inactive text | a worn gear could re-equip itself; a loose one fired its worn trigger |
| **719.3.a** | attachments travel with the host | true on the move path, not the combat-recall path |
| **359.3.e.5** | a choice is re-checked before it is honoured | a stale option could bind a gear to a card not in play |
| **816 Temporary** | a Temporary permanent dies at its controller's Beginning Phase | it lived forever (11 cards) |
| **825 Unique** | one copy per deck by name | unenforced |
| **815.1.c.2** | the citation for Tank damage ordering | cited 626.1.d.4, which is not a rule |

323.6 alone moved mean game length from **14.2 turns to 25.3**. Several
existing tests had been written against the buggy behaviour and had to be
corrected — which is how a wrong engine makes its own tests agree with it,
and the reason reading the rules beats trusting a green suite.

Two of those found each other: making Buff counters real immediately exposed
136.2.c, and fixing that exposed 718.2/724. A wrong model can hide the next
wrong model behind it.

All four rule series have now been walked one rule at a time. What remains
is **unimplemented**, not unexamined, which is a different and much better
kind of gap — every item below is named, counted and reachable:

- **473-477 Layers** — not transcribed at all (RQ-3). Nothing currently
  needs them; that stops being true as soon as continuous modifiers land.
- **727 Dependent Keywords** (12 cards) and **728-732 XP** (6 cards).
- **Nine keywords**, led by Deflect (27 cards) and Repeat (17). Both of those
  are blocked on RQ-19, not on effort.
- **Three Game Actions**: Double (4 cards), Prevent (2), Swap (1).

Eighteen live bugs across four audited series. The rate did not fall off:
the last series still produced three, and the 10,000-game validation run
found one more that every smaller run had missed.

### The structural gap, now closed

**RQ-19** was the largest single deviation: targets were chosen at resolution
rather than at finalization, against 355.8. One deviation cost four rules, and
fixing it required putting the whole play process back in its printed order
(354 chain — 355 choices — 356 cost — 357 pay):

| Rule | Now |
| --- | --- |
| 359.3.e.5 — a spell can be **fizzled** by answering its target | works |
| 809 — **Deflect** has a target to price against | built, 27 cards |
| 355.8 — a spell with no legal target is not a legal play | gated |
| opponents see the target before responding | the chain item carries it |

355.5.b scopes it, and the scope is the rule's own: a permanent's "when I'm
played" trigger does *not* choose its target as the card is played — "The
target will be chosen when the ability triggers", which is where the engine
chooses it. The related gap that remains is that triggered abilities are not
yet chain items anyone can respond to (383, 354.2); that belongs with 471.2's
timing work rather than here.

### Known approximations, all logged

`RULES_QUESTIONS.md` carries every one with its estimated effect. Open at
this commit: RQ-1 and RQ-11 (source data quality — UNL and VEN are largely
unsimulatable), RQ-2 (source precedence for card text), RQ-3 (Layers), RQ-4
(a sixth interface call awaiting the owner's approval), RQ-5 (card text),
RQ-6 (multi-domain power splits), RQ-7 (damage assignment enumeration),
RQ-8 (Burn Out's point choice), RQ-9 (concede disabled in simulation),
RQ-12 (decklist knowledge assumption), RQ-13 (four Equipment whose Equip
cost cannot be read off the card).

Closed this session: RQ-10, RQ-14, RQ-15, RQ-16.

---

## What would actually raise the claim

In the order that buys the most:

1. **Script the cards.** 6% to a meaningful fraction. This is the gate on
   every number the project produces, and nothing else changes that.
2. **Triggered abilities as real chain items** (383, 354.2), so a trigger can
   be responded to and 471.2's ordering is expressible. It is what remains of
   the targeting work, and what the FAQ's Azir / Overzealous Fan example
   needs.
3. **Implement Double, Prevent and Swap** (7 cards).
4. **Transcribe Layers (473-477)** before any continuous modifier lands.

---

## The one-line version

The engine is a *sound and honest machine running an incomplete rulebook*.
Everything it does, it does reproducibly, without leaking information, and
without reaching an impossible state. What it does not yet do is most of
what the cards say — and every gap is written down rather than papered over.
