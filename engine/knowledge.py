"""Deduction from public information -- what a player can legitimately know.

Riftbound is a closed system: a Main Deck is exactly 40 cards (103.2) fixed
before the game, a Rune Deck exactly 12 (161.2.a), and most zones are public.
So a player does not have to guess at the opponent's hand from nothing — they
can *subtract*.

    unseen(P) = decklist(P) - everything of P's that is publicly visible

and P's hand is a subset of that. Everything below is arithmetic on multisets
of card ids; nothing here reads a zone the observer is not entitled to see.

## What is public (107-108, 128)

- Board, both bases, both battlefields: public (107.1.d, 107.2.c)
- Both trashes: public, contents not just counts (108.2.d)
- Champion Zones and Legend Zones: public (108.3.e, 107.4)
- Banishment: public
- Own hand: known to its owner only
- **Opponent's hand**: hidden — but bounded by the unseen pool
- Main Deck and Rune Deck *order*: secret (108.4.d, 108.5.d); contents are
  deducible, order is not

## The decklist assumption

Subtracting from a decklist requires knowing it. That is exactly true for the
project's actual goal — a matchup matrix against a database of known
tournament decks — and it is *not* true of game one against an unknown
opponent. `KnownDecklists` makes the assumption explicit and switchable rather
than baking an information advantage in silently. See RQ-12.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum


class KnownDecklists(str, Enum):
    """Whose decklist the deducer is allowed to subtract from."""

    OWN_ONLY = "own_only"      # realistic game one: you know only your own list
    BOTH = "both"              # known field decks: the matchup-matrix case


@dataclass(frozen=True)
class Knowledge:
    """What one observer can deduce about one subject player."""

    observer: int
    subject: int
    # Cards whose location the observer knows exactly.
    located: Counter
    # Cards known to be in {hand + main deck} but not which -- the pool a
    # determinization must sample from.
    unseen: Counter
    hand_size: int
    deck_size: int
    decklist_known: bool

    def unseen_total(self) -> int:
        return sum(self.unseen.values())

    def probability_in_hand(self, card_id: str) -> float:
        """P(a specific copy is in hand), assuming hand is a uniform subset.

        With `n` unseen cards of which `h` are in hand, any particular unseen
        card is in hand with probability h/n. Exact when the observer has no
        further information, which is the case here -- deck order is secret.
        """
        total = self.unseen_total()
        if total <= 0 or self.hand_size <= 0:
            return 0.0
        return min(1.0, self.unseen.get(card_id, 0) * self.hand_size / total)

    def expected_in_hand(self, predicate) -> float:
        """Expected number of cards in hand satisfying `predicate(card_id)`."""
        total = self.unseen_total()
        if total <= 0 or self.hand_size <= 0:
            return 0.0
        matching = sum(n for cid, n in self.unseen.items() if predicate(cid))
        return matching * self.hand_size / total


# 052 / 161.1 -- runes, legends and battlefields are not Main Deck cards, so
# they take no part in decklist arithmetic. A channeled rune sitting in a base
# is public, but counting it here would inflate the deduced 40-card list every
# time one was channeled.
MAIN_DECK_TYPES = frozenset({"unit", "spell", "gear"})


def _is_main_deck(state, instance_id: int) -> bool:
    card = state.db.get(state.cards[instance_id].card_id)
    return card is not None and card.type in MAIN_DECK_TYPES


def public_counts(state, subject: int) -> Counter:
    """Every Main Deck card of `subject`'s whose location is public (107-108)."""
    counts: Counter = Counter()
    player = state.players[subject]

    # Trash is public contents, not just a count (108.2.d).
    for instance_id in player.trash:
        if _is_main_deck(state, instance_id):
            counts[state.cards[instance_id].card_id] += 1
    for instance_id in player.banishment:
        if _is_main_deck(state, instance_id):
            counts[state.cards[instance_id].card_id] += 1
    # Champion Zone (108.3.e); the Legend Zone holds the legend, which is not
    # a Main Deck card and so never enters this arithmetic.
    for instance_id in player.champion_zone:
        if _is_main_deck(state, instance_id):
            counts[state.cards[instance_id].card_id] += 1
    # Anything on the board, in either base or at a battlefield (107.1.d).
    for ref in state.cards.values():
        if ref.location is not None and ref.owner == subject:
            if _is_main_deck(state, ref.instance_id):
                counts[ref.card_id] += 1
    # Items on the chain are public too (108.1.b).
    for item in getattr(state, "chain", []):
        ref = state.cards[item.instance_id]
        if ref.owner == subject and _is_main_deck(state, ref.instance_id):
            counts[ref.card_id] += 1
    return counts


def decklist_counts(state, subject: int) -> Counter:
    """The subject's full Main Deck as constructed, by card id.

    Reconstructed from every instance the engine minted for that player, which
    is the decklist by definition -- not a peek at a hidden zone.
    """
    counts: Counter = Counter()
    player = state.players[subject]
    main_deck_ids = set(player.main_deck) | set(player.hand)
    for instance_id in main_deck_ids:
        counts[state.cards[instance_id].card_id] += 1
    for card_id, extra in public_counts(state, subject).items():
        # Champion-zone and board copies came from the same 40 cards.
        counts[card_id] += extra
    # The Chosen Champion starts in the Champion Zone but is part of the deck
    # (103.2.a.1); it is already counted above via champion_zone.
    return counts


def deduce(state, observer: int, subject: int,
           mode: KnownDecklists = KnownDecklists.BOTH) -> Knowledge:
    """What `observer` can work out about `subject`'s hidden cards."""
    player = state.players[subject]
    own = observer == subject
    decklist_known = own or mode is KnownDecklists.BOTH

    located = public_counts(state, subject)
    if own:
        # You know your own hand exactly, so it is located, not unseen; what
        # remains hidden is your own deck's contents-and-order.
        for instance_id in player.hand:
            located[state.cards[instance_id].card_id] += 1
        unseen = Counter()
        for instance_id in player.main_deck:
            unseen[state.cards[instance_id].card_id] += 1
    elif decklist_known:
        # Subtract the public from the list: what is left is hand + deck.
        unseen = decklist_counts(state, subject) - located
    else:
        # No list to subtract from; only the sizes are public.
        unseen = Counter()

    return Knowledge(
        observer=observer,
        subject=subject,
        located=located,
        unseen=unseen,
        hand_size=len(player.hand),
        deck_size=len(player.main_deck),
        decklist_known=decklist_known,
    )


# --------------------------------------------------------------------------
# Draw mathematics -- a 40-card deck makes these exact, not estimates.
# --------------------------------------------------------------------------


def hypergeometric_at_least_one(successes: int, population: int, draws: int) -> float:
    """P(at least one success) drawing `draws` from `population` without
    replacement, where `successes` of the population are successes.

    The complement of drawing none, computed as a product of falling ratios so
    it stays exact in floating point for the sizes this game uses.
    """
    if successes <= 0 or draws <= 0 or population <= 0:
        return 0.0
    if draws >= population:
        return 1.0
    failures = population - successes
    if failures < draws:
        return 1.0
    probability_none = 1.0
    for i in range(draws):
        probability_none *= (failures - i) / (population - i)
    return 1.0 - probability_none


def draw_odds(state, player: int, predicate, draws: int = 1) -> float:
    """P(drawing at least one card satisfying `predicate` in `draws` draws).

    Uses only the player's own deck contents, which they are entitled to know
    by subtraction even though the order is secret (108.4.d).
    """
    knowledge = deduce(state, player, player)
    population = knowledge.unseen_total()
    successes = sum(n for cid, n in knowledge.unseen.items() if predicate(cid))
    return hypergeometric_at_least_one(successes, population, draws)
