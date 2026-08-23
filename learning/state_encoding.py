"""State encoding into object tokens (`TCG_AI_BUILD.md` section 16).

The plan asks for the game to reach the model as a *sequence of objects* --
a global token, a token per player, one per battlefield, one per permanent,
one per card in hand -- rather than as a flat feature vector. This is that,
and it is pure standard library on purpose: what the model is allowed to see
is a property of the game, not of the framework that consumes it, and it
belongs on the side of the line that `tests/test_stdlib_only.py` guards.
`model/` turns these tokens into tensors; nothing here imports torch.

Why this exists, measured rather than argued
--------------------------------------------

`analysis/evaluation.py` reduces a position to 13 scalars. Building the style
agents measured what that costs: **77% of decisions are exact ties at the top
of the evaluator, and on 100% of sampled ties every tied action produces an
identical feature vector.** The evaluator cannot tell those actions apart, so
neither can anything built on it. `test_state_encoding.py` asserts the
converse for these tokens -- that positions the 13 features call identical
encode differently -- which is the whole case for section 16 in one test.

Perspective
-----------

Every token is written from the *acting player's* point of view: `owner` and
`controller` are "mine" or "theirs", never "player 0". A model that had to
learn seat symmetry would be spending capacity on a fact the encoder can
simply not introduce. `analysis/evaluation.py` made the same choice for the
same reason, and its antisymmetry is what catches a feature written from one
seat's view.

Shape
-----

Each token carries:

* **categoricals** -- token kind, card identity, zone, location -- meant to be
  looked up in embedding tables;
* **flags** -- binary status, one bit each;
* **scalars** -- costs, Might, damage, counters;
* **keywords** -- a multi-hot over the keyword vocabulary.

Absent objects are handled by a mask rather than by padding with zeros that
look like real values, as section 16 asks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cards.database import CardDatabase
from cards.keywords import ALL_KEYWORDS

# Token kinds. The order is part of the schema: changing it changes what an
# embedding table's row 3 means, so it is versioned by `schema_version()`.
TOKEN_KINDS: tuple[str, ...] = (
    "pad",          # only ever under a False mask
    "global",       # turn, phase, timing
    "me",           # my resources, points, zone sizes
    "opponent",     # theirs, as far as the privacy rules allow
    "battlefield",
    "unit",
    "gear",
    "rune",
    "hand",         # a card in my hand
    "trash",
    "champion",
    "legend",
    "chain",        # an item waiting on the chain
    "choice",       # an option in a pending choice (431.1.c.1)
)
KIND_INDEX = {name: index for index, name in enumerate(TOKEN_KINDS)}

# Binary status bits, one per flag. Order is schema.
FLAGS: tuple[str, ...] = (
    "mine",             # controlled by the acting player
    "owned_by_me",
    "exhausted",
    "stunned",
    "is_attacker",
    "is_defender",
    "is_token",
    "is_champion",
    "attached",
    "text_implemented",
    "at_battlefield",
    "contested",
)

# Numeric fields. Order is schema.
SCALARS: tuple[str, ...] = (
    "energy",
    "power",
    "might",
    "current_might",
    "damage",
    "buffs",
    "prevent",          # -1 encodes "prevent all" (437.1.b.1.b)
    "turn_number",
    "points_me",
    "points_them",
    "hand_size",
    "deck_size",
    "trash_size",
    "pool_energy",
    "pool_power",
)

ZONES: tuple[str, ...] = (
    "none", "hand", "base", "battlefield", "trash", "deck",
    "champion_zone", "legend_zone", "chain", "choice",
)
ZONE_INDEX = {name: index for index, name in enumerate(ZONES)}

MAX_LOCATION = 8        # "base" plus battlefield indices; see `_location`
KEYWORDS: tuple[str, ...] = tuple(sorted(ALL_KEYWORDS))
KEYWORD_INDEX = {name: index for index, name in enumerate(KEYWORDS)}


class EncodingOverflow(RuntimeError):
    """A position held more objects than the encoding has token slots."""


@dataclass(frozen=True)
class Token:
    kind: int
    card: int                       # index into the card vocabulary, 0 = none
    zone: int
    location: int
    flags: tuple[int, ...]
    scalars: tuple[float, ...]
    keywords: tuple[int, ...]


@dataclass
class EncodedState:
    """A padded token sequence and the mask that says which rows are real."""

    tokens: list[Token]
    mask: list[bool]
    player: int

    @property
    def length(self) -> int:
        return sum(self.mask)

    def columns(self) -> dict[str, list]:
        """Parallel lists, which is the shape a tensor wants."""
        return {
            "kind": [t.kind for t in self.tokens],
            "card": [t.card for t in self.tokens],
            "zone": [t.zone for t in self.tokens],
            "location": [t.location for t in self.tokens],
            "flags": [list(t.flags) for t in self.tokens],
            "scalars": [list(t.scalars) for t in self.tokens],
            "keywords": [list(t.keywords) for t in self.tokens],
            "mask": list(self.mask),
        }


_PAD = Token(
    kind=KIND_INDEX["pad"], card=0, zone=0, location=0,
    flags=(0,) * len(FLAGS), scalars=(0.0,) * len(SCALARS),
    keywords=(0,) * len(KEYWORDS),
)


class StateEncoder:
    """Turns one player's observation into a masked sequence of object tokens.

    Takes a `RiftboundObservation` and nothing else. It may not be handed a
    `RiftboundState`: an encoder with state access would feed a model
    information the interface does not grant, and no leakage test would catch
    it, because the leak would be in the encoder.
    """

    def __init__(self, db: CardDatabase, max_tokens: int = 192) -> None:
        # A stable vocabulary: sorted card ids, index 0 reserved for "no card".
        self.cards: tuple[str, ...] = tuple(sorted(db.cards))
        self.card_index = {card_id: i + 1 for i, card_id in enumerate(self.cards)}
        self.max_tokens = max_tokens

    # -- schema ------------------------------------------------------------

    def schema_version(self) -> str:
        """Digest over everything that changes what a column means.

        Derived rather than hand-maintained, like `engine/versions.py`: a
        checkpoint trained against one token layout cannot be loaded against
        another, and a version number someone has to remember to bump is a
        version number that will not be bumped.
        """
        digest = hashlib.sha256()
        for group in (TOKEN_KINDS, FLAGS, SCALARS, ZONES, KEYWORDS):
            digest.update("|".join(group).encode())
            digest.update(b"\x00")
        digest.update(str(len(self.cards)).encode())
        digest.update(str(self.max_tokens).encode())
        return digest.hexdigest()[:12]

    @property
    def widths(self) -> dict[str, int]:
        return {
            "kinds": len(TOKEN_KINDS),
            "cards": len(self.cards) + 1,
            "zones": len(ZONES),
            "locations": MAX_LOCATION,
            "flags": len(FLAGS),
            "scalars": len(SCALARS),
            "keywords": len(KEYWORDS),
            "max_tokens": self.max_tokens,
        }

    # -- helpers -----------------------------------------------------------

    def _card(self, card_id: str | None) -> int:
        if not card_id:
            return 0
        try:
            return self.card_index[card_id]
        except KeyError:
            raise KeyError(
                f"{card_id} is not in the encoder's vocabulary; the card pool "
                f"moved without the encoder being rebuilt"
            ) from None

    @staticmethod
    def _location(location: str | None) -> int:
        """0 nowhere, 1 base, 2+ battlefield index."""
        if location is None:
            return 0
        if location == "base":
            return 1
        if location.startswith("bf:"):
            index = int(location.split(":", 1)[1])
            if index + 2 >= MAX_LOCATION:
                raise EncodingOverflow(f"{location} exceeds MAX_LOCATION")
            return 2 + index
        return 0

    def _flags(self, **named: bool) -> tuple[int, ...]:
        return tuple(1 if named.get(name) else 0 for name in FLAGS)

    def _scalars(self, **named: float) -> tuple[float, ...]:
        return tuple(float(named.get(name, 0.0)) for name in SCALARS)

    def _keywords(self, names) -> tuple[int, ...]:
        out = [0] * len(KEYWORDS)
        for name in names:
            index = KEYWORD_INDEX.get(str(name))
            if index is not None:
                out[index] = 1
        return tuple(out)

    def _card_token(self, card, kind: str, zone: str, me: int) -> Token:
        mine = card.controller == me
        # 437.1.b.1.b -- None is "prevent all", which is not 0 and must not
        # encode as it. -1 is the sentinel; nothing else in this column is
        # negative.
        prevent = -1.0 if card.prevent is None else float(card.prevent)
        return Token(
            kind=KIND_INDEX[kind],
            card=self._card(card.card_id),
            zone=ZONE_INDEX[zone],
            location=self._location(card.location),
            flags=self._flags(
                mine=mine,
                owned_by_me=card.owner == me,
                exhausted=card.exhausted,
                stunned=card.stunned,
                is_attacker=card.is_attacker,
                is_defender=card.is_defender,
                is_champion=card.is_champion,
                attached=card.attached_to is not None,
                text_implemented=card.text_implemented,
                at_battlefield=bool(card.location
                                    and card.location.startswith("bf:")),
            ),
            scalars=self._scalars(
                energy=card.energy, power=card.power, might=card.might,
                current_might=card.current_might, damage=card.damage,
                buffs=card.buffs, prevent=prevent,
            ),
            keywords=self._keywords(
                list(card.keywords)
                + [granted[0] for granted in card.granted_keywords]
            ),
        )

    # -- the encoder ------------------------------------------------------

    def encode(self, obs) -> EncodedState:
        me = obs.player_id
        them = 1 - me
        tokens: list[Token] = []

        # --- global ------------------------------------------------------
        tokens.append(Token(
            kind=KIND_INDEX["global"], card=0, zone=ZONE_INDEX["none"],
            location=0,
            flags=self._flags(mine=obs.current_player == me),
            scalars=self._scalars(
                turn_number=obs.turn_number,
                points_me=obs.points[me], points_them=obs.points[them],
            ),
            keywords=self._keywords(()),
        ))

        # --- the two players ---------------------------------------------
        for kind, pid in (("me", me), ("opponent", them)):
            pool = obs.pool if pid == me else obs.opponent_pool
            hand_size = (len(obs.hand) if pid == me else obs.opponent_hand_size)
            tokens.append(Token(
                kind=KIND_INDEX[kind], card=0, zone=ZONE_INDEX["none"],
                location=0,
                flags=self._flags(mine=pid == me),
                scalars=self._scalars(
                    points_me=obs.points[pid],
                    points_them=obs.points[1 - pid],
                    hand_size=hand_size,
                    deck_size=obs.deck_sizes[pid],
                    trash_size=obs.trash_sizes[pid],
                    pool_energy=pool[0],
                    pool_power=sum(count for _, count in pool[1]) + pool[2],
                ),
                keywords=self._keywords(()),
            ))

        # --- battlefields -------------------------------------------------
        for battlefield in obs.battlefields:
            tokens.append(Token(
                kind=KIND_INDEX["battlefield"],
                card=self._card(battlefield.card_id),
                zone=ZONE_INDEX["battlefield"],
                location=2 + battlefield.index,
                flags=self._flags(
                    mine=battlefield.controller == me,
                    contested=battlefield.contested,
                    at_battlefield=True,
                ),
                scalars=self._scalars(),
                keywords=self._keywords(()),
            ))

        # --- permanents ----------------------------------------------------
        kind_of = {"unit": "unit", "gear": "gear", "rune": "rune"}
        for card in obs.board:
            tokens.append(self._card_token(
                card, kind_of.get(card.type, "gear"), "base", me))

        # --- my hand, and cards of theirs I have legally seen ---------------
        for card in obs.hand:
            tokens.append(self._card_token(card, "hand", "hand", me))
        for card in obs.revealed_opponent_hand:        # 424
            tokens.append(self._card_token(card, "hand", "hand", me))

        # --- trashes (108.2.d, public) --------------------------------------
        for pile in obs.trash:
            for card in pile:
                tokens.append(self._card_token(card, "trash", "trash", me))

        # --- champions and legends (108.3.e, 107.4.c) -----------------------
        for kind, zone, cards in (
            ("champion", "champion_zone", obs.champion_zone),
            ("legend", "legend_zone", obs.legend),
        ):
            for card in cards:
                if card is not None:
                    tokens.append(self._card_token(card, kind, zone, me))

        # --- the chain (327-340) --------------------------------------------
        for item in obs.chain:
            kind_name, label, controller, instance_id = item
            tokens.append(Token(
                kind=KIND_INDEX["chain"], card=0, zone=ZONE_INDEX["chain"],
                location=0,
                flags=self._flags(mine=controller == me),
                scalars=self._scalars(),
                keywords=self._keywords(()),
            ))

        # --- a pending choice, which only the chooser sees (431.1.c.1) -------
        for card in obs.choice_options:
            tokens.append(self._card_token(card, "choice", "choice", me))

        if len(tokens) > self.max_tokens:
            raise EncodingOverflow(
                f"{len(tokens)} tokens exceeds max_tokens={self.max_tokens}"
            )

        mask = [True] * len(tokens) + [False] * (self.max_tokens - len(tokens))
        tokens = tokens + [_PAD] * (self.max_tokens - len(tokens))
        return EncodedState(tokens=tokens, mask=mask, player=me)
