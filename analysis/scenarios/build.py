"""Construction helpers for the benchmark scenarios.

A scenario is a hand-built position, and a hand-built position can easily be
one the rules cannot reach. Two things keep that honest:

* Every scenario is checked with `engine.invariants.assert_ok` in the test
  suite, so a fabricated board that violates a structural rule fails loudly.
* Nothing here mints a card. Instances are drawn from the player's own Main
  Deck and *retyped* -- the `card_id` on an existing `CardRef` is changed --
  so the total number of cards a player owns never moves and the card
  conservation invariant (107) still means something. The resulting deck is
  not a legal decklist, which does not matter: a scenario is a position, not
  a game to be played out from a shuffle.
"""

from __future__ import annotations

from engine.actions import ChooseBattlefield, Mulligan, PassPhase
from engine.setup import build_state, load_deck
from engine.state import RiftboundState
from engine.zones import BASE_LOCATION

DEFAULT_DECKS = ("jinx_chaos_fury", "volibear_body_fury")


def opening(decks: tuple[str, str] = DEFAULT_DECKS, seed: int = 1) -> RiftboundState:
    """Setup run to the first Main Phase: battlefields chosen, no mulligan."""
    state = build_state(load_deck(decks[0]), load_deck(decks[1]), seed=seed)
    for _ in range(10):
        legal = state.legal_actions()
        choice = (
            [a for a in legal if isinstance(a, ChooseBattlefield)]
            or [a for a in legal if isinstance(a, Mulligan) and not a.instance_ids]
        )
        if not choice:
            break
        state.apply(choice[0])
    return state


def to_main(state: RiftboundState, player: int, limit: int = 6) -> RiftboundState:
    """Pass turns until `player` is on their own Main Phase."""
    for _ in range(limit):
        if state.current_player == player and str(state.phase).endswith("MAIN"):
            return state
        legal = state.legal_actions()
        passes = [a for a in legal if isinstance(a, PassPhase)]
        state.apply(passes[0] if passes else legal[0])
    return state


def clear_hand(state: RiftboundState, player: int) -> None:
    """Send the opening hand to the bottom of the deck.

    A scenario is about one decision, and seven unrelated cards in hand add
    seven unrelated legal actions to it. The cards go to the bottom rather
    than to the trash so nothing is created or destroyed.
    """
    player_state = state.players[player]
    player_state.main_deck.extend(player_state.hand)
    player_state.hand.clear()


def _take(state: RiftboundState, player: int, card_id: str) -> int:
    """Pull an instance off the player's deck and retype it. See module note."""
    deck = state.players[player].main_deck
    if not deck:
        raise RuntimeError(f"P{player} has no cards left to build a scenario from")
    instance_id = deck.pop(0)
    state.cards[instance_id].card_id = card_id
    return instance_id


def to_hand(state: RiftboundState, player: int, card_id: str) -> int:
    instance_id = _take(state, player, card_id)
    state.players[player].hand.append(instance_id)
    return instance_id


def place(
    state: RiftboundState,
    player: int,
    card_id: str,
    location: str = BASE_LOCATION,
    exhausted: bool = False,
    damage: int = 0,
) -> int:
    """Put a permanent on the board for `player`.

    A board permanent lives in its controller's `base` list whatever its
    location -- the list is the zone (107.1.c) and `location` is where on the
    map it stands, which is the distinction the invariant checker enforces.
    """
    instance_id = _take(state, player, card_id)
    ref = state.cards[instance_id]
    ref.location = location
    ref.exhausted = exhausted
    ref.damage = damage
    state.players[player].base.append(instance_id)
    return instance_id


def channel(state: RiftboundState, player: int, card_id: str,
            exhausted: bool = False) -> int:
    """Put a rune on the board, ready by default (430 Channel)."""
    instance_id = _take(state, player, card_id)
    ref = state.cards[instance_id]
    ref.location = BASE_LOCATION
    ref.exhausted = exhausted
    state.players[player].base.append(instance_id)
    state.players[player].channeled_runes.append(instance_id)
    return instance_id


def deploy_champion(state: RiftboundState, player: int,
                    exhausted: bool = False) -> int | None:
    """Put the champion on the board instead of leaving it in the Champion Zone.

    A champion sitting in the Champion Zone (108.3) is playable from there,
    which quietly adds a strong and perfectly reasonable option to every
    scenario. Three scenarios were failing agents for taking it. Deploying the
    champion first removes the option instead of pretending it is a mistake.
    """
    zone = state.players[player].champion_zone
    if not zone:
        return None
    instance_id = zone.pop(0)
    state.cards[instance_id].location = BASE_LOCATION
    state.cards[instance_id].exhausted = exhausted
    state.players[player].base.append(instance_id)
    return instance_id


def exhaust_runes(state: RiftboundState, player: int) -> None:
    """Turn every channeled rune sideways, so tapping is not an option."""
    for instance_id in state.players[player].channeled_runes:
        state.cards[instance_id].exhausted = True


def give(state: RiftboundState, player: int, energy: int = 0,
         power: dict[str, int] | None = None) -> None:
    """Fill a rune pool directly (165). Scenarios test decisions, not ramp."""
    pool = state.players[player].pool
    pool.energy = energy
    pool.power = dict(power or {})


def points(state: RiftboundState, mine: int, theirs: int, player: int = 0) -> None:
    state.players[player].points = mine
    state.players[1 - player].points = theirs


def control(state: RiftboundState, index: int, controller: int | None,
            contested_by: int | None = None) -> None:
    """Set battlefield control (190) directly."""
    battlefield = state.battlefields[index]
    battlefield.controller = controller
    battlefield.contested = contested_by is not None
    battlefield.contested_by = contested_by
