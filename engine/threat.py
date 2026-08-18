"""Can I take that battlefield, and can they take it back?

Riftbound is a closed system, so most of this is arithmetic rather than
guesswork. Two rules do the work:

* **465.2.c** -- in combat each side assigns damage equal to its *summed
  Might* among the other's units, and a unit must be assigned lethal damage
  in full before any damage goes to the next. So a side wipes the other
  exactly when its Might covers the sum of what the other side still has
  standing. No simulation is needed to answer "do I win this fight".
* **103.1.b.2** -- a deck's Domain Identity is its Champion Legend's, and
  every card in the deck must abide by it (103.1.b.3-4). The opponent's hand
  is secret, but it is not unknown: every card in it is legal in their
  Domain Identity, which bounds the worst case.

Everything on the board is public (107.1.d), so both sides' committed force
*and* their reinforcements are known exactly. Only the hand is hidden, and
that is the only place a bound is used instead of a fact.

This module reads state; it never mutates it, and it never looks at a hand
other than the observer's own.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.knowledge import MAIN_DECK_TYPES
from engine.zones import BASE_LOCATION


def _bf_location(index: int) -> str:
    return f"bf:{index}"


def domain_identity(state, player: int) -> frozenset[str]:
    """103.1.b.2 -- the deck's Domain Identity, from its Champion Legend."""
    legend = state.players[player].legend
    if legend is None:
        return frozenset()
    return frozenset(state.db[state.cards[legend].card_id].domains)


# Keyed by id() of the database: `CardDatabase` is immutable and shared -- it
# even returns itself from __deepcopy__, so every cloned state in a search
# points at the same object -- but it is not hashable by value, so this is a
# plain dict rather than lru_cache.
_POOL_CACHE: dict[tuple[int, frozenset], tuple] = {}


def _pool_for(db, identity: frozenset) -> tuple:
    key = (id(db), identity)
    pool = _POOL_CACHE.get(key)
    if pool is None:
        pool = tuple(
            card
            for card in db.cards.values()
            if card.type in MAIN_DECK_TYPES and set(card.domains) <= identity
        )
        _POOL_CACHE[key] = pool
    return pool


def domain_pool(state, player: int) -> tuple:
    """Every Main Deck card `player` could legally be holding.

    103.1.b.3 admits a single-domain card into the matching identity;
    103.1.b.4 admits a multi-domain card only into an identity containing
    *all* of its domains. Domainless cards are legal in any deck.

    This is the bound used when the opponent's decklist is unknown. It is
    deliberately generous -- it is an upper bound on their hand, not a guess
    at it.
    """
    # Memoized on the identity: the pool is a pure function of the card
    # database, and scanning 697 cards per battlefield per player made the
    # evaluation nine times slower when this fed search.
    return _pool_for(state.db, domain_identity(state, player))


def projected_might(state, ref, designation: str | None = None) -> int:
    """The Might `ref` would have under a designation it does not yet hold.

    Forecasting an attack needs the Might a unit *would* fight at: 807 gives
    an attacker +Assault, 814 gives a defender +Shield. Asking the state
    directly would mean mutating the board to ask a question, so this
    computes the difference instead.
    """
    might = state.might_of(ref)
    card = state.db[ref.card_id]

    granted_assault = sum(v for n, v, _ in ref.granted_keywords if n == "Assault")
    granted_shield = sum(v for n, v, _ in ref.granted_keywords if n == "Shield")
    assault = card.assault + granted_assault
    shield = card.shield + granted_shield

    # Undo whatever the unit currently carries, then apply what was asked.
    if ref.is_attacker:
        might -= assault
    if ref.is_defender:
        might -= shield
    if designation == "attacker":
        might += assault
    elif designation == "defender":
        might += shield
    return might


def _standing(state, location: str, player: int, designation: str) -> int:
    """How much Might a side must be dealt before it is wiped off `location`.

    465.2.c.4 -- damage already marked counts, so a unit that has taken
    damage is cheaper to finish off than a fresh one.
    """
    total = 0
    for ref in state.units_at(location, player):
        total += max(0, projected_might(state, ref, designation) - ref.damage)
    return total


def _mobile_might(state, player: int, designation: str) -> int:
    """Might sitting in `player`'s Base that could move in this turn.

    144.2 -- a Standard Move costs exhausting the unit, so an already
    exhausted unit cannot join. The Base is public (107.1.d), so this is
    exact for both players, not an estimate.
    """
    total = 0
    for instance_id in state.players[player].base:
        ref = state.cards[instance_id]
        if ref.location != BASE_LOCATION or ref.exhausted:
            continue
        if state.db[ref.card_id].type != "unit":
            continue
        total += projected_might(state, ref, designation)
    return total


def _mobile_bodies(state, player: int) -> int:
    """How many units could move in -- an empty battlefield needs a body, not
    Might: 348.2.a hands Control to whoever is the only player left there."""
    return sum(
        1
        for instance_id in state.players[player].base
        if (ref := state.cards[instance_id]).location == BASE_LOCATION
        and not ref.exhausted
        and state.db[ref.card_id].type == "unit"
    )


def _hidden_threat(state, observer: int, player: int) -> int:
    """The biggest single unit `player` could still play from a hidden hand.

    Bounded by their Domain Identity (103.1.b) and by what their current pool
    can actually pay for (163) -- they cannot deploy what they cannot afford.

    A unit played this way enters **exhausted** (143.4), so it cannot move to
    a battlefield the same turn. This number is therefore pressure on the
    *next* turn, which is why `can_lose` (this turn, exact) and
    `can_lose_next_turn` (includes this bound) are reported separately.

    For the observer's own seat the hand is visible, so their own number is
    read from the hand rather than bounded.
    """
    pool = state.players[player].pool
    if player == observer:
        candidates = [
            state.db[state.cards[i].card_id] for i in state.players[player].hand
        ]
    else:
        candidates = list(domain_pool(state, player))

    best = 0
    for card in candidates:
        if card.type != "unit":
            continue
        if not pool.can_pay(card.energy, list(card.power_domains)):
            continue
        best = max(best, card.might)
    return best


@dataclass(frozen=True)
class BattlefieldForecast:
    """What each side can do to one battlefield, right now."""

    battlefield: int
    name: str
    controller: int | None
    contested: bool
    # Might standing at the battlefield, per player, as it would fight.
    committed: tuple[int, int]
    # Might in each Base that could still move in this turn (public, exact).
    reinforcement: tuple[int, int]
    # Upper bound on the biggest unit each could still play from hand.
    hidden_threat: tuple[int, int]
    # Can the observer take this battlefield this turn?
    can_take: bool
    # Could the observer lose it this turn, to what is already on the board?
    can_lose: bool
    # ...or next turn, once what they could be holding is deployed?
    can_lose_next_turn: bool


def forecast(state, observer: int, index: int) -> BattlefieldForecast:
    """Assess battlefield `index` from `observer`'s seat."""
    bf = state.battlefields[index]
    location = _bf_location(index)
    them = state.opponent(observer)

    # As attacker I get Assault; as defender they get Shield, and vice versa.
    mine_here = _standing(state, location, observer, "defender")
    theirs_here = _standing(state, location, them, "defender")
    my_attack = _standing(state, location, observer, "attacker") + _mobile_might(
        state, observer, "attacker"
    )
    their_attack = _standing(state, location, them, "attacker") + _mobile_might(
        state, them, "attacker"
    )

    my_bodies = len(state.units_at(location, observer)) + _mobile_bodies(state, observer)
    their_bodies = len(state.units_at(location, them)) + _mobile_bodies(state, them)

    def takes(attack: int, bodies: int, defence: int, defenders: int) -> bool:
        if bodies == 0:
            return False
        if defenders == 0:
            return True                 # 348.2.a -- walk in and hold it
        return attack > defence         # equal wipes both (466.5.b), taking nothing

    can_take = bf.controller != observer and takes(
        my_attack, my_bodies, theirs_here, len(state.units_at(location, them))
    )
    can_lose = bf.controller == observer and takes(
        their_attack, their_bodies, mine_here, len(state.units_at(location, observer))
    )
    hidden = [0, 0]
    hidden[observer] = _hidden_threat(state, observer, observer)
    hidden[them] = _hidden_threat(state, observer, them)
    can_lose_next_turn = bf.controller == observer and takes(
        their_attack + hidden[them],
        their_bodies + (1 if hidden[them] else 0),
        mine_here,
        len(state.units_at(location, observer)),
    )

    committed = [0, 0]
    committed[observer] = mine_here
    committed[them] = theirs_here
    reinforcement = [0, 0]
    reinforcement[observer] = _mobile_might(state, observer, "attacker")
    reinforcement[them] = _mobile_might(state, them, "attacker")

    return BattlefieldForecast(
        battlefield=index,
        name=state.db[bf.card_id].name,
        controller=bf.controller,
        contested=bf.contested,
        committed=tuple(committed),
        reinforcement=tuple(reinforcement),
        hidden_threat=tuple(hidden),
        can_take=can_take,
        can_lose=can_lose,
        can_lose_next_turn=can_lose_next_turn,
    )


def forecast_all(state, observer: int) -> tuple[BattlefieldForecast, ...]:
    return tuple(
        forecast(state, observer, index) for index in range(len(state.battlefields))
    )


def takeable_count(state, player: int) -> int:
    """How many battlefields `player` could take right now.

    The cheap half of `forecast`: this reads only public information
    (107.1.d) and skips the hidden-hand bound entirely, because search calls
    it at every leaf. `forecast` is for a human reading the board; this is
    for the agent scoring one.
    """
    total = 0
    for index, bf in enumerate(state.battlefields):
        if bf.controller == player:
            continue
        location = _bf_location(index)
        them = state.opponent(player)
        defenders = state.units_at(location, them)
        attack = _standing(state, location, player, "attacker") + _mobile_might(
            state, player, "attacker"
        )
        bodies = len(state.units_at(location, player)) + _mobile_bodies(state, player)
        if bodies == 0:
            continue
        if not defenders or attack > _standing(state, location, them, "defender"):
            total += 1
    return total
