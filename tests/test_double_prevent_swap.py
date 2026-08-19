"""The last three Game Actions any card needs: Double (432), Swap (433) and
Prevent (437).

The Game Actions audit (410-444) found these three unimplemented while every
other action some card needs was working. Seven cards in the pool use them:
four Double, two Prevent, one Swap.

None is scripted yet, so these tests drive the primitives directly and through
synthetic scripts -- the same approach as the Hidden targeting tests, and for
the same reason: a Game Action tested only by the cards that happen to be
scripted today is tested by accident.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from cards.database import load as load_db
from cards.dsl import Duration, Selector
from cards.primitives import EffectContext, execute
from engine.setup import build_state, load_deck
from engine.state import Phase, bf_location
from engine.zones import BASE_LOCATION, CardRef

DB = load_db()
BIG = "OGN-142"        # Mountain Drake, 10 Might
SMALL = "OGN-197"      # Teemo - Scout, 1 Might


@pytest.fixture(scope="module")
def decks():
    return load_deck("jinx_chaos_fury"), load_deck("volibear_body_fury")


def arena(decks, seed=1):
    state = build_state(decks[0], decks[1], seed=seed, db=DB)
    agent = RandomAgent(seed)
    while state.phase is not Phase.MAIN and not state.is_terminal():
        state.apply(agent.act(state))
    return state


def put_unit(state, player, card_id=BIG, location=BASE_LOCATION):
    instance_id = max(state.cards) + 1
    state.cards[instance_id] = CardRef(
        instance_id=instance_id, card_id=card_id, owner=player,
        controller=player, location=location,
    )
    state.players[player].base.append(instance_id)
    return instance_id


# --- 432 Double -------------------------------------------------------------


def test_double_adds_the_units_current_might(decks):
    """432.1 -- "increasing a numeric attribute by an amount equal to that
    attribute's **current** value"."""
    from cards.dsl import Double

    state = arena(decks)
    unit = put_unit(state, 0)
    before = state.might_of(state.cards[unit])

    execute(state, Double(selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))

    assert state.might_of(state.cards[unit]) == before * 2


def test_double_reads_current_might_including_shield(decks):
    """432.1.a's worked example: a 3-Might unit with Shield 2, defending, has
    current Might 5, so Double gives +5 -- and the +5 stays after combat ends
    and Shield stops applying, leaving 8, not 6."""
    from cards.dsl import Double

    state = arena(decks)
    unit = put_unit(state, 0, SMALL, bf_location(0))
    ref = state.cards[unit]
    printed = DB[SMALL].might
    ref.granted_keywords = (("Shield", 2, Duration.THIS_TURN.value),)
    ref.is_defender = True
    assert state.might_of(ref) == printed + 2

    execute(state, Double(selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    assert state.might_of(ref) == (printed + 2) * 2

    ref.is_defender = False           # combat ends; Shield stops applying
    assert state.might_of(ref) == printed + (printed + 2)


def test_double_is_a_this_turn_modifier_by_default(decks):
    """432.1.a -- "for the duration specified by the Game Effect"."""
    from cards.dsl import Double

    state = arena(decks)
    unit = put_unit(state, 0)
    printed = DB[BIG].might

    execute(state, Double(selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    state._phase_ending()

    assert state.might_of(state.cards[unit]) == printed


# --- 433 Swap ---------------------------------------------------------------


def test_swap_reverses_two_units_might(decks):
    """433.1 -- "increasing one numeric value and decreasing another ... such
    that their values are reversed"."""
    from cards.dsl import Swap

    state = arena(decks)
    big = put_unit(state, 0, BIG)
    small = put_unit(state, 0, SMALL)
    big_might = state.might_of(state.cards[big])
    small_might = state.might_of(state.cards[small])
    assert big_might != small_might

    execute(state, Swap(selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(big, small)))

    assert state.might_of(state.cards[big]) == small_might
    assert state.might_of(state.cards[small]) == big_might


def test_swapping_equal_values_does_nothing(decks):
    """433.1.c -- "If both attributes are the same numeric value, Swapping has
    no effect"."""
    from cards.dsl import Swap

    state = arena(decks)
    one = put_unit(state, 0, BIG)
    two = put_unit(state, 0, BIG)
    before = state.might_of(state.cards[one])

    execute(state, Swap(selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(one, two)))

    assert state.might_of(state.cards[one]) == before
    assert state.might_of(state.cards[two]) == before
    assert state.cards[one].might_this_turn == 0, "a no-op left a modifier behind"


# --- 437 Prevent ------------------------------------------------------------


def test_prevent_reduces_the_next_damage(decks):
    """437.2 -- damage is "replaced with an event where it deals that much
    damage reduced by the Prevent Value"."""
    from cards.dsl import Deal, Prevent

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))

    execute(state, Prevent(amount=3, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    execute(state, Deal(amount=5, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=1, chosen=(unit,)))

    assert state.cards[unit].damage == 2


def test_prevent_is_spent_as_it_is_used(decks):
    """437.3 -- "reduce the Prevent Value being tracked on the Unit ... by the
    prevented amount", and 437.3.a expires it at 0."""
    from cards.dsl import Deal, Prevent

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))

    execute(state, Prevent(amount=3, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    execute(state, Deal(amount=1, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=1, chosen=(unit,)))
    assert state.cards[unit].damage == 0
    assert state.cards[unit].prevent == 2

    execute(state, Deal(amount=5, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=1, chosen=(unit,)))
    assert state.cards[unit].damage == 3
    assert state.cards[unit].prevent == 0


def test_damage_fully_prevented_was_never_dealt(decks):
    """437.4 -- "Damage dealt to a Unit that has all of that damage Prevented
    is not considered to have been dealt to it at all"."""
    from cards.dsl import Deal, Prevent

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))

    execute(state, Prevent(amount=4, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    execute(state, Deal(amount=4, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=1, chosen=(unit,)))

    assert state.cards[unit].damage == 0


def test_prevent_all_never_lets_damage_through(decks):
    """437.1.b.1.b -- X can be "All", an infinite amount; 437.3.c keeps it
    "All" however much it absorbs."""
    from cards.dsl import Deal, Prevent

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))

    execute(state, Prevent(amount=None, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    for _ in range(3):
        execute(state, Deal(amount=99, selector=Selector(scope="choose", type="unit")),
                EffectContext(controller=1, chosen=(unit,)))

    assert state.cards[unit].damage == 0
    assert state.cards[unit].prevent is None, "'All' must stay 'All' (437.3.c)"


def test_lethal_damage_accounts_for_prevent(decks):
    """437.5.a's worked example -- a 2-Might unit with prevent 3 needs 5
    damage assigned before that assignment is lethal."""
    from cards.dsl import Prevent

    state = arena(decks)
    unit = put_unit(state, 0, SMALL, bf_location(0))
    ref = state.cards[unit]
    might = state.might_of(ref)

    execute(state, Prevent(amount=3, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))

    ref.damage = might              # lethal without Prevent
    assert not state._has_lethal(ref)
    ref.damage = might + 3
    assert state._has_lethal(ref)


def test_prevent_all_is_never_lethal(decks):
    """437.5.b -- "No amount of damage is ever considered lethal if the
    Prevent Value is 'All'"."""
    from cards.dsl import Prevent

    state = arena(decks)
    unit = put_unit(state, 0, SMALL, bf_location(0))

    execute(state, Prevent(amount=None, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    state.cards[unit].damage = 999

    assert not state._has_lethal(state.cards[unit])


def test_an_unspent_prevent_expires_at_end_of_turn(decks):
    """437.1.b.1 formats Prevent as "...that would be dealt to a [unit] **this
    turn**", so an unspent Prevent Value expires at step 3d with every other
    this-turn effect (317.2.c)."""
    from cards.dsl import Prevent

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))

    execute(state, Prevent(amount=3, selector=Selector(scope="choose", type="unit")),
            EffectContext(controller=0, chosen=(unit,)))
    assert state.cards[unit].prevent == 3

    state._phase_ending()
    assert state.cards[unit].prevent == 0


def test_an_invariant_catches_prevent_on_a_card_off_the_board(decks):
    """A Prevent Value is tracked on a unit; a card in no zone cannot hold
    one, and the structural checker should say so."""
    from engine.invariants import check

    state = arena(decks)
    unit = put_unit(state, 0, BIG, bf_location(0))
    state.cards[unit].prevent = 2
    assert not any("prevent" in p.lower() for p in check(state))

    state.leave_board(state.cards[unit])
    state.cards[unit].prevent = 2
    assert any("prevent" in p.lower() for p in check(state))
