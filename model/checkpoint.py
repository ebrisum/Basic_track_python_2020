"""Saving and loading a model, with the versions that make it loadable.

A checkpoint is weights plus the shape of the world they were trained in. Load
a policy head against a different token layout and it will run: the tensors
still multiply, the outputs still look like logits, and every column means
something else. Nothing crashes and everything is wrong.

So a checkpoint carries the state encoder's `schema_version` and the action
encoder's segment layout, and `load` refuses a mismatch. Both digests are
derived from the layouts themselves rather than hand-maintained, for the
reason `engine/versions.py` gives: a version someone has to remember to bump
is a version that will not be bumped.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import torch

from engine.versions import provenance
from learning.action_encoding import ActionEncoder
from learning.state_encoding import StateEncoder
from model.network import ModelConfig, RiftboundNet

FORMAT = "riftbound-checkpoint/1"


class CheckpointMismatch(RuntimeError):
    """The world this checkpoint was trained in is not this one."""


def save(
    path: str | Path,
    network: RiftboundNet,
    state_encoder: StateEncoder,
    action_encoder: ActionEncoder | None = None,
    db=None,
    extra: dict | None = None,
) -> Path:
    action_encoder = action_encoder or ActionEncoder()
    payload = {
        "format": FORMAT,
        "config": asdict(network.config),
        "state_schema": state_encoder.schema_version(),
        "action_space": action_encoder.size,
        "action_segments": list(action_encoder.segments),
        "provenance": provenance(db) if db is not None else None,
        "weights": network.state_dict(),
        "extra": extra or {},
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)
    return path


def load(
    path: str | Path,
    state_encoder: StateEncoder,
    action_encoder: ActionEncoder | None = None,
    strict: bool = True,
) -> tuple[RiftboundNet, dict]:
    """Rebuild the network. Raises `CheckpointMismatch` on a changed world."""
    action_encoder = action_encoder or ActionEncoder()
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if payload.get("format") != FORMAT:
        raise CheckpointMismatch(
            f"{path} is {payload.get('format')!r}, not {FORMAT!r}"
        )

    if strict:
        if payload["state_schema"] != state_encoder.schema_version():
            raise CheckpointMismatch(
                "the state encoding changed since this checkpoint was saved "
                f"({payload['state_schema']} -> {state_encoder.schema_version()}). "
                "Its policy head would read every column as something else."
            )
        if payload["action_segments"] != list(action_encoder.segments):
            raise CheckpointMismatch(
                "the action space's segments changed since this checkpoint "
                "was saved; action-type indices no longer mean the same thing."
            )

    network = RiftboundNet(ModelConfig(**payload["config"]))
    network.load_state_dict(payload["weights"])
    return network, payload
