"""Run configuration (`TCG_AI_BUILD.md` section 30).

The plan says "do not hard-code training parameters" and asks for
`ai/config/*.yaml`. These are `config/*.toml` instead, read with the standard
library's `tomllib`. That is a deliberate substitution, not an oversight:
YAML needs PyYAML, the project's standing rule is stdlib plus pytest with any
dependency asked for first, and a configuration format is a poor thing to
spend the first dependency on. TOML gets the same job done from the standard
library since 3.11.

Precedence, which is the only part of a config system anyone actually needs to
know: **built-in defaults < config file < command-line flags.** A flag always
wins, so a config file can never silently override something a person typed.

One field is validated rather than merely read. `reward.shaping` must be
false. Section 15 asks for an unshaped terminal reward and
`analysis/evaluation.py` explains at length why the heuristic is a *prior over
winning* rather than a payout -- a config key that could quietly turn points
into reward would undo that in one line, so setting it raises instead.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# The values every run starts from. A config file names only what it changes.
DEFAULTS: dict[str, Any] = {
    "run": {
        "games": 100,
        "seed0": 0,
        "action_cap": 3000,
        "progress": 25,
        "workers": 1,
    },
    "game": {
        "decks": ["jinx_chaos_fury", "volibear_body_fury"],
        "agents": ["random", "greedy"],
    },
    "reward": {
        # 1.0 win / 0.0 loss / 0.5 draw, and nothing else. See the module note.
        "shaping": False,
    },
    "curriculum": {
        # Section 26. No level restricts anything yet; the key exists so a
        # config naming a level fails loudly rather than being ignored.
        "level": 0,
    },
    "evaluation": {
        "scenarios": True,
        "interval_games": 1000,
    },
    "league": {
        "checkpoint_interval": 1,
        "sampling": "uniform",
    },
}


class ConfigError(ValueError):
    """A configuration file asked for something the project will not do."""


def _merge(base: dict, overlay: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def validate(config: dict) -> dict:
    if config.get("reward", {}).get("shaping"):
        raise ConfigError(
            "reward.shaping must be false. Section 15 asks for an unshaped "
            "terminal reward, and analysis/evaluation.py explains why the "
            "heuristic is a prior over winning rather than a payout. Turning "
            "this on would make the proxy the objective."
        )
    level = config.get("curriculum", {}).get("level", 0)
    if level not in (0,):
        raise ConfigError(
            f"curriculum.level {level} is not implemented -- section 26 has no "
            f"content restrictions yet. Level 0 is the full game."
        )
    return config


def load(name: str | Path | None = None) -> dict:
    """Load a config by name (`default`) or path. Returns merged defaults."""
    if name is None:
        return validate(_merge(DEFAULTS, {}))
    path = Path(name)
    if not path.exists():
        path = CONFIG_DIR / f"{name}.toml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in CONFIG_DIR.glob("*.toml")))
        raise ConfigError(f"no config {name!r}; available: {available}")
    with path.open("rb") as handle:
        overlay = tomllib.load(handle)
    return validate(_merge(DEFAULTS, overlay))
