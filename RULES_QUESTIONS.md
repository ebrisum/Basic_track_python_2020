# Rules questions and open blockers

Every unresolved rules decision goes here with: the situation, the options,
which was provisionally implemented, and the estimated effect on outcomes.

Approximations are allowed. Hidden approximations are not.

---

## BLOCKER-1 — No network access to any data source or to the rules

**Status:** open, blocking Session 1 steps 2, 3, and 4.

**Situation.** This session runs in a remote container whose egress proxy
enforces an organization allowlist. All four sources named in the brief are
refused at the CONNECT stage with `403 Forbidden`:

| Host | Purpose | Result |
| --- | --- | --- |
| `api.riftcodex.com` | primary card data | 403 at proxy |
| `riftscribe.gg` | cross-check card data | 403 at proxy |
| `docs.google.com` | community spreadsheet | 403 at proxy |
| `riftbound.gg` | core rules PDF | 403 at proxy |

Verified three ways: `curl` (exit 56, tunnel refused), the `WebFetch` tool
(`EGRESS_BLOCKED`), and the proxy's own status endpoint, which logs each
denial under `recentRelayFailures`. Reachable hosts are limited to package
registries (`pypi.org`, `registry.npmjs.org`, …) and
`raw.githubusercontent.com`. `WebSearch` returns result listings — titles and
URLs — but cannot retrieve page or document bodies.

The proxy README is explicit that a 403 is an organization policy denial and
must be reported rather than retried or routed around. So this is not
something the session can resolve on its own.

**Why this stops the work rather than slowing it.** Session 1's remaining
deliverables are `RULES_SUMMARY.md` and a proposed DSL primitive list. The
brief requires the summary to trace to the comprehensive rules and the
primitive list to be *derived from the actual card text of the two Milestone 1
decks*. Neither document exists yet, and neither can be honestly written from
model recall:

- A rules summary written from memory would carry no citations, which is the
  one thing the brief says every implemented rule must have. It would also be
  the document everything else is implemented against — an error there
  propagates into the engine, into the DSL, and into every number the system
  eventually produces, while looking authoritative the whole way down.
- A primitive vocabulary is only meaningful as a *closure* over a specific
  card pool. Derived from recall instead of read text it would be an
  unfalsifiable guess, and the "frozen primitive list" gate in the brief —
  the thing that keeps this project at weeks rather than months — would be
  gating on nothing.

Writing either from memory is precisely the hidden approximation this file
exists to prevent, so neither was written. `RULES_SUMMARY.md` and `DSL.md`
are present as stubs recording what they are waiting on.

**Options, for the repo owner to pick from.**

1. Allowlist the four hosts on the environment's egress policy, then re-run
   `python data/fetch.py --all`. Cleanest — everything downstream is already
   built and tested.
2. Commit the source data into this repo directly (the spreadsheet CSVs, an
   API dump, the rules PDF). `raw.githubusercontent.com` is reachable, and
   `data/normalize.py` reads only from `data/raw/`, so this unblocks
   everything with no code change beyond the three adapters.
3. Paste the two decklists' card text and the relevant rules sections into a
   message. Enough to derive the DSL primitives and hand-script cards; not
   enough to build the full `cards.json`.

**Provisionally implemented:** nothing. No rules code was written, per the
brief's instruction not to write engine code in Session 1.

**Estimated effect on outcomes:** total — no simulation can run until at least
one option lands.

---

## Open rules questions

None recorded yet — no rules have been implemented. The brief flags these
areas as expected trouble spots, and they are reproduced here so the list is
ready to fill as each is decided:

- Response windows and priority passing during showdowns
- Simultaneous trigger ordering
- Exact timing of battlefield hold/conquer scoring
- Rune deck mechanics and resource payment edge cases
- Champion death, recycling, and re-deployment
