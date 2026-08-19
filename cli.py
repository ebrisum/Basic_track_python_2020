#!/usr/bin/env python3
"""One entry point for everything this project can do (BUILD.md section 31).

    python3 cli.py <command> [options]
    python3 cli.py --help

The plan asks for `ai validate`, `ai benchmark`, `ai simulate`, and so on, and
says equivalent commands are acceptable. These are the equivalents. Each one
delegates to the module that already owned the work, so this file adds a door
and no behaviour -- the point is that a newcomer does not have to know that
validation lives in `analysis.validate` and scenarios in `analysis.scenarios`.

Two commands the plan lists do not exist yet, and this says so rather than
printing a stub that looks like it worked:

    train      needs a neural policy/value model (sections 16-18, 22), which
               needs a dependency beyond the standard library. That decision
               is open -- see BUILD_STATUS.md.
    selfplay   `analysis.self_play_loop` fits *evaluator weights* by self-play
               and is wired up below, but it is not the section-21 self-play
               that trains a network on trajectories.
"""

from __future__ import annotations

import argparse
import json
import sys

COMMANDS = """commands:
  validate    the large-scale simulator validation run (section 10)
  simulate    alias for validate, in the plan's wording
  benchmark   head-to-head match between two agents (section 13)
  evaluate    grade an agent on the fixed scenarios (section 27)
  config      print a merged configuration (section 30)
  generate    write a trajectory dataset from baseline agents (sections 14, 20)
  fit         fit evaluator weights to recorded outcomes
  selfplay    generational self-play over evaluator weights
  play        start the local web frontend
  train       not implemented -- see the module docstring
"""


def _agents():
    from agents.greedy_agent import GreedyAgent
    from agents.random_agent import RandomAgent
    from agents.styles import AggroAgent, ConservativeAgent, ObjectiveAgent
    return {
        "random": RandomAgent,
        "greedy": GreedyAgent,
        "aggro": AggroAgent,
        "conservative": ConservativeAgent,
        "objective": ObjectiveAgent,
    }


def _cmd_validate(rest: list[str]) -> int:
    from analysis.validate import main
    return main(rest)


def _cmd_benchmark(rest: list[str]) -> int:
    from analysis.benchmark import main
    return main(rest)


def _cmd_evaluate(rest: list[str]) -> int:
    from analysis.scenarios.__main__ import main
    return main(rest)


def _cmd_generate(rest: list[str]) -> int:
    from learning.config import ConfigError, load as load_config

    # Precedence is defaults < config file < flags, so every flag defaults to
    # None here and only a value the person actually typed overrides the file.
    parser = argparse.ArgumentParser(prog="cli.py generate")
    parser.add_argument("--config", default=None,
                        help="a name from config/ or a path to a .toml")
    parser.add_argument("--games", type=int, default=None)
    parser.add_argument("--out", default="data/trajectories/baseline.jsonl.gz")
    parser.add_argument("--agents", nargs=2, default=None)
    parser.add_argument("--decks", nargs=2, default=None)
    parser.add_argument("--seed0", type=int, default=None)
    parser.add_argument("--progress", type=int, default=None)
    args = parser.parse_args(rest)

    try:
        config = load_config(args.config)
    except ConfigError as problem:
        print(str(problem), file=sys.stderr)
        return 2
    if args.games is None:
        args.games = config["run"]["games"]
    if args.seed0 is None:
        args.seed0 = config["run"]["seed0"]
    if args.progress is None:
        args.progress = config["run"]["progress"]
    if args.agents is None:
        args.agents = list(config["game"]["agents"])
    if args.decks is None:
        args.decks = list(config["game"]["decks"])

    from pathlib import Path
    from learning.generate import generate

    table = _agents()
    unknown = [name for name in args.agents if name not in table]
    if unknown:
        print(f"unknown agent(s): {', '.join(unknown)}; "
              f"choose from {', '.join(sorted(table))}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = generate(
        out, args.games,
        tuple(table[name] for name in args.agents),
        decks=tuple(args.decks), seed0=args.seed0, progress=args.progress,
        action_cap=config["run"]["action_cap"],
    )
    summary["config"] = args.config or "built-in defaults"
    summary["agents"] = list(args.agents)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_fit(rest: list[str]) -> int:
    from analysis.fit_weights import main
    return main(rest)


def _cmd_selfplay(rest: list[str]) -> int:
    from analysis.self_play_loop import main
    return main(rest)


def _cmd_play(rest: list[str]) -> int:
    """`play.py` parses `sys.argv` itself, so its flags are handed over there."""
    import play
    argv = sys.argv
    sys.argv = [argv[0]] + rest
    try:
        return play.main()
    finally:
        sys.argv = argv


def _cmd_train(rest: list[str]) -> int:
    print(
        "train: not implemented.\n\n"
        "Sections 16-18 and 22 need a neural policy/value model, which needs a\n"
        "dependency beyond the standard library (NumPy at minimum, realistically\n"
        "PyTorch). That decision is open and is not one this CLI should make\n"
        "quietly. See BUILD_STATUS.md, 'The decision that blocks this block'.\n\n"
        "What does work today: `fit` fits the linear evaluator, `selfplay` runs\n"
        "generations of it, and `generate` writes the trajectories a future\n"
        "trainer would consume.",
        file=sys.stderr,
    )
    return 2


def _cmd_config(rest: list[str]) -> int:
    """Print the configuration a run would actually use.

    A config system nobody can inspect is a second place for behaviour to
    hide. This resolves defaults, file and (nothing else, here) and prints the
    result, so "what did that run use" has an answer.
    """
    from learning.config import ConfigError, load as load_config

    parser = argparse.ArgumentParser(prog="cli.py config")
    parser.add_argument("name", nargs="?", default=None)
    args = parser.parse_args(rest)
    try:
        print(json.dumps(load_config(args.name), indent=2))
    except ConfigError as problem:
        print(str(problem), file=sys.stderr)
        return 2
    return 0


HANDLERS = {
    "validate": _cmd_validate,
    "config": _cmd_config,
    "simulate": _cmd_validate,
    "benchmark": _cmd_benchmark,
    "evaluate": _cmd_evaluate,
    "generate": _cmd_generate,
    "fit": _cmd_fit,
    "selfplay": _cmd_selfplay,
    "play": _cmd_play,
    "train": _cmd_train,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        print(COMMANDS)
        return 0
    command, rest = argv[0], argv[1:]
    handler = HANDLERS.get(command)
    if handler is None:
        print(f"unknown command {command!r}\n", file=sys.stderr)
        print(COMMANDS, file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
