# Effect DSL specification

**Status: proposed primitive vocabulary, for review. Interpreter not written.**

Per the brief, this list is derived from card text and the rules, and is the
gate before anything is scripted in bulk.

## How this was derived

Not invented. Three inputs, all in-repo:

1. **The rules define their own action vocabulary.** Core Rules v1.4 section
   **407 Game Actions** enumerates exactly 32 named actions at rules
   **413–444**: Draw, Exhaust, Ready, Recycle, Deal, Heal, Play, Move, Hide,
   Discard, Stun, Reveal, Counter, Buff, Banish, Kill, Add, Channel, Burn Out,
   Double, Swap, Attach, Detach, Predict, Prevent, Replace, Create, Burn,
   Empower, Disempower, Skip, Pay.
2. **Card text is written in those terms**, measured over the 888 cards in
   `data/cards.json` that have rules text.
3. **Triggers reference them by name** — rule **411.4**: "If an ability
   triggers when *you* do something, it triggers when a Game Action that you
   are responsible for occurs."

That third point is decisive. Because triggers key off game actions, the
primitives *must* be the game actions — any primitive set that doesn't
correspond one-to-one cannot express "when you move an enemy unit" correctly.
So the proposal is: **adopt the game's own Game Actions as the effect
primitives verbatim, rather than designing a vocabulary.**

The brief predicted "roughly 30–40 primitives." The rules define 32. That is a
convergence worth taking seriously.

## Layer 1 — Effect primitives (the 32 Game Actions)

Measured frequency in real card text, so scripting order follows real payoff:

| Primitive | Rule | Uses | Primitive | Rule | Uses |
|---|---|---|---|---|---|
| `play` | 419 | 586 | `discard` | 422 | 32 |
| `exhaust` | 414 | 204 | `channel` | 430 | 27 |
| `add` | 429 | 181 | `banish` | 427 | 16 |
| `pay` | 444 | 160 | `counter` | 425 | 9 |
| `move` | 420 | 145 | `predict` | 436 | 9 |
| `draw` | 413 | 140 | `double` | 432 | 7 |
| `buff` | 426 | 140 | `heal` | 418 | 6 |
| `ready` | 415 | 138 | `detach` | 435 | 5 |
| `deal` | 417 | 115 | `swap` | 433 | 2 |
| `kill` | 428 | 82 | `prevent` | 437 | 2 |
| `attach` | 434 | 78 | `replace` | 438 | 2 |
| `recycle` | 416 | 64 | `burn_out` | 431 | 0 |
| `hide` | 421 | 41 | `create` | 439 | 0 |
| `stun` | 423 | 38 | `burn` | 440 | 0 |
| `reveal` | 424 | 37 | `empower` / `disempower` | 441/442 | 0 |
| | | | `skip` | 443 | 0 |

The six zero-frequency actions are rules-internal (`burn_out` fires from deck
exhaustion at **315.4.b.1**, not from card text) or belong to sets whose text
this dataset may not fully cover. **Implement them last, on demand.**

Each primitive carries the rules' own distinctions, which the engine must not
flatten:

- `deal` vs `assign` — combat *assigns* damage, then deals it simultaneously
  (**465.2.c.1**). Two different operations.
- `kill` is attributable to a spell/ability separately from player
  responsibility (**411.5**).
- `recycle` sends runes to the **Rune Deck**, not the Main Deck (**161.2.b**).
- `add` produces resources and resolves *immediately* off the chain
  (**337.2**) — it is not an ordinary effect.

## Layer 2 — Selectors

**Riftbound has no "target" keyword.** The string `target` appears **0 times**
in 888 card texts; selection is expressed as "choose" (127), "a unit", "an
enemy" (88), "a friendly" (84), "you control" (95), "each" (104), "another"
(42), "up to" (14), "among" (18).

So the selector layer is a filter algebra, not a targeting system:

- `SELF` — cards refer to themselves in the first person (**053**): units and
  legends say "I/me", gear and spells "this", battlefields "here".
- Scope: `a` (choose one) · `each` · `all` · `up_to(n)` · `another`
- Controller: `friendly` · `enemy` · `any`
- Type: `unit` · `spell` · `gear` · `rune` · `battlefield` · `legend` · `token`
- Location: `at(battlefield)` · `at_base` · `here` · `anywhere` (**197–200**)
- State: `ready` · `exhausted` · `hidden` · `stunned` · `attacker` · `defender`
- Property: `might >= n` · `cost <= n` · `domain(d)` · `tag(t)` · `named(x)`
- `is_champion` · `is_chosen_champion` (**103.2.a.3**)

`random` appears **0 times** in card text — no effect-level randomness, which
matters for determinism: the only RNG in a game is shuffling.

## Layer 3 — Triggers and timing

Measured lead-ins: `When` (320), `If` (24), `While` (21), `As you play` (12).

- `on(game_action, filter)` — the dominant form (**382**), keyed to a game
  action per **411.4**
- `while(condition)` — static/continuous (**363** Passive Abilities)
- `as_you_play` — additional costs and replacements (**367** Replacement
  Effects)
- `at(phase, step)` — e.g. `at(beginning, scoring)` (**315.2.b**)
- `activated(cost)` — **376**
- `reflexive` — **386**
- `delayed` — **389**
- `linked` — **393**

Simultaneous triggers order by **turn order starting from the turn player**
(**303.2.a**); in combat, Attacker first, then non-defenders, then Defender
(**464.2.e.1**).

## Layer 4 — Costs and conditions

Costs are an **(energy, power)** pair, never a scalar (**163**, and see
`RULES_SUMMARY.md`):

- `cost(energy=n, power=[domains])`
- `additional_cost(action)` — "you may discard a card as an additional cost"
- `reduce_cost(n)`
- `exhaust_self` / `recycle_self` as costs (**164.2**)
- `if(condition) then ... else ...`
- `may(...)` — optional, distinct from mandatory
- `once_per_turn` (**470** for scoring)

## Layer 5 — Modifiers

- `buff(might=n, duration)` where duration ∈ `this_turn` · `permanent` ·
  `while_condition` (**426**, **701**)
- `grant_keyword(kw, n?, duration)` — keywords take numeric parameters
  ("ASSAULT 3")
- `set_stat` / `cant(action)` — "Can't beats Can" (**054**)

## Layer 6 — Keywords

25 keywords at **804–829**, all with rules definitions. Frequency-ordered, so
Milestone 1 implements only what the two decks use:

`Reaction` 107 · `Action` 81 · `Deflect` 53 · `Hidden` 48 · `Equip` 48 ·
`Repeat` 48 · `Ganking` 42 · `Assault` 41 · `Accelerate` 36 · `Temporary` 31 ·
`Shield` 30 · `Deathknell` 27 · `Tank` 27 · `Level` 26 · `Ambush` 18 ·
`Weaponmaster` 17 · `Hunt` 14 · `Legion` 13 · `Vision` 12 · `Quick-Draw` 6 ·
`Backline` 6 · `Unique` 3

`Action` and `Reaction` are not ordinary keywords — they are the **timing
permissions** that drive the four-state machine (**308.1.a**, **309.1.a**), so
they belong in the engine's state model, not in the card-effect layer.

Keyword reminder text is parenthetical and redundant with the glossary; the
parser should **strip it** rather than interpret it.

### The one exception: Equip

**818.1.c.2** does not merely describe Equip, it *defines* it:
"Equip is functionally short for `[Cost]: Attach this gear to a unit you
control.`" The reminder text on every Equipment card is that expansion,
printed — `(1 Fury: Attach this to a unit you control.)`. So `cards/gear.py`
reads the cost out of the reminder rather than the `[EQUIP ...]` marker.

This is a measurement, not a preference. The marker is printed at least six
ways across sets (`[EQUIP Fury]`, `[EQUIP 1, Fury]`, `[EQUIP1, Calm]`,
`[EQUIP 1 Body]`, `[Equip] 1 calm rune`, bare `Equip 1 body rune`); the
reminder's grammar is identical on all of them. Parsing the reliable half of
the card is what makes 32 Equipment playable with no per-card Python.

Cards whose Equip cost is not purely resources print `(Pay the cost: ...)`,
which states no cost at all. Those are reported unparsed with a note and stay
inert — never handed a guessed cost.

## What is deliberately absent

No `custom_script` escape hatch, per the brief. If a card cannot be expressed
here, the DSL is wrong and I escalate.

## Open questions for review

1. **Adopting the rules' vocabulary wholesale** — this is the load-bearing
   decision. It gives every primitive a citation and makes trigger semantics
   correct by construction, at the cost of a few primitives no card currently
   uses. Confirm before I build the interpreter.
2. **`buff` covers a lot of ground** (140 uses) and may need splitting once
   the layer system (**473–477**) is transcribed. See RQ-3.
3. **Keyword parameterisation** — "ASSAULT 3" suggests keywords are
   `(keyword, value)` pairs uniformly. Not yet verified against the glossary.

## Process from here

1. You supply the two Milestone 1 decklists.
2. I read every card's text in them and check it against this vocabulary.
3. Any card needing a primitive not listed → **this file is updated first**,
   and I flag it.
4. Hand-script 25 cards spanning every primitive used, each with a unit test.
5. Freeze the list; only then script in bulk.
