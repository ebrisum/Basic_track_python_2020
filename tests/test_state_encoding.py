"""`TCG_AI_BUILD.md` section 16 -- the game as a sequence of object tokens.

The test that matters most here is
`test_the_tokens_separate_what_the_13_features_cannot`. Building the style
agents measured that 77% of decisions are exact ties at the top of the
evaluator and that on 100% of sampled ties every tied action produces an
*identical* 13-feature vector. That is the case for section 16, and it is only
a case if the replacement actually does better. This asserts it does.

The rest are the properties an encoder has to have before anything is trained
on it: it sees only the observation, it is stable, it masks rather than pads
with plausible zeros, and it never invents a card.
"""

from __future__ import annotations

import copy
import random

import pytest

from analysis.evaluation import FEATURE_NAMES, evaluate, features, load_model
from analysis.validate import build_state, load_db, load_deck
from learning.state_encoding import (
    FLAGS,
    KIND_INDEX,
    SCALARS,
    EncodingOverflow,
    StateEncoder,
)


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


@pytest.fixture(scope="module")
def db():
    return load_db()


@pytest.fixture(scope="module")
def encoder(db):
    return StateEncoder(db)


def _state(db, seed: int = 1):
    return build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)


def _fingerprint(encoder, state, player: int):
    """Every column of the encoding, flattened into something hashable."""
    columns = encoder.encode(state.observation(player)).columns()
    return tuple(
        tuple(tuple(row) if isinstance(row, list) else row for row in column)
        for column in columns.values()
    )


def _advance(state, steps: int, seed: int = 0):
    rng = random.Random(seed)
    for _ in range(steps):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        state.apply(rng.choice(legal))
    return state


def test_the_tokens_separate_what_the_13_features_cannot(db, encoder):
    """The whole argument for section 16, as an assertion.

    Find a plateau -- a set of actions the evaluator scores exactly equally,
    which it does on 77% of decisions -- confirm the 13 features really are
    identical across it, and then check the token encoding is not.
    """
    model = load_model()
    state = _advance(_state(db), 40)
    checked = 0

    for _ in range(150):
        if state.is_terminal():
            break
        legal = state.legal_actions()
        if not legal:
            break
        if len(legal) > 1:
            me = state.current_player
            best, group = None, []
            for action in legal:
                clone = copy.deepcopy(state)
                try:
                    clone.apply(action)
                except Exception:
                    continue
                score = evaluate(clone, me, model)
                if best is None or score > best:
                    best, group = score, [clone]
                elif score == best:
                    group.append(clone)

            if len(group) > 1:
                vectors = {
                    tuple(features(c, me)[n] for n in FEATURE_NAMES)
                    if not c.is_terminal() else None
                    for c in group
                }
                if len(vectors) == 1:          # the features cannot tell them apart
                    # Every column, not a chosen few. A first version of this
                    # test compared `card` and `scalars` only and failed --
                    # the plateau was four ways to tap a rune plus one
                    # activated ability, which differ in the `exhausted` bit
                    # and nowhere else. Dropping `flags` from the key dropped
                    # exactly the column carrying the difference.
                    encodings = {
                        _fingerprint(encoder, c, me) for c in group
                    }
                    assert len(encodings) > 1, (
                        "the tokens agree with the 13 features that these "
                        "positions are identical -- section 16 buys nothing here"
                    )
                    checked += 1
                    if checked >= 3:
                        return
        state.apply(random.Random(checked).choice(legal))

    assert checked, "no feature-identical plateau was reached; test measured nothing"


def test_the_encoder_reads_an_observation_and_nothing_else(db, encoder):
    """Handed a state rather than an observation, it must fail, not cope."""
    state = _state(db)
    with pytest.raises(AttributeError):
        encoder.encode(state)


def test_absent_objects_are_masked_not_padded_with_plausible_zeros(db, encoder):
    state = _advance(_state(db), 30)
    encoded = encoder.encode(state.observation(0))
    assert len(encoded.tokens) == encoder.max_tokens
    assert len(encoded.mask) == encoder.max_tokens
    assert encoded.length == sum(encoded.mask)
    # Every masked-out row is the pad kind, so a bug that reads past the mask
    # produces something obviously wrong rather than a plausible unit.
    for token, real in zip(encoded.tokens, encoded.mask):
        if not real:
            assert token.kind == KIND_INDEX["pad"]
            assert token.card == 0


def test_encoding_is_stable(db, encoder):
    state = _advance(_state(db), 25)
    obs = state.observation(0)
    assert encoder.encode(obs).columns() == encoder.encode(obs).columns()


def test_every_perspective_is_the_actors_own(db, encoder):
    """`mine` is relative to the viewer, never to seat 0."""
    state = _advance(_state(db), 60)
    mine_flag = FLAGS.index("mine")
    first = encoder.encode(state.observation(0))
    second = encoder.encode(state.observation(1))
    ours = [t.flags[mine_flag] for t, m in zip(first.tokens, first.mask) if m]
    theirs = [t.flags[mine_flag] for t, m in zip(second.tokens, second.mask) if m]
    assert ours != theirs, "both seats produced the same ownership bits"


def test_the_encoder_cannot_see_the_opponents_hand(db, encoder):
    """It is handed an observation, so this is structural -- and pinned anyway."""
    state = _advance(_state(db), 40)
    encoded = encoder.encode(state.observation(0))
    hand_kind = KIND_INDEX["hand"]
    hand_tokens = [t for t, m in zip(encoded.tokens, encoded.mask)
                   if m and t.kind == hand_kind]
    obs = state.observation(0)
    assert len(hand_tokens) == len(obs.hand) + len(obs.revealed_opponent_hand)


def test_prevent_all_does_not_encode_as_zero(db, encoder):
    """437.1.b.1.b -- "prevent all" and "prevent nothing" are opposites."""
    from analysis.scenarios.build import clear_hand, opening, place, to_main

    state = to_main(opening(), 0)
    clear_hand(state, 0)
    instance_id = place(state, 0, "OGN-175")
    index = SCALARS.index("prevent")

    def prevent_column():
        encoded = encoder.encode(state.observation(0))
        return [t.scalars[index] for t, m in zip(encoded.tokens, encoded.mask)
                if m and t.card == encoder.card_index["OGN-175"]]

    assert prevent_column() == [0.0]
    state.cards[instance_id].prevent = None
    assert prevent_column() == [-1.0]


def test_buff_counters_reach_the_tokens(db, encoder):
    from analysis.scenarios.build import clear_hand, opening, place, to_main

    state = to_main(opening(), 0)
    clear_hand(state, 0)
    instance_id = place(state, 0, "OGN-175")
    index = SCALARS.index("buffs")
    state.buff(state.cards[instance_id])
    encoded = encoder.encode(state.observation(0))
    buffed = [t.scalars[index] for t, m in zip(encoded.tokens, encoded.mask)
              if m and t.card == encoder.card_index["OGN-175"]]
    assert buffed == [1.0]


def test_an_unknown_card_is_refused(db, encoder):
    with pytest.raises(KeyError):
        encoder._card("NOT-A-CARD")


def test_overflow_raises_rather_than_truncating(db):
    small = StateEncoder(db, max_tokens=3)
    state = _advance(_state(db), 40)
    with pytest.raises(EncodingOverflow):
        small.encode(state.observation(0))


def test_the_schema_version_moves_when_a_column_changes(db):
    """A checkpoint trained on one layout cannot be loaded against another.

    Derived rather than hand-maintained, for the same reason
    `engine/versions.py` derives its digests: a number someone must remember
    to bump is a number that will not be bumped.
    """
    import learning.state_encoding as module

    encoder = StateEncoder(db)
    before = encoder.schema_version()
    original = module.SCALARS
    try:
        module.SCALARS = original + ("a_new_column",)
        assert StateEncoder(db).schema_version() != before
    finally:
        module.SCALARS = original
    assert StateEncoder(db).schema_version() == before


def test_a_bigger_position_still_fits(db, encoder):
    """192 slots against the largest position 150 fuzzed games produced."""
    largest = 0
    for seed in range(6):
        state = _state(db, seed)
        rng = random.Random(seed)
        for _ in range(400):
            if state.is_terminal():
                break
            legal = state.legal_actions()
            if not legal:
                break
            largest = max(largest,
                          encoder.encode(state.observation(state.current_player)).length)
            state.apply(rng.choice(legal))
    assert largest > 20, "the sample never built a real board"
    assert largest < encoder.max_tokens, f"{largest} tokens against a 192 cap"
