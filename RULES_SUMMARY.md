# Rules summary

The implementation target for `engine/`. Every claim cites a rule number in
the official **Core Rules v1.4 "Vendetta"** (header: *Last Updated 2026-07-16*),
cached at `data/raw/core_rules/CR-v1.4.txt` — 120 pages extracted from the PDF
mirrored by `ChristianIvicevic/riftboundfaq`. Citations are bare rule numbers,
e.g. **194.3**.

Two-player game assumed throughout; Milestone 1 is strictly 1v1. Multiplayer
carve-outs (462.1–462.3, 487–488) are noted but not implemented.

## Win condition

- Victory Score is **8 points** by default (**194.3**).
- A player wins if, **in a cleanup**, they have ≥ Victory Score *and* strictly
  more points than any other player (**194.2**). Ties do not win — play
  continues until someone leads in a cleanup (**194.2.b**).
- Points also come from card effects and from an opponent Burning Out
  (**194.1.c–d**). Points floor at 0; losing points at 0 does nothing and
  triggers nothing (**194.4**).
- A player also wins by effect, or by being the last player in the game
  (**195**). The game ends the moment someone wins (**196**).

## Deck

- 1 Champion Legend, which fixes the deck's **Domain Identity** (**103.1**).
  Every card must fall inside it; multi-domain cards need *all* their domains
  present (**103.1.b.3–4**).
- Main Deck ≥ **40** cards, including a Chosen Champion (**103.2**). Max **3**
  copies per name (**103.2.b**); max **3** Signature cards total, all matching
  the Legend's champion tag (**103.2.d**).
- Rune Deck: exactly **12** runes (**161.2.a**).
- Battlefields: count set by Mode of Play (**103**).

## Resources

Two distinct resources, and conflating them is the classic modelling error:

- **Energy** — pays numeric energy costs. No domain, no type (**163.1**).
- **Power** — pays *domain-associated* power costs (**163.2**). Power carries a
  domain, normally the producing rune's (**163.2.a.1**). Some Power is
  Universal and pays any domain (**163.2.b**).

A card's cost is therefore an **(energy, power)** pair, not a scalar — which is
why `data/normalize.py` requires both and why the npm source, which has no
power field at all, could not supply costs.

Basic runes (one per domain, **164.1**) each have exactly two abilities
(**164.2**):

- `[E]: [Reaction] — Add [1]` (exhaust for 1 energy)
- `Recycle this: [Reaction] — Add [C]` (recycle for 1 power of its domain)

Recycled runes return to the **Rune Deck**, not the Main Deck (**161.2.b**).
Runes are not Main Deck cards and are **not permanents** (**161.1**).

## Turn structure

Phases are rigid; the actions inside them may be taken in any order unless
specified (**303**). Game actions are performed strictly one at a time, never
simultaneously (**303.1–303.2**).

**Start of turn** (**315**):

1. **Awaken** — turn player readies everything they control that can ready
   (**315.1**).
2. **Beginning** — start-of-phase effects, then the **Scoring Step**: the turn
   player *Holds* every battlefield they control (**315.2**).
3. **Channel** — turn player channels **2** runes from their Rune Deck, or as
   many as remain (**315.3**).
4. **Draw** — turn player draws **1**. An empty Main Deck means **Burn Out**,
   and they still draw afterwards (**315.4.b.1–2**).

**Main Phase** (**316**): rune pools empty first — unspent Energy and Power are
**lost** (**316.3**) — then start-of-main effects (**316.4**). The Main Phase
has no defined structure (**316.5**).

**Ending Phase** (**317**), then the turn passes when all phases complete
(**306**).

A phase or step ends when the Chain is empty and the turn player cannot or
will not take a Discretionary Action (**305**).

## Turn state — the four-state machine

Two independent axes (**307–310**). Getting this wrong breaks every timing
question, so the engine models it explicitly:

| | **Open** (no Chain) | **Closed** (Chain exists) |
|---|---|---|
| **Neutral** (no showdown/combat) | default play window | only `Reaction` |
| **Showdown** (showdown/combat live) | only `Action` / `Reaction` | only `Reaction` |

- Chain exists ⇒ Closed (**309.1**); only `Reaction` may be played (**309.1.a**).
- Showdown or combat in progress ⇒ Showdown state (**308.1**); only `Action` or
  `Reaction` (**308.1.a**).
- By default cards are played only with priority, on your turn, in **Neutral
  Open** (**310.1.a**).

## Priority and Focus

Two separate permissions — a real trap:

- **Priority**: at most one player holds it; it is the exclusive right to take
  Discretionary Actions (**312, 312.1**). Limited Actions can always be taken
  when instructed, regardless of priority (**312.1.b.1**).
- **Focus**: at most one player holds it; it is permission to act in a
  **Showdown Open** state (**313, 313.1**).
- Gaining Focus also grants Priority (**313.2**). Passing Priority **retains**
  Focus (**313.3**). Focus without Priority cannot act (**313.4**). In a
  Neutral state nobody has Focus (**313.5**).

## Chains — the resolution engine

The Chain is a temporary non-board zone holding played cards and activated
abilities (**328**). Only one Chain exists at a time; anything played while one
exists joins it (**330.1–330.2**). Items are **Pending** until the "Check
Legality" step, then **Finalized** (**329.2–329.3**).

Resolution follows **HOT FEPR**: Handle Outstanding Tasks, then Finalize,
Execute, Pass, Resolve (**334**).

1. **Finalize** (**337**) — the controller of the *oldest* pending item
   finalizes it. Finalizing does **not** pass priority (**337.1.a**). A
   finalized Unit, Gear, or resource-Adding ability **resolves immediately**,
   jumping to step 4 (**337.2**).
2. **Execute** (**338**) — the priority holder either plays a legally-timed
   card/ability (which returns to Finalize) or passes.
3. **Pass** (**339**) — when all players pass in sequence with nothing added,
   go to Resolve.
4. **Resolve** (**340**) — the **newest** finalized item resolves fully (LIFO).
   If the Chain empties, play returns to an Open state (**340.2**).

## Showdowns

A Showdown is a window where players play spells alternately in an Open state
(**342**); each spell creates a Chain as normal (**342.1**).

- Opens when control of a battlefield becomes **Contested** during a Cleanup in
  a Neutral Open state (**344**).
- The player who applied Contested status gains Focus (**345**).
- When the last chain item resolves and the state reopens, **Focus passes**
  (**346**) — *except* when the chain opened from a triggered ability or an Add
  ability (**346.1**). The combat chain opens from triggers, so focus does not
  pass there.

## Combat

Combat occurs at a Cleanup when the Chain is empty, a combat is staged at a
battlefield, and no other showdown/combat is ongoing (**460**). Staged means
opposing players have units at a battlefield but the steps have not started
(**461**). Combat is strictly between **exactly two** players (**462**).

**Step 1 — Combat Showdown** (**464**): start-of-combat effects; establish
Attacker (whoever applied Contested) and Defender (**464.2.c**); Attacker gains
Focus (**464.2.d**); triggered abilities go on the chain — Attacker first, then
non-defenders in turn order, then Defender (**464.2.e.1**).

**Step 2 — Combat Damage** (**465**): if attackers and defenders both remain,
sum each side's **current Might**; starting with the Attacker, each player
*assigns* damage equal to their summed Might among the other's units
(**465.2**). **Assigning is not dealing** — all assigned damage is dealt
**simultaneously** afterwards (**465.2.c.1**). Units with `Tank` must be
assigned damage first; ties in assignment priority are the assigner's choice
(**626.1.d.4**).

**Step 3 — Resolution** (**466**): remove units with lethal damage (nonzero
damage ≥ Might); if both sides remain, attackers are **recalled**; the
battlefield is **Conquered** if defenders are gone but attackers remain, which
exchanges Control; clear Contested; clear all marked damage everywhere.

## Scoring

Scoring is gaining a point by seizing or maintaining battlefield control
(**468**). Two ways (**469**):

- **Conquer** — gaining control of a battlefield not yet Scored this turn.
- **Hold** — retaining control, checked in your Beginning Phase (**469.2**).

A battlefield may be Scored **once per player per turn** (**470**).

On Scoring (**471**): gain up to one point, then trigger that battlefield's
Score abilities — Conquer abilities on conquer, Hold on hold (**471.2**).

**The Final Point rule** (**471.1.b**) — easy to miss and outcome-relevant:
when Conquering would take you to within one point of, or past, the Victory
Score, you gain that final point **only if you have Scored every battlefield
this turn**. Otherwise you **draw a card instead**. Points from non-Conquer
sources are exempt (**471.1.a.1**).

## Layers

Continuous effects apply in the fixed order at **473–477**. Not yet transcribed
in detail — see RQ-3.

## Two rules that override everything

- **Golden Rule** (**002**): card text supersedes rules text.
- **Silver Rule** (**051**): card text is interpreted *according to* the rules,
  not as if it were rules text. "Card" in card text means Main Deck card —
  runes, legends and battlefields are not cards for card effects, though they
  are for these rules (**052**).
- **"Can't beats Can"** (**054**): prohibitions beat permissions; "only" is
  exclusive (**054.2**).

## Deliberately not covered here

Modes of play beyond 1v1 (**481–488**), conceding (**649**), XP (**728**),
additional turns (**734**), counters (**741**), and the full keyword glossary
(**804–829**, 25 keywords). The keyword glossary is transcribed per-keyword in
`DSL.md` as cards requiring each are scripted.


## Game Actions audit (410-444)

Worked through one at a time, checking each printed rule against the engine
and implementing what was missing. Card counts are mentions across the 521
playable cards with printed text, which is what decides the order.

| action | cards | state |
| --- | --- | --- |
| 414 Exhaust | 85 | implemented |
| 415 Ready | 84 | implemented |
| 413 Draw | 71 | implemented |
| 417 Deal | 66 | implemented |
| 434 Attach | 54 | implemented |
| 428 Kill | 41 | implemented |
| 416 Recycle | 36 | implemented (was a cost only) |
| 426 Buff | 35 | implemented |
| 439 Create / tokens | 34 | implemented |
| 421 Hide / 811 Hidden | 32 | implemented; RQ-14 and RQ-15 now closed |
| 422 Discard | 20 | implemented |
| 424 Reveal | 16 | implemented; RQ-16 now closed |
| 423 Stun | 14 | implemented |
| 430 Channel | 13 | implemented (was phase-only) |
| 425 Counter | 5 | implemented |
| 432 Double | 4 | **not implemented** |
| 427 Banish | 4 | implemented |
| 418 Heal | 4 | implemented |
| 437 Prevent | 2 | **not implemented** |
| 435 Detach | 2 | implemented |
| 433 Swap | 1 | **not implemented** |
| 436 Predict, 440 Burn, 441 Empower, 443 Skip | 0 | no card needs them |

**What the audit found**, beyond filling gaps -- these were live bugs, not
missing features:

* **431.2 Burn Out ran two of its four steps.** A player whose deck emptied
  gave up a point and then stayed permanently deckless: the trash was never
  recycled and the draw never completed.
* **167 emptied the rune pool at the start of a Main Phase but not at the end
  of a turn**, so power survived into the opponent's Awaken, Beginning,
  Channel and Draw phases -- where Reactions can spend it.
* **471.2.b Hold abilities did not exist**, so ten cards' printed text did
  nothing, including one whose entire text is "When I hold, you score 1
  point."
* **471.2 fired Conquer triggers everywhere**, not at the battlefield that
  scored, so gear on a unit at one battlefield triggered on a score at
  another.
* **423.1.b could not be expressed at all** -- there was no way for a unit to
  contribute no Might to combat damage.
* **719.5 was enforced in one of three code paths**, so a unit bounced to hand
  left its Equipment attached to a card that was no longer in play.

The last one was found by `engine/invariants.py` rather than by reading, which
is the argument for checking structural truths after every action of every
game rather than sampling them with hand-written cases.
