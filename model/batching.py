"""Turning encoded states and legal actions into tensors.

The only place in the project that knows both the pure-stdlib encodings and
torch. Keeping it here rather than in `learning/` is what lets the encoders
stay importable — and testable — with nothing installed.

Two things this file is careful about:

**An action's row is looked up, never assumed.** The policy head reads the
transformer's output at the token describing the object an action acts on, so
the mapping from action to row has to be exact. It comes from
`EncodedState.instances`, which the encoder fills in as it appends tokens, so
the two can not drift apart.

**Padding is masked, not zero-filled and hoped for.** A padded action entry
gets row -1 and mask False, and the network turns that into a -inf logit.
"""

from __future__ import annotations

import torch

from engine.actions import Action, Mulligan
from learning.action_encoding import ActionEncoder
from learning.state_encoding import EncodedState


def _type_indices(encoder: ActionEncoder) -> dict[str, int]:
    """Action-type name to a stable index, in the encoder's segment order."""
    return {name: index for index, name in enumerate(encoder.segments)}


def state_batch(states: list[EncodedState], device=None) -> dict[str, torch.Tensor]:
    """Stack encoded states into the batch the network's `embed` expects."""
    columns = [state.columns() for state in states]
    long = lambda key: torch.tensor(                       # noqa: E731
        [c[key] for c in columns], dtype=torch.long, device=device)
    return {
        "kind": long("kind"),
        "card": long("card"),
        "zone": long("zone"),
        "location": long("location"),
        "flags": torch.tensor([c["flags"] for c in columns],
                              dtype=torch.float32, device=device),
        "scalars": torch.tensor([c["scalars"] for c in columns],
                                dtype=torch.float32, device=device),
        "keywords": torch.tensor([c["keywords"] for c in columns],
                                 dtype=torch.float32, device=device),
        "mask": torch.tensor([c["mask"] for c in columns],
                             dtype=torch.bool, device=device),
    }


def _object_of(action: Action) -> int | None:
    """The game object an action acts on, if it names one."""
    if isinstance(action, Mulligan):
        # A mulligan names a *set*. It is pointed at the first card it sets
        # aside rather than at nothing, so the head has something related to
        # attend to; the set itself is carried by the option index.
        return action.instance_ids[0] if action.instance_ids else None
    return getattr(action, "instance_id", None)


def action_batch(
    states: list[EncodedState],
    legal: list[list[Action]],
    observations: list,
    encoder: ActionEncoder,
    device=None,
) -> dict[str, torch.Tensor]:
    """Pack each position's legal actions, padded to the widest in the batch."""
    if len(states) != len(legal) or len(states) != len(observations):
        raise ValueError("states, legal actions and observations must line up")
    types = _type_indices(encoder)
    width = max((len(actions) for actions in legal), default=0)
    width = max(width, 1)

    type_rows, option_rows, object_rows, masks, indices = [], [], [], [], []
    for state, actions, obs in zip(states, legal, observations):
        t, o, r, m, i = [], [], [], [], []
        for action in actions:
            name = type(action).__name__
            _slot, option = encoder._factor(obs, action)
            instance_id = _object_of(action)
            row = state.row_of(instance_id) if instance_id is not None else None
            t.append(types[name])
            o.append(option)
            r.append(-1 if row is None else row)
            m.append(True)
            i.append(encoder.encode(obs, action))
        pad = width - len(actions)
        type_rows.append(t + [0] * pad)
        option_rows.append(o + [0] * pad)
        object_rows.append(r + [-1] * pad)
        masks.append(m + [False] * pad)
        indices.append(i + [-1] * pad)

    return {
        "type": torch.tensor(type_rows, dtype=torch.long, device=device),
        "option": torch.tensor(option_rows, dtype=torch.long, device=device),
        "row": torch.tensor(object_rows, dtype=torch.long, device=device),
        "mask": torch.tensor(masks, dtype=torch.bool, device=device),
        # Not a network input: the flat action-space index for each entry, so
        # a sampled position can be turned back into an action and so a
        # trajectory's stored `action_index` can be matched to a column.
        "index": torch.tensor(indices, dtype=torch.long, device=device),
    }
