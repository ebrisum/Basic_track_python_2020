"""Embedding -> transformer -> policy and value heads (sections 17 and 18).

The shape the plan asks for, with one decision made the way section 18 prefers
rather than the way that would have been easier.

**The policy head points at objects; it is not a 4,507-wide output layer.**
Section 18 says to score the actual legal actions and allows a fixed masked
vector as a v1 fallback. The fallback would have been trivial here -- the
action space already exists with a mask -- but the pointer form is a better
fit for this game and costs no more. An action factorises into (type, slot,
option), and the slot names a *game object*: the unit being moved, the card
being played, the target being chosen. `learning/state_encoding.py` already
emits one token per object, so the score for an action is read off the
transformer's output at the row describing the object it acts on:

    logit(action) = MLP([ type_emb ; option_emb ; H[object_row] ; pooled ])

An action naming no object -- pass, channel, concede -- gets a learned
"no object" vector in that slot. Nothing about this depends on the action
space's width, so a card pool that doubles the number of objects does not
widen a single layer.

**Value is in [-1, +1]**, as section 17 specifies, which is an affine remap of
`state.returns()`'s 1.0 / 0.5 / 0.0 rather than a second opinion about it.
`analysis/evaluation.py` explains at length why the reward and the heuristic
must not be confused; the same applies here.

Sizes come from configuration (section 30), defaulting to the plan's own
example: 128-dimensional embeddings, 4 layers, 4 heads, 512 feed-forward.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from learning.state_encoding import FLAGS, KEYWORDS, SCALARS


@dataclass(frozen=True)
class ModelConfig:
    """Section 17's example defaults, overridable from `config/*.toml`."""

    embedding_dim: int = 128
    transformer_layers: int = 4
    attention_heads: int = 4
    ff_dim: int = 512
    dropout: float = 0.0

    # Vocabulary sizes, which come from the encoder rather than being guessed.
    kinds: int = 14
    cards: int = 698
    zones: int = 10
    locations: int = 8
    action_types: int = 13
    action_options: int = 8

    @classmethod
    def from_config(cls, config: dict, encoder) -> "ModelConfig":
        """Merge a `learning.config` dict with an encoder's actual widths."""
        model = dict(config.get("model", {}))
        widths = encoder.widths
        return cls(
            embedding_dim=int(model.get("embedding_dim", 128)),
            transformer_layers=int(model.get("transformer_layers", 4)),
            attention_heads=int(model.get("attention_heads", 4)),
            ff_dim=int(model.get("ff_dim", 512)),
            dropout=float(model.get("dropout", 0.0)),
            kinds=widths["kinds"],
            cards=widths["cards"],
            zones=widths["zones"],
            locations=widths["locations"],
        )


class RiftboundNet(nn.Module):
    """Object tokens in; a logit per legal action and one value out."""

    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        dim = self.config.embedding_dim

        # One embedding table per categorical column. Section 16's "each game
        # object becomes an embedding", summed rather than concatenated so the
        # width does not grow with the number of columns.
        self.kind = nn.Embedding(self.config.kinds, dim)
        self.card = nn.Embedding(self.config.cards, dim, padding_idx=0)
        self.zone = nn.Embedding(self.config.zones, dim)
        self.location = nn.Embedding(self.config.locations, dim)

        # The numeric columns go through one linear layer. Flags and keywords
        # are already 0/1; scalars are raw game numbers, so they are normalised
        # here rather than in the encoder -- the encoder's job is to say what
        # is true, not to pick a scale for a particular model.
        numeric = len(FLAGS) + len(SCALARS) + len(KEYWORDS)
        self.numeric = nn.Linear(numeric, dim)
        self.scalar_norm = nn.LayerNorm(len(SCALARS))
        self.token_norm = nn.LayerNorm(dim)

        layer = nn.TransformerEncoderLayer(
            d_model=dim,
            nhead=self.config.attention_heads,
            dim_feedforward=self.config.ff_dim,
            dropout=self.config.dropout,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, num_layers=self.config.transformer_layers,
            # `norm_first` disables the nested-tensor fast path anyway; saying
            # so explicitly keeps a warning out of every test run.
            enable_nested_tensor=False,
        )

        # Section 18's action side.
        self.action_type = nn.Embedding(self.config.action_types, dim)
        self.action_option = nn.Embedding(self.config.action_options, dim)
        # Stands in for H[row] when an action names no object.
        self.no_object = nn.Parameter(torch.zeros(dim))
        self.policy = nn.Sequential(
            nn.Linear(dim * 4, dim), nn.GELU(), nn.Linear(dim, 1),
        )

        self.value = nn.Sequential(
            nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, 1), nn.Tanh(),
        )

    # -- encoding ----------------------------------------------------------

    def embed(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        """(B, T, D) token embeddings before attention."""
        scalars = self.scalar_norm(batch["scalars"])
        numeric = torch.cat(
            [batch["flags"], scalars, batch["keywords"]], dim=-1
        )
        tokens = (
            self.kind(batch["kind"])
            + self.card(batch["card"])
            + self.zone(batch["zone"])
            + self.location(batch["location"])
            + self.numeric(numeric)
        )
        return self.token_norm(tokens)

    def encode(self, batch: dict[str, torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
        """Returns per-token hidden states and a masked mean pooling of them."""
        mask = batch["mask"]                                  # (B, T) bool
        hidden = self.encoder(self.embed(batch),
                              src_key_padding_mask=~mask)
        # Masked mean. Padded rows are excluded rather than averaged in as
        # zeros, which would make the pooled vector depend on how much padding
        # a position happened to need.
        weights = mask.unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1.0)
        return hidden, pooled

    # -- heads -------------------------------------------------------------

    def value_of(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        """(B,) expected result in [-1, +1] for the player to move."""
        _hidden, pooled = self.encode(batch)
        return self.value(pooled).squeeze(-1)

    def forward(
        self,
        batch: dict[str, torch.Tensor],
        actions: dict[str, torch.Tensor],
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Logits over each position's legal actions, and the value.

        `actions` carries, padded to the widest legal set in the batch:
          type   (B, A) long   -- action-type index
          option (B, A) long   -- the option within that type
          row    (B, A) long   -- token row this action acts on, or -1
          mask   (B, A) bool   -- which entries are real legal actions

        Illegal and padded entries are set to -inf, so a softmax over the
        result can only ever put mass on a legal action. Section 29's
        "the agent never attempts an illegal action" then holds by
        construction rather than by checking afterwards.
        """
        hidden, pooled = self.encode(batch)
        batch_size, num_actions = actions["type"].shape
        dim = self.config.embedding_dim

        rows = actions["row"]
        has_object = rows >= 0
        safe_rows = rows.clamp(min=0).unsqueeze(-1).expand(-1, -1, dim)
        gathered = hidden.gather(1, safe_rows)                 # (B, A, D)
        object_vector = torch.where(
            has_object.unsqueeze(-1),
            gathered,
            self.no_object.expand(batch_size, num_actions, dim),
        )

        context = pooled.unsqueeze(1).expand(-1, num_actions, -1)
        features = torch.cat(
            [
                self.action_type(actions["type"]),
                self.action_option(actions["option"]),
                object_vector,
                context,
            ],
            dim=-1,
        )
        logits = self.policy(features).squeeze(-1)             # (B, A)
        logits = logits.masked_fill(~actions["mask"], float("-inf"))
        return logits, self.value(pooled).squeeze(-1)
