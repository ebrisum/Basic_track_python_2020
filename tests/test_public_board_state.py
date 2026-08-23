"""Buff counters and the Prevent value are public, and now they are visible.

Everything on the board is Public (107.1.d, 107.2.c). Two pieces of it never
reached the observation:

* **Buff counters** (426, 702.3) are physical objects sitting on a card.
  `current_might` folded their effect into a number, which looks like enough
  until you need the *count* — 702.2.b lets a Buff be spent, and a player
  reading the board can see whether there is one left to spend.
* **The Prevent value** (437) was not exposed at all. It is the difference
  between a unit that survives the next 3 damage and one that dies to it.

This is the same shape of gap as `choice_options`: not a leak, a deficit. The
no-cheating suite pins one direction and cannot see the other.
"""

from __future__ import annotations

def _put_a_unit_on_the_board(player: int = 0):
    """Borrow the scenario builders rather than reinventing them."""
    from analysis.scenarios.build import clear_hand, opening, place, to_main
    state = to_main(opening(), player)
    clear_hand(state, player)
    return state, place(state, player, "OGN-175")


def test_a_buff_counter_is_visible_to_both_players():
    state, instance_id = _put_a_unit_on_the_board()
    ref = state.cards[instance_id]
    before = state.observation(0).board
    assert [c.buffs for c in before if c.instance_id == instance_id] == [0]

    state.buff(ref)                      # 426
    for viewer in (0, 1):
        seen = [c for c in state.observation(viewer).board
                if c.instance_id == instance_id]
        assert seen and seen[0].buffs == 1, f"P{viewer} cannot see the Buff"


def test_current_might_alone_could_not_have_carried_it():
    """The reason folding the effect into Might was not enough.

    A unit with one Buff and a unit given +1 Might by an effect have the same
    `current_might` and different futures: only one of them can spend
    something (702.2.b).
    """
    state, instance_id = _put_a_unit_on_the_board()
    ref = state.cards[instance_id]
    state.buff(ref)
    buffed = [c for c in state.observation(0).board
              if c.instance_id == instance_id][0]

    other, other_id = _put_a_unit_on_the_board()
    other_ref = other.cards[other_id]
    other_ref.might_this_turn += 1
    modified = [c for c in other.observation(0).board
                if c.instance_id == other_id][0]

    assert buffed.current_might == modified.current_might
    assert buffed.buffs != modified.buffs


def test_a_prevent_value_is_visible():
    """437 — whether the next damage lands is not a secret."""
    state, instance_id = _put_a_unit_on_the_board()
    ref = state.cards[instance_id]
    ref.prevent = 3
    for viewer in (0, 1):
        seen = [c for c in state.observation(viewer).board
                if c.instance_id == instance_id]
        assert seen and seen[0].prevent == 3, f"P{viewer} cannot see Prevent"


def test_prevent_all_is_distinguishable_from_no_prevent():
    """437.1.b.1.b — "prevent all" is None, and None is not 0."""
    state, instance_id = _put_a_unit_on_the_board()
    ref = state.cards[instance_id]
    assert [c.prevent for c in state.observation(0).board
            if c.instance_id == instance_id] == [0]
    ref.prevent = None
    assert [c.prevent for c in state.observation(0).board
            if c.instance_id == instance_id] == [None]


def test_these_fields_are_in_the_replay_hash():
    """Unlike `choice_options`, these are public, so they belong in the hash.

    A field only one player may read cannot go into a hash both players'
    observations feed. A field both players read must, or the hash stops
    covering the board.
    """
    state, instance_id = _put_a_unit_on_the_board()
    before = state.observation(0).to_canonical_bytes()
    state.buff(state.cards[instance_id])
    assert state.observation(0).to_canonical_bytes() != before
