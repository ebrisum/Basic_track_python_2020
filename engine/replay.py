"""Replay format and runner -- the guard against silent rules drift.

A replay is an ordered list of `(player, action, expected_state_hash)`. The
engine must reproduce every one exactly. When a rules change alters behaviour,
a replay fails at the first divergent step and names it, instead of quietly
invalidating every number the system has ever produced.

Real games recorded on Rift Atlas get dropped into `tests/replays/` as-is.

Format (JSON):

    {
      "replay_id": "synthetic-001",
      "description": "human-readable",
      "source": "synthetic" | "rift-atlas" | ...,
      "game": "toy" | "riftbound",
      "seed": 12345,
      "initial_state_hash": "<hex>",
      "steps": [
        {"player": 0, "action": "<repr>", "expected_state_hash": "<hex>"},
        ...
      ],
      "final_returns": [1.0, 0.0]
    }

Actions are recorded by `repr()`. That keeps logs readable and diffable by
hand, which matters because a human reads these when a replay breaks. It also
means `repr()` is load-bearing: an action whose repr changes invalidates every
replay containing it. That is a feature -- it forces the change to be seen.

Nothing here knows any Riftbound rule; it runs against anything implementing
`engine.interface.GameState`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engine.interface import PLAYERS, Action, GameState

HASH_PREFIX_LEN = 16  # 64 bits -- ample for collision-free replay logs


def state_hash(state: GameState) -> str:
    """Canonical hash of a state, derived only from the frozen interface.

    Deliberately does not require states to grow a `state_hash()` method: the
    agent-facing contract stays exactly the five calls the brief specifies.

    Both players' observations are hashed, so hidden information on either
    side is covered -- hashing only the mover's view would let the opponent's
    hand drift undetected.
    """
    h = hashlib.sha256()
    for player in PLAYERS:
        h.update(state.observation(player).to_canonical_bytes())
        h.update(b"\x1f")
    terminal = state.is_terminal()
    h.update(b"T" if terminal else b"F")
    if terminal:
        for value in state.returns():
            h.update(f"{value:.6f}".encode())
            h.update(b"\x1f")
    else:
        h.update(str(state.current_player).encode())
    return h.hexdigest()[:HASH_PREFIX_LEN]


class ReplayDivergence(AssertionError):
    """Raised when the engine does not reproduce a recorded game."""


@dataclass
class ReplayStep:
    player: int
    action: str  # repr() of the action
    expected_state_hash: str


@dataclass
class Replay:
    replay_id: str
    seed: int
    steps: list[ReplayStep]
    initial_state_hash: str | None = None
    final_returns: tuple[float, float] | None = None
    description: str = ""
    source: str = "synthetic"
    game: str = "riftbound"
    # BUILD.md 37 -- what produced this replay. See `engine/versions.py`.
    # Empty for a replay recorded before stamping existed, which the drift
    # reporter treats as "nothing to compare" rather than as a mismatch.
    provenance: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Replay":
        returns = raw.get("final_returns")
        return cls(
            replay_id=raw["replay_id"],
            seed=raw["seed"],
            steps=[
                ReplayStep(
                    player=s["player"],
                    action=s["action"],
                    expected_state_hash=s["expected_state_hash"],
                )
                for s in raw["steps"]
            ],
            initial_state_hash=raw.get("initial_state_hash"),
            final_returns=tuple(returns) if returns is not None else None,
            description=raw.get("description", ""),
            source=raw.get("source", "synthetic"),
            game=raw.get("game", "riftbound"),
            provenance=dict(raw.get("provenance") or {}),
        )

    @classmethod
    def load(cls, path: Path) -> "Replay":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "replay_id": self.replay_id,
            "description": self.description,
            "source": self.source,
            "game": self.game,
            "provenance": dict(self.provenance),
            "seed": self.seed,
            "initial_state_hash": self.initial_state_hash,
            "steps": [
                {
                    "player": s.player,
                    "action": s.action,
                    "expected_state_hash": s.expected_state_hash,
                }
                for s in self.steps
            ],
        }
        if self.final_returns is not None:
            out["final_returns"] = list(self.final_returns)
        return out

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")


def _resolve(state: GameState, action_repr: str, step_index: int) -> Action:
    """Find the legal action whose repr matches the recorded one."""
    legal = state.legal_actions()
    matches = [a for a in legal if repr(a) == action_repr]
    if not matches:
        raise ReplayDivergence(
            f"step {step_index}: recorded action {action_repr!r} is not legal.\n"
            f"  legal actions were: {[repr(a) for a in legal]}\n"
            f"  the rules changed, or this replay predates a repr() change."
        )
    if len(matches) > 1:
        raise ReplayDivergence(
            f"step {step_index}: action repr {action_repr!r} is ambiguous -- "
            f"{len(matches)} legal actions share it. Action reprs must be "
            f"unique within a state or replays cannot address them."
        )
    return matches[0]


def describe_provenance_drift(recorded: dict[str, str] | None,
                             current: dict[str, str] | None) -> str:
    """Name every provenance component that moved between two stamps.

    Returns "" when nothing moved, or when either stamp is missing -- an
    unstamped replay predates stamping and has nothing to say. This is a
    *diagnostic*, never a failure on its own: a stamp can move while every
    recorded hash still matches, which is the happy case and means the change
    did not touch these games.

    It exists because a bare hash mismatch says only "something changed". This
    turns it into "the observation schema changed", which is the difference
    between a five-minute check and an afternoon.
    """
    if not recorded or not current:
        return ""
    drifted = [
        f"{key}: {recorded.get(key, '<absent>')} -> {current.get(key, '<absent>')}"
        for key in sorted(set(recorded) | set(current))
        if recorded.get(key) != current.get(key)
    ]
    if not drifted:
        return ""
    return "provenance drift since this replay was recorded:\n  " + "\n  ".join(drifted)


def run_replay(replay: Replay, build_state: Callable[[int], GameState],
               provenance: dict[str, str] | None = None) -> None:
    """Replay a recorded game, raising `ReplayDivergence` at the first mismatch.

    `build_state` takes the seed and returns a fresh initial state.
    `provenance` is the *current* stamp; when a divergence is raised, any
    component that has moved since recording is named in the message.
    """
    drift = describe_provenance_drift(replay.provenance, provenance)

    def diverge(message: str) -> ReplayDivergence:
        return ReplayDivergence(message + (f"\n{drift}" if drift else ""))

    state = build_state(replay.seed)

    if replay.initial_state_hash is not None:
        actual = state_hash(state)
        if actual != replay.initial_state_hash:
            raise diverge(
                f"{replay.replay_id}: initial state hash mismatch "
                f"(expected {replay.initial_state_hash}, got {actual}). "
                f"Setup or shuffle changed for seed {replay.seed}."
            )

    for i, step in enumerate(replay.steps):
        if state.is_terminal():
            raise diverge(
                f"{replay.replay_id} step {i}: game ended early -- "
                f"{len(replay.steps) - i} recorded step(s) remain."
            )
        if state.current_player != step.player:
            raise diverge(
                f"{replay.replay_id} step {i}: expected player {step.player} "
                f"to move, engine says player {state.current_player}. "
                f"Turn/priority order diverged."
            )

        state.apply(_resolve(state, step.action, i))

        actual = state_hash(state)
        if actual != step.expected_state_hash:
            raise diverge(
                f"{replay.replay_id} step {i}: state hash mismatch after "
                f"player {step.player} played {step.action}\n"
                f"  expected {step.expected_state_hash}\n"
                f"  got      {actual}\n"
                f"  the engine's handling of this action changed."
            )

    if replay.final_returns is not None:
        if not state.is_terminal():
            raise diverge(
                f"{replay.replay_id}: replay recorded a finished game, but the "
                f"engine's state is not terminal after all steps."
            )
        actual_returns = state.returns()
        if tuple(actual_returns) != tuple(replay.final_returns):
            raise diverge(
                f"{replay.replay_id}: final returns mismatch "
                f"(expected {tuple(replay.final_returns)}, got {actual_returns})."
            )


@dataclass
class ReplayRecorder:
    """Records a game as it is played, producing a `Replay`.

    Used to mint synthetic replays now and to convert Rift Atlas logs later.
    """

    replay_id: str
    seed: int
    description: str = ""
    source: str = "synthetic"
    game: str = "riftbound"
    provenance: dict[str, str] = field(default_factory=dict)
    steps: list[ReplayStep] = field(default_factory=list)
    initial_state_hash: str | None = None

    def start(self, state: GameState) -> None:
        self.initial_state_hash = state_hash(state)

    def record(self, state_before: GameState, action: Action, state_after: GameState) -> None:
        self.steps.append(
            ReplayStep(
                player=state_before.current_player,
                action=repr(action),
                expected_state_hash=state_hash(state_after),
            )
        )

    def finish(self, state: GameState) -> Replay:
        return Replay(
            replay_id=self.replay_id,
            seed=self.seed,
            steps=self.steps,
            initial_state_hash=self.initial_state_hash,
            final_returns=state.returns() if state.is_terminal() else None,
            description=self.description,
            source=self.source,
            game=self.game,
            provenance=dict(self.provenance),
        )


def record_game(
    build_state: Callable[[int], GameState],
    policy: Callable[[GameState], Action],
    seed: int,
    replay_id: str,
    description: str = "",
    game: str = "riftbound",
    max_steps: int = 10_000,
    provenance: dict[str, str] | None = None,
) -> Replay:
    """Play one game under `policy` and return it as a `Replay`."""
    state = build_state(seed)
    rec = ReplayRecorder(
        replay_id=replay_id, seed=seed, description=description, game=game,
        provenance=dict(provenance or {}),
    )
    rec.start(state)

    steps = 0
    while not state.is_terminal():
        if steps >= max_steps:
            raise RuntimeError(
                f"{replay_id}: exceeded {max_steps} steps without terminating -- "
                f"likely a rules loop."
            )
        action = policy(state)
        before_player = state.current_player
        rec.steps.append(
            ReplayStep(player=before_player, action=repr(action), expected_state_hash="")
        )
        state.apply(action)
        rec.steps[-1].expected_state_hash = state_hash(state)
        steps += 1

    return rec.finish(state)
