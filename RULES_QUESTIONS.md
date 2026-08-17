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
