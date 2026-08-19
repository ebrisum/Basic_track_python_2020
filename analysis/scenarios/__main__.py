"""Grade an agent against the benchmark scenarios.

    .venv/bin/python -m analysis.scenarios --agent greedy
    .venv/bin/python -m analysis.scenarios --agent all
"""

from __future__ import annotations

import argparse

from agents.greedy_agent import GreedyAgent
from agents.random_agent import RandomAgent
from agents.styles import AggroAgent, ConservativeAgent, ObjectiveAgent
from analysis.scenarios import SCENARIOS, grade

AGENTS = {
    "random": RandomAgent,
    "greedy": GreedyAgent,
    "aggro": AggroAgent,
    "conservative": ConservativeAgent,
    "objective": ObjectiveAgent,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="greedy",
                        choices=sorted(AGENTS) + ["all"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verbose", action="store_true",
                        help="print the full report, not just the score line")
    args = parser.parse_args(argv)

    names = sorted(AGENTS) if args.agent == "all" else [args.agent]
    for name in names:
        report = grade(AGENTS[name](args.seed), SCENARIOS)
        marks = " ".join("." if row.passed else "X" for row in report.rows)
        print(f"{name:14s} {report.score:.2f}  {marks}")
        if args.verbose:
            print(report.render())
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
