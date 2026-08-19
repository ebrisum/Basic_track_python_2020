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
| 432 Double | 4 | implemented |
| 427 Banish | 4 | implemented |
| 418 Heal | 4 | implemented |
| 437 Prevent | 2 | implemented |
| 435 Detach | 2 | implemented |
| 433 Swap | 1 | implemented |
| 436 Predict, 440 Burn, 441 Empower, 443 Skip | 0 | no card needs them |

**Every Game Action any card in the pool needs is now implemented.** Double,
Prevent and Swap were the last three. Each is exercised by tests driving the
primitive directly, because none of the seven cards that use them is scripted
yet -- a Game Action tested only by the cards that happen to be scripted today
is tested by accident.

Prevent (437) was the substantial one: a delayed replacement effect (437.7)
that tracks a Prevent Value per unit, reduces the next damage by it (437.2),
spends itself down as it absorbs (437.3), treats fully-prevented damage as
never dealt (437.4), and raises the bar for lethal damage assignment in
combat (437.5.a) -- with "All" never lethal at any amount (437.5.b).

Implementing it turned up a detail worth stating: 437.5 says damage can still
be *assigned* to a prevented unit, so the assignment spends the attacker's
Might whether or not Prevent then eats it. Subtracting only what got through
would have let a protected unit soak an attack for free.

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

## Turn structure audit (300-348)

The same method applied to the second series: read one rule, ask whether the
engine does what it says, write a test, fix or log. The Game Actions audit
(410-444) found five live bugs. This one found **four**, and the first is the
largest rules gap the project has had.

| Rule | What it says | Was |
| --- | --- | --- |
| **323.6 / 190.4.c** | a player with no Units at a Battlefield loses Control at the next Cleanup, in an Open State, with nothing running there | **missing entirely** |
| **337.4** | after finalizing, the controller of the next item on the chain gains Priority -- the caster | gave it to the opponent |
| **347.2.a** | the Showdown ends when all players have passed once *in sequence* | a play did not break the sequence |
| **317.2.b** | the Ending Phase inserts "3c. Heal all Units" | only combat healed (466.1.a.1) |

### 323.6 in particular

Control was granted permanently once taken. A battlefield therefore cost
nothing to keep and scored a Hold point every Beginning Phase from an empty
field, which removes the central tension of the game: units cannot both
garrison and attack.

Measured before the fix: **2,209 of 8,408** sampled Neutral Open States had a
battlefield held by a player with no units on it. Mean game length goes from
**14.2 turns to 25.3** once control has to be earned every turn.

Several existing tests had been written against the buggy behaviour and had to
garrison their battlefields to keep working. That is worth naming rather than
quietly fixing: they were asserting about positions the rules delete on the
next cleanup, which is how a wrong engine makes its own tests agree with it.

### What the audit did *not* cover

`_run_automatic_phases` treats Awaken, Beginning, Channel, Draw and Ending as
non-interactive. 315.2.a.1, 316.4 and 317.1.a all say "game effects take
place" at those points, which becomes a real window as soon as a card triggers
there. Nothing in the scripted pool does yet.

315.1.b readies every Game Object the turn player controls, including cards in
non-board zones; 415.1 scopes readying to the board. Harmless today because
`exhausted` is set explicitly whenever a card enters the board, and logged
here rather than left to be discovered.

### Still unaudited

The **700-series** (attachment, keywords) and **800-series** (the keyword
glossary) have not had this treatment. Two series audited, nine live bugs
between them. It would be unreasonable to assume the remaining two are clean.

## Attachment and keywords audit (700-732)

The third series. Four findings, two of them live.

| Rule | What it says | Was |
| --- | --- | --- |
| **426 / 701-705** | a Buff is a *counter*: one per unit, +1 Might, spendable, removed on leaving play | modelled as an unbounded Might modifier |
| **136.2.c / 718.3** | an attached card's Effect Text is appended to the **host's** rules text, so "me" is the host | fired with the gear as "me" |
| **718.2 / 724** | Rules Text is Inactive while attached; Effect Text is Inactive while loose | both live at all times |
| **719.3.a** | attachments move with the Top-Most Card | the Standard Move carried them; the combat recall did not |

### Buffs

53 cards reference buffs — "While I'm buffed", "for each buffed friendly
unit", "spend a buff to..." — and the set prints a rules-reminder card,
OGN-357, whose entire text is "A unit may have no more than one buff at a
time." The DSL had an effect called `Buff` that added N Might, which is a
different mechanic wearing the same name. `PlaceBuff` and `SpendBuff` are now
the Game Actions and `ModifyMight` is the raw modifier; naming them apart is
what stops them being conflated again.

`state.buff()` **reports** whether the counter landed. 426.1.c is the reason:
a unit that already holds a buff can still be chosen but is not buffed, so
"if it was buffed this way, draw 1" and "when you buff me" must both be able
to tell.

### The two that found each other

Making buffs real immediately exposed 136.2.c: Warmog's Armor's "When I
conquer, buff me" was buffing the *gear*, and a gear cannot hold a buff at
all. The invariant checker caught it within a few random games.

Fixing that exposed 718.2/724: with the trigger now landing on the host, it
was still firing while the gear sat loose in a base — where 724 makes its
Effect Text Inactive — and the Equip ability was still offered while worn,
where 718.2 makes its Rules Text Inactive. An equipped gear could re-target
itself onto any other unit, every turn, for its cost.

### Reading versus fuzzing, both ways round

719.3.a is the pair to the earlier 719.5 bug, and the two were found by
opposite methods:

* **719.5** — a rule enforced in one code path out of three. Found by the
  invariant checker, after reading had missed it.
* **719.3.a** — the same shape. Found by reading, and *unreachable* by
  fuzzing: 150 Greedy-vs-Random games produced zero instances, because it
  needs an Equipment attached to an attacker in a combat both sides survive.

Neither method subsumes the other. Both fixes now route through one function
(`leave_board`, `move_to`) so the shape cannot recur.

### Missing features, not bugs

* **727 Dependent Keywords** (LEGION, Level N) — 12 cards, none scripted,
  none in the starter decks.
* **728-732 XP** — 6 cards, same.

Neither influences the engine today, so neither was built speculatively.

### Still unaudited

The **800-series** keyword glossary, apart from the keywords the engine
already acts on (Assault, Tank, Backline, Ganking, Accelerate, Shield,
Deflect, Hidden, Equip, Quick-Draw). Three series audited, **thirteen live
bugs** between them.

## Keyword glossary audit (800-829)

The fourth series. It produced two implementations and one finding much
larger than a keyword.

### The implemented keywords check out

Assault (807), Shield (814), Tank (815), Backline (826), Ganking (810),
Accelerate (805), Hidden (811), Equip (818), Quick-Draw (819), Action (806)
and Reaction (813) were each read against the engine. Tank-before-middle-
before-Backline damage ordering matches 815.1.c.2 and 826.4.b; Assault and
Shield sum printed and granted instances per 807.2 / 814.2 and apply only
while attacking or defending. One wrong citation was corrected in passing
(`_assign_actions` cited 626.1.d.4; the rule is 815.1.c.2).

### The finding: RQ-19

Auditing **Deflect (809)** — a mandatory additional cost of "[Deflect Value]
more ... for each time they choose [me]" — showed it is not merely
unimplemented but *unimplementable* as the engine stands, because there is no
target at the moment costs are paid.

That traces back to **355.8**: "In order to put a spell or ability on the
chain, valid choices must be made for all targets." The engine defers target
choice to resolution. Verified directly: after `PlayCard`, `state.awaiting`
is `None`; the choice appears only once the chain has emptied.

Four rules fail together as a result — a spell cannot be fizzled (359.3.e.5),
Deflect cannot be charged (809.1.d), opponents respond blind, and "when you
choose me" triggers (383.4.b.3) cannot fire on time. It is written up as
RQ-19 and flagged rather than fixed: it is a redesign of the play pipeline,
not a rule patch, and it is the right kind of decision to hand over rather
than make quietly.

### Two built: Temporary (816) and Unique (825)

Both were unimplemented and neither depends on RQ-19, so they were built
rather than logged.

**816 Temporary** -- "At the start of this permanent's controller's Beginning
Phase, **before scoring**, kill this." Without it a permanent meant to last
one round lived forever. The ordering is load-bearing and has a second step
the rule does not spell out: killing the permanent makes a Cleanup an
Outstanding Task (319.6), so step 4 (323.6) runs *before* 315.2.b scores. A
Temporary unit that was a battlefield's only defender therefore cannot Hold
it on the way out. That only became true once 323.6 existed -- the two fixes
compose.

816.1.c scopes the trigger to *its controller's* Beginning Phase, so a
Temporary permanent survives the opponent's turn; 816.2.a's redundancy is
free, since a card killed once has no location to be killed from again.

**825 Unique** -- "A deck can contain only one card of a given name if the
card has Unique", a tighter limit than 103.2.b's three, checked alongside it
in deck validation. 825.4 confirms it does nothing else during play.

### Still unimplemented, by pool reach

| Keyword | Cards in pool | In the starter decks | Blocked on |
| --- | --- | --- | --- |
| Repeat (820) | 17 | 1 | 820.2's choices are now possible; not yet built |
| ~~Weaponmaster (821)~~ | 16 | 0 | **built** |
| Deathknell (808) | 15 | 0 | — |
| Empower / Empowered (827-828) | 13 | 0 | — |
| Legion (812 / 727) | 12 | 0 | — |
| Vision (817) | 7 | 0 | — |
| Quick-Draw (819) | 6 | 0 | derived already; no scripted card uses it |
| Level (824) | 2 | 0 | 728-732 XP |

Ambush (822), Hunt (823) and Flow (829) appear on no card in the cached pool
at all.

Deflect (809) has since been built -- see below -- which removed the largest
entry from this table. Repeat is no longer blocked either; it simply has not
been written.

### Weaponmaster (821)

Built, and derived from the printed keyword like Quick-Draw (819.1.d), so the
16 cards carrying it need no per-card script.

The interaction flagged earlier turned out to be the interesting part.
821.1.c says "Necessary portions of its Rules Text are no longer Inactive if
they are currently Inactive", and 725.3 says the keyword can reference an
attached card's Equip ability. That is an **explicit exception to 718.2**: an
Equipment already worn by another unit cannot normally have its Equip ability
activated, and Weaponmaster moves it anyway. The implementation therefore
reads the cost from the card's Equipment profile rather than from its
(Inactive) activated ability, and a test moves a worn Equipment between hosts
to prove 718.2 does not block it.

821.1.c.5 is the whole error path -- if the cost cannot be paid, or the card
cannot be detached or attached, "it stays in its current location, Attached
to anything it was already Attached to" -- so nothing changes until payment
succeeds.

One detail worth pinning, because it makes a test vacuous if missed: the
[A] reduction is a full Power, so an Equipment costing exactly one Power is
**free** to a Weaponmaster. The can't-afford test needs a more expensive card
to mean anything.

### Deflect (809), and the order of the play steps

Deflect is a **mandatory additional cost priced off the declared target**
(809.1.d), so it was unimplementable while targets were chosen at resolution.
Fixing RQ-19 unblocked it, but not on its own: the engine still paid before it
targeted, where 353-359 print the order

    354 move to chain -> 355 make choices -> 356 total cost -> 357 pay

Putting the steps in that order is what gave Deflect something to attach to.
`deflect_cost` sums 809.2's multiple instances, reads an omitted value as 1
(809.1.b.3), taxes only an opponent's permanents (809.1.c), and is paid in
Power of any domain (809.1.c.1).

358 Check legality falls out of it: a target whose tax cannot be paid is not
offered, and a spell with no affordable target set is not a legal play.

Reordering the steps exposed a second defect immediately -- an activated
ability's chain item was charged its *source card's* play cost on top of the
ability cost it had already paid. The whole suite went red at once, which is
the useful kind of failure.

### What the 10,000-game run added

The acceptance run then found an **eighteenth**, which every smaller run had
missed: a `ChoiceRequest` lists the options legal when it is *raised*, and the
cleanup at the end of that same action can kill one before the answer arrives
in a later one. An Attach bound a gear to a host that had already left the
board, leaving it in a base with no location (107.1.c, 718.5).

359.3.e.5 already covered this for targets declared at play time; the
resolution-time path was the half that did not re-check. It does now, in
`_targets`, so every effect gets the same treatment rather than Attach alone.

It needed a game where a cleanup kills a unit between a choice being offered
and answered. Sampling 500 end-states out of 10,000 games caught it, which is
the argument for the large runs being large.

## Triggered abilities on the Chain (383.3)

Not an audit finding so much as the last structural gap the audits pointed
at. **383.3**: "When a Condition is met, a Triggered Ability behaves like an
Activated Ability and is placed on the Chain." The engine ran a trigger's
effects inline, out of the game action that caused them.

Three rules failed together: **383.3.c** (a trigger can be responded to, in a
Closed or Open state, on any player's turn), **340.1** (triggers resolve
newest-first like any chain item), and **355.5.b** (a trigger's targets are
declared when *it* is finalized).

**341 of 526 playable cards print trigger wording** -- the most common
mechanic in the game -- and only 9 scripted abilities are triggers today.
That gap is exactly why the change was cheap now and would not have been
after scripting.

A spell's own ON_RESOLVE text is not a triggered ability (359) and stays
inline; two tests pin that, or every spell would take two resolutions.

**A second defect fell out.** A trigger firing during an automatic phase --
471.2's Hold abilities in the Beginning Phase -- left the turn walking on to
Channel, Draw and Main with a finalized item still on the chain, offering
Main-phase actions in what 309.1 calls a Closed State. 354.4 says outstanding
tasks are finished first, so the sequence now stops, opens a window, and
resumes where it left off.

### Tally

Four series audited: **twenty live bugs**, three keywords built (Temporary,
Unique, Deflect), and both structural gaps closed -- RQ-19's targeting and
383.3's triggers.
