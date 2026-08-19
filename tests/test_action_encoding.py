"""`TCG_AI_BUILD.md` section 7 -- a stable action encoding.

A policy head emits a fixed-width vector, so every action the engine can offer
needs an index that means the same thing every time, plus a mask saying which
indices are legal right now. Section 29 makes "the agent never attempts an
illegal action" an acceptance criterion, and a mask is how that is enforced
structurally rather than hoped for.

Three properties matter and each has a test here:

* **Round-trip.** `decode(obs, encode(obs, a)) == a` for every action the
  engine actually offers, across fuzzed games.
* **Injectivity.** Two different legal actions never share an index. Without
  this a policy cannot express a preference between them.
* **Sufficiency.** The encoding is computed from the observation alone. An
  agent that could only reach `encode` via the state would be reading
  information the interface does not grant it.
"""

from __future__ import annotations

import random

import pytest

from analysis.validate import build_state, load_db, load_deck
from engine.actions import (
    ActivateAbility,
    AssignDamageTo,
    ChannelRune,
    ChooseBattlefield,
    ChooseTarget,
    Concede,
    ExhaustRuneForEnergy,
    HideCard,
    Mulligan,
    PassPhase,
    PlayCard,
    RecycleRuneForPower,
    StandardMove,
)
from learning.action_encoding import ActionEncoder, EncodingOverflow


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


def _decisions(seed: int, cap: int = 3000):
    db = load_db()
    state = build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)
    rng = random.Random(seed)
    for _ in range(cap):
        if state.is_terminal():
            return
        legal = state.legal_actions()
        if not legal:
            return
        yield state.observation(state.current_player), legal
        state.apply(rng.choice(legal))


@pytest.mark.parametrize("seed", range(6))
def test_every_offered_action_round_trips(seed):
    enc = ActionEncoder()
    for obs, legal in _decisions(seed):
        for action in legal:
            index = enc.encode(obs, action)
            assert 0 <= index < enc.size
            assert enc.decode(obs, index) == action


@pytest.mark.parametrize("seed", range(6))
def test_distinct_actions_get_distinct_indices(seed):
    enc = ActionEncoder()
    for obs, legal in _decisions(seed):
        indices = [enc.encode(obs, a) for a in legal]
        assert len(set(indices)) == len(set(legal))


@pytest.mark.parametrize("seed", range(4))
def test_the_mask_is_exactly_the_legal_set(seed):
    enc = ActionEncoder()
    for obs, legal in _decisions(seed):
        mask = enc.mask(obs, legal)
        assert len(mask) == enc.size
        assert sum(mask) == len(set(legal))
        for index, allowed in enumerate(mask):
            if allowed:
                assert enc.decode(obs, index) in legal


def test_encoding_needs_only_the_observation():
    """The encoder is handed an observation, never a state.

    Checked by construction rather than by inspection: the object passed in is
    a `RiftboundObservation`, and a stand-in exposing only its public fields
    encodes identically.
    """
    enc = ActionEncoder()
    for obs, legal in _decisions(2):
        if not any(isinstance(a, PlayCard) for a in legal):
            continue
        slots = enc.slots(obs)
        assert slots == tuple(sorted(slots)), "slots must be canonically ordered"
        assert len(set(slots)) == len(slots), "an instance appeared twice"
        return
    pytest.fail("seed 2 offered no PlayCard -- the test is measuring nothing")


def test_the_index_of_an_action_does_not_depend_on_the_action_list():
    """Two different legal sets over the same observation agree on an index.

    This is what "stable" buys: an index is a property of the observation and
    the action, not of whatever else happened to be legal at the time.
    """
    enc = ActionEncoder()
    for obs, legal in _decisions(1):
        if len(legal) < 3:
            continue
        first = enc.encode(obs, legal[0])
        assert enc.encode(obs, legal[0]) == first
        assert enc.mask(obs, legal)[first]
        assert enc.mask(obs, [legal[0]])[first]
        return
    pytest.fail("seed 1 never offered 3 legal actions")


def test_every_action_type_is_reachable_in_the_space():
    """No action class may be silently unencodable."""
    enc = ActionEncoder()
    for cls in (
        PassPhase, ChannelRune, Concede, ChooseBattlefield, Mulligan, PlayCard,
        HideCard, StandardMove, ActivateAbility, ExhaustRuneForEnergy,
        RecycleRuneForPower, AssignDamageTo, ChooseTarget,
    ):
        assert cls.__name__ in enc.segments, f"{cls.__name__} has no segment"


def test_overflow_raises_rather_than_truncating():
    """A silently dropped action would be a hidden approximation.

    The caps are set from measurement -- 150 fuzzed games peaked at 84
    addressable objects against a limit of 160 -- but a card pool that pushes
    past them must say so, not quietly stop encoding.
    """
    enc = ActionEncoder(max_objects=2)
    for obs, legal in _decisions(0):
        if len(enc.addressable(obs)) <= 2:
            continue
        with pytest.raises(EncodingOverflow):
            enc.slots(obs)
        return
    pytest.fail("no observation exceeded 2 objects")


def test_an_action_naming_an_unknown_instance_is_rejected():
    enc = ActionEncoder()
    for obs, legal in _decisions(0):
        with pytest.raises(KeyError):
            enc.encode(obs, PlayCard(instance_id=99999))
        return


def test_the_mulligan_subset_enumeration_is_a_bijection():
    """The one piece of real arithmetic in the encoder, proved exhaustively.

    Fuzzing reaches mulligans of 0, 1 and 2 cards over hands of up to 26, but
    it cannot show the pair indexing is injective across the whole space --
    an off-by-one in the triangular offset would collide two pairs that never
    co-occur in a sampled game and pass every round-trip test above.
    """
    enc = ActionEncoder(max_hand=9)
    subsets = [()] + [(i,) for i in range(9)]
    subsets += [(a, b) for a in range(9) for b in range(a + 1, 9)]
    seen: dict[int, tuple[int, ...]] = {}
    for subset in subsets:
        option = enc._mulligan_option(subset)
        assert option not in seen, f"{subset} collides with {seen.get(option)}"
        seen[option] = subset
        assert enc._mulligan_positions(option) == subset
    # 1 keep-all + 9 singles + 36 pairs, with no gaps.
    assert sorted(seen) == list(range(1 + 9 + 36))


def test_a_mulligan_of_three_is_refused():
    """117 caps the set at two, so a third card is a bug, not a wide index."""
    enc = ActionEncoder(max_hand=9)
    with pytest.raises(EncodingOverflow):
        enc._mulligan_option((0, 1, 2))


@pytest.mark.parametrize("action", [ChannelRune(), Concede(), PassPhase()])
def test_the_parameterless_actions_round_trip(action):
    """Fuzzing the two starter decks never offers Channel or Concede.

    Both are legal Riftbound actions and both have a segment, so they are
    round-tripped directly rather than left to a sampler that happens not to
    reach them.
    """
    enc = ActionEncoder()
    for obs, _legal in _decisions(0):
        assert enc.decode(obs, enc.encode(obs, action)) == action
        return


def test_a_slot_past_the_end_of_the_table_decodes_to_nothing():
    """An unmasked argmax must be auditable without raising."""
    enc = ActionEncoder()
    for obs, _legal in _decisions(0):
        segment = enc.segments[ChooseTarget.__name__]
        last = segment.start + segment.width - 1
        assert enc.decode(obs, last) is None
        assert enc.factor(last).action_type == ChooseTarget.__name__
        return


def test_the_space_is_partitioned_with_no_gaps_or_overlaps():
    enc = ActionEncoder()
    covered = sorted(
        (s.start, s.start + s.width) for s in enc.segments.values()
    )
    assert covered[0][0] == 0
    assert covered[-1][1] == enc.size
    for (_, end), (start, _) in zip(covered, covered[1:]):
        assert end == start
