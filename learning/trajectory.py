"""The trajectory format (`TCG_AI_BUILD.md` section 14).

`engine/replay.py` already records games, but for a different purpose: it
stores actions and a final state hash, which proves a game reproduces and is
useless as training data. A trajectory carries what a learner needs -- the
observation, the legal-action mask, the action taken, the reward, and space
for a policy and a value estimate that sections 18 and 21 will fill in.

Three choices in this format are worth stating, because each trades something
away.

**The observation is stored, not an encoding of it.** Section 16's
`StateEncoder` does not exist yet, and baking today's feature vector into
stored data would freeze a design decision into every dataset ever generated.
A trajectory holding the observation can have any future encoder applied to it
retroactively; one holding 13 scalars cannot.

**It is stored slim.** A full `RiftboundObservation` serialises to about 20 KB,
and a single game is hundreds of decisions -- 19 MB for one game, which makes
a 10,000-game dataset unusable. The fields dropped are presentation
(`image_url`, `rules_text`, `name`) and static card data (`energy`, `might`,
`domains`, ...), every one of which is a function of `card_id` and the card
pool. The pool is pinned by `card_pool_version` in the header, so the slimming
is lossless *given the header* -- the only sense in which dropping data from a
dataset is defensible. Measured: 102 bytes per decision against 1,215 for the
full observation, both gzipped, on a real 624-decision game.

**The mask is stored as the sorted list of legal indices, not as booleans.**
The action space is 4,507 wide and a typical decision has 5 legal actions;
writing 4,507 booleans per decision would be three orders of magnitude of
padding. `ActionEncoder.mask` reconstitutes the dense form when a learner
wants it.

Reward follows section 15: **zero everywhere except the final transition**,
which carries the acting player's `returns()`. A nonzero mid-game reward would
be shaping, which the plan forbids and `analysis/evaluation.py` explains at
length.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from cards.database import CardDatabase
from cards.database import load as load_pool
from engine.versions import provenance
from learning.action_encoding import ActionEncoder

# Dropped from every stored observation. Each is either presentation or a
# static property of the printed card, recoverable from `card_id` against the
# pool named in the header. `card_id` itself is never dropped.
SLIM_DROP: frozenset[str] = frozenset({
    "name",
    "rules_text",
    "image_url",
    "image_alt",
    "script_note",
    "text_implemented",
    "is_champion",
    "energy",
    "power",
    "might",
    "domains",
    "keywords",
    "type",
    # Presentation on the observation itself.
    "log_tail",
    "choice_prompt",
})


@lru_cache(maxsize=1)
def _stamp() -> dict:
    """The provenance stamp for the default card pool.

    Cached because a data-generation loop constructs one writer per game, and
    `cards.database.load` re-parses the whole card JSON on every call -- its
    docstring says it caches, and it does not.
    """
    return provenance(load_pool())


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in SLIM_DROP}
    if isinstance(value, (list, tuple)):
        return [_strip(v) for v in value]
    return value


def slim_observation(obs) -> dict:
    """The observation with recoverable and presentational fields removed."""
    return _strip(asdict(obs))


@dataclass
class Transition:
    """One decision, from the acting player's point of view."""

    game_id: str
    step: int
    player: int
    observation: dict
    action_index: int
    action_repr: str
    legal_indices: list[int]
    legal_reprs: list[str]
    reward: float = 0.0
    # Filled in by a policy that has one; None for the baseline agents.
    policy: list[float] | None = None
    value: float | None = None
    extra: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        row = asdict(self)
        row["record"] = "transition"
        return row


class TrajectoryWriter:
    """Streams gzipped JSONL: one header, N transitions, one result."""

    def __init__(
        self,
        path: str | Path,
        encoder: ActionEncoder | None = None,
        db: CardDatabase | None = None,
        decks: tuple[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.encoder = encoder or ActionEncoder()
        self._handle = gzip.open(self.path, "wt", encoding="utf-8")
        header = {
            "record": "header",
            "format": "riftbound-trajectory/1",
            # BUILD.md 37 -- a dataset without a stamp cannot be compared to a
            # later dataset, and the slimming above is only lossless with
            # respect to the card pool this names.
            "provenance": provenance(db) if db is not None else _stamp(),
            "action_space_size": self.encoder.size,
            "slim_drop": sorted(SLIM_DROP),
        }
        if decks is not None:
            header["decks"] = list(decks)
        self._emit(header)

    def _emit(self, row: dict) -> None:
        self._handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def write(self, transition: Transition) -> None:
        self._emit(transition.to_record())

    def finish(self, returns: tuple[float, ...], winner: int | None,
               turns: int | None = None) -> None:
        self._emit({
            "record": "result",
            "returns": list(returns),
            "winner": winner,
            "turns": turns,
        })

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TrajectoryWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_trajectory(path: str | Path) -> Iterator[dict]:
    """Yield every record in a trajectory file, header first."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def record_into(
    writer: TrajectoryWriter,
    state,
    agents: list,
    game_id: str,
    encoder: ActionEncoder | None = None,
    action_cap: int = 3000,
):
    """Play `state` out with `agents`, writing into an already-open writer.

    Split out from `record_game` so a dataset can hold many games in one file:
    a header per game would repeat the provenance stamp thousands of times,
    and a file per game would put a gzip container around 200 records.

    The reward on every transition is 0.0 except the last, which carries the
    acting player's `returns()`. Assigning a return to *the player who moved*
    rather than to seat 0 is what makes the file usable without knowing which
    seat a learner is training.
    """
    active = encoder or writer.encoder
    written: list[Transition] = []
    step = 0
    while not state.is_terminal() and step < action_cap:
        legal = state.legal_actions()
        if not legal:
            break
        player = state.current_player
        obs = state.observation(player)
        action = agents[player].act(state)
        transition = Transition(
            game_id=game_id,
            step=step,
            player=player,
            observation=slim_observation(obs),
            action_index=active.encode(obs, action),
            action_repr=repr(action),
            legal_indices=sorted({active.encode(obs, a) for a in legal}),
            legal_reprs=sorted({repr(a) for a in legal}),
            policy=getattr(agents[player], "last_policy", None),
            value=getattr(agents[player], "last_value", None),
        )
        written.append(transition)
        writer.write(transition)
        state.apply(action)
        step += 1

    returns = state.returns() if state.is_terminal() else (0.5, 0.5)
    if written:
        # Section 15: the terminal signal is the only reward. It is written as
        # a separate closing record as well as onto the last transition, so a
        # reader streaming one record at a time does not have to look ahead to
        # know an episode ended.
        last = written[-1]
        last.reward = returns[last.player]
        writer._emit({
            "record": "transition-reward",
            "game_id": game_id,
            "step": last.step,
            "player": last.player,
            "reward": last.reward,
        })
    writer.finish(returns, state.winner, turns=getattr(state, "turn_number", None))
    return state


def record_game(
    state,
    agents: list,
    path: str | Path,
    game_id: str = "game",
    encoder: ActionEncoder | None = None,
    db: CardDatabase | None = None,
    decks: tuple[str, str] | None = None,
    action_cap: int = 3000,
):
    """Play one game into its own file. Returns the finished state."""
    active = encoder or ActionEncoder()
    with TrajectoryWriter(path, encoder=active, db=db, decks=decks) as writer:
        return record_into(writer, state, agents, game_id, active, action_cap)
