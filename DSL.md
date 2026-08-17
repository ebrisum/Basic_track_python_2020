# Effect DSL specification

**Status: not written. Blocked on BLOCKER-1 (see `RULES_QUESTIONS.md`) and on
the two Milestone 1 decklists.**

This document specifies the primitive vocabulary that card effects compile
into. It is the gate: no card is scripted in bulk until the primitive list
here is frozen, and no interpreter is written until this spec exists.

## Why it is empty

The brief is specific that the primitive list must be *derived from the actual
card text of the two Milestone 1 decks* — read the ~60–80 cards, then extract
the minimal vocabulary that covers them. That ordering is the whole point: a
primitive set is only meaningful as a closure over a known card pool, and its
value comes from being falsifiable ("this card needs a primitive not in the
spec" is a real signal only if the spec was derived from real text).

Two inputs are missing:

1. **The decklists** — to be supplied.
2. **The card text** — `api.riftcodex.com`, `riftscribe.gg`, and
   `docs.google.com` are all refused by this environment's egress proxy, so no
   card text has been read.

A primitive list written from model recall would be an unfalsifiable guess
dressed as a derivation, and freezing it would gate the project on nothing.
So none was written.

## Expected shape, once inputs land

The brief anticipates roughly 30–40 primitives spanning these categories.
Reproduced here as the checklist to work through, **not** as a proposed
vocabulary:

- Cost and payment
- Targeting and selection
- Zone movement
- Stat modification
- Keyword grant
- Damage and destruction
- Triggered abilities
- Static / continuous effects
- Replacement effects
- Player actions (draw, recycle, energy, points)

## Process, restated

1. Read the card text of every card in the two Milestone 1 decks.
2. Derive the minimal primitive vocabulary that covers all of it.
3. Write it into this file — before the interpreter.
4. Hand-script 25 cards spanning every primitive.
5. If a card forces a primitive not in the spec: update this file first, and
   flag it to the repo owner.
6. Only once those 25 pass their tests against a frozen list does anything
   else get scripted.

## Non-negotiable

No escape hatch that executes arbitrary Python per card. If a card cannot be
expressed in the DSL without a special case, the DSL is wrong — escalate
rather than special-case.
