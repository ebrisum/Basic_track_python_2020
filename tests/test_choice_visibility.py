"""A player must be able to see what they are being asked to choose between.

431.1.c.1's reminder says cards being looked at stay in their zone of origin --
the Main Deck, which is Secret (108.4.d). But the *looking* player is looking
at them: that is the whole instruction. 128.2.a makes Privacy follow the zone
only "unless specified otherwise by the state of the card", and "being looked
at by this player" is such a state.

The engine raised those choices as `ChooseTarget(instance_id=...)` actions
while the observation exposed nothing about the cards, so an agent restricted
to the frozen interface -- which is every agent, by design -- chose blind. The
opposite direction is covered by `test_no_cheating`; this file covers the
deficit, and pins that closing it did not open a leak.
"""

from __future__ import annotations

import random

import pytest

from analysis.validate import build_state, load_db, load_deck
from engine.actions import ChooseTarget, Mulligan


DECKS = ("jinx_chaos_fury", "volibear_body_fury")


def _observable_ids(obs) -> set[int]:
    """Every instance the observation lets this player identify."""
    ids = {c.instance_id for c in obs.hand}
    ids |= {c.instance_id for c in obs.board}
    for trash in obs.trash:
        ids |= {c.instance_id for c in trash}
    for card in obs.champion_zone + obs.legend:
        if card is not None:
            ids.add(card.instance_id)
    ids |= {c.instance_id for c in obs.revealed_opponent_hand}
    for battlefield in obs.battlefields:
        if battlefield.facedown_card is not None:
            ids.add(battlefield.facedown_card.instance_id)
    ids |= {c.instance_id for c in obs.choice_options}
    return ids


def _play(seed: int, steps: int = 3000):
    db = load_db()
    state = build_state(load_deck(DECKS[0]), load_deck(DECKS[1]), seed=seed, db=db)
    rng = random.Random(seed)
    for _ in range(steps):
        if state.is_terminal():
            return
        legal = state.legal_actions()
        if not legal:
            return
        yield state, legal
        state.apply(rng.choice(legal))


@pytest.mark.parametrize("seed", range(8))
def test_every_legal_action_names_something_the_player_can_see(seed):
    """No action may reference an instance the acting player cannot identify."""
    for state, legal in _play(seed):
        obs = state.observation(state.current_player)
        seen = _observable_ids(obs)
        for action in legal:
            referenced = set()
            instance_id = getattr(action, "instance_id", None)
            if instance_id is not None:
                referenced.add(instance_id)
            if isinstance(action, Mulligan):
                referenced |= set(action.instance_ids)
            unknown = referenced - seen
            assert not unknown, (
                f"{action!r} references {sorted(unknown)}, which P"
                f"{state.current_player} cannot see in their observation"
            )


def test_choice_options_carry_the_cards_being_chosen_between():
    """The options are named, not just counted -- a card_id and a name."""
    for state, legal in _play(3):
        if not any(isinstance(a, ChooseTarget) for a in legal):
            continue
        obs = state.observation(state.current_player)
        offered = {a.instance_id for a in legal if isinstance(a, ChooseTarget)}
        assert obs.choice_options, "a pending choice exposed no options"
        described = {c.instance_id: c for c in obs.choice_options}
        assert offered <= set(described), "an offered choice was not described"
        for instance_id in offered:
            card = described[instance_id]
            assert card.card_id and card.name
        return
    pytest.fail("no ChooseTarget arose in seed 3 -- the test is measuring nothing")


def test_the_opponent_is_told_a_choice_is_pending_but_not_what_it_is():
    """128.4 -- Private means *only* the choosing player reads those faces."""
    for state, legal in _play(3):
        if not any(isinstance(a, ChooseTarget) for a in legal):
            continue
        chooser = state.current_player
        opponent = 1 - chooser
        theirs = state.observation(chooser)
        if not theirs.choice_options:
            continue
        watching = state.observation(opponent)
        assert watching.choice_options == ()
        # And the cards themselves must not have leaked in by another door.
        hidden = {c.instance_id for c in theirs.choice_options}
        assert not (hidden & _observable_ids(watching)) or all(
            # anything the opponent can also see was already public
            instance_id in {c.instance_id for c in watching.board}
            or any(instance_id in {c.instance_id for c in t}
                   for t in watching.trash)
            for instance_id in hidden & _observable_ids(watching)
        )
        return
    pytest.fail("no ChooseTarget arose in seed 3 -- the test is measuring nothing")


def test_choice_options_do_not_enter_the_replay_hash():
    """Both players' observations back the hash, so a private field in it

    would make the hash depend on information neither player fully has. The
    prompt is already excluded for the same reason.
    """
    for state, legal in _play(3):
        obs = state.observation(state.current_player)
        if not obs.choice_options:
            continue
        assert b"choice_options" not in obs.to_canonical_bytes()
        return
    pytest.fail("no pending choice arose in seed 3")
