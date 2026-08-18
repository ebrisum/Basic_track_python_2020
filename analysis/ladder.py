"""How to tell whether training actually improved anything.

Self-play produces a sequence of agents. The hard part is not producing them,
it is knowing whether generation 7 is better than generation 0 -- and the
obvious method, "each generation plays the one before it", cannot answer that.
Strength is not transitive: A beats B, B beats C, C beats A is an ordinary
outcome in games, and a self-play loop gated only on the incumbent will happily
walk in a circle for ten generations and report ten improvements.

This module is the standard answer, borrowed rather than invented:

* **Elo on a logistic scale**, so scores against different opponents become one
  comparable number. +400 Elo is a 10:1 expected score, by definition.
* **A frozen gauntlet** -- a fixed set of reference opponents that never
  changes. Every generation is scored against the same opponents, so the
  numbers are comparable across the whole run. This is how engine ladders work.
* **SPRT**, the Sequential Probability Ratio Test, as used by computer-chess
  testing (Stockfish's fishtest). It watches the result as it comes in and
  stops the moment the evidence is decisive, instead of committing to a fixed
  game count in advance.
* **A league** -- every promoted generation is kept, not overwritten, so the
  gauntlet can grow and old agents remain playable.

## Why SPRT rather than a fixed number of games

A fixed-N test has to be sized for the smallest edge worth detecting. At 95%
confidence that is roughly `(1.96 * 0.5 / (p - 0.5))^2` games: about **385**
for a true 55% score, and this engine plays ~0.66 greedy games/second. SPRT
resolves the same question in far fewer games on average, because most
candidates are either clearly good or clearly bad and it stops as soon as it
can tell -- spending the long runs only on the genuinely marginal cases.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from analysis.evaluation import Model

LEAGUE_DIR = Path(__file__).resolve().parent / "league"

# A win rate of exactly 0 or 1 is infinite Elo, which poisons any table it
# lands in. Clamping treats a clean sweep as very strong evidence rather than
# infinite evidence -- the standard fix.
_CLAMP = 1e-3


def score_from_elo(elo: float) -> float:
    """Expected score for a rating advantage of `elo`."""
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def elo_from_score(score: float) -> float:
    """Rating difference implied by an observed score."""
    score = min(1.0 - _CLAMP, max(_CLAMP, score))
    return -400.0 * math.log10(1.0 / score - 1.0)


def elo_interval(score: float, games: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval on the rating, from a normal interval on the score."""
    if games <= 0:
        return (float("-inf"), float("inf"))
    spread = z * math.sqrt(max(score * (1 - score), 1e-9) / games)
    return (
        elo_from_score(min(1.0, max(0.0, score - spread))),
        elo_from_score(min(1.0, max(0.0, score + spread))),
    )


@dataclass
class SPRT:
    """Sequential Probability Ratio Test on a game-by-game result stream.

    Tests H0 "the candidate is `elo0` stronger" against H1 "it is `elo1`
    stronger", accumulating a log-likelihood ratio and stopping when it
    crosses a bound. `alpha` and `beta` are the false-accept and false-reject
    rates; the fishtest defaults are 0.05 each.

    Call `record()` with 1.0, 0.5 or 0.0 per game and check `verdict()`.
    """

    elo0: float = 0.0
    elo1: float = 20.0
    alpha: float = 0.05
    beta: float = 0.05
    llr: float = 0.0
    games: int = 0
    total: float = 0.0

    @property
    def lower(self) -> float:
        return math.log(self.beta / (1 - self.alpha))

    @property
    def upper(self) -> float:
        return math.log((1 - self.beta) / self.alpha)

    def record(self, result: float) -> None:
        """Add one game result: 1.0 win, 0.5 draw, 0.0 loss."""
        self.games += 1
        self.total += result
        p0 = score_from_elo(self.elo0)
        p1 = score_from_elo(self.elo1)
        # Bernoulli log-likelihood ratio, with a draw counted as half a win --
        # the usual simplification when draws are rare.
        for weight, outcome in ((result, 1.0), (1.0 - result, 0.0)):
            if weight <= 0:
                continue
            like1 = p1 if outcome == 1.0 else 1 - p1
            like0 = p0 if outcome == 1.0 else 1 - p0
            self.llr += weight * math.log(like1 / like0)

    def verdict(self) -> str:
        """"accept" (H1), "reject" (H0), or "continue"."""
        if self.llr >= self.upper:
            return "accept"
        if self.llr <= self.lower:
            return "reject"
        return "continue"

    @property
    def score(self) -> float:
        return self.total / self.games if self.games else 0.0

    def summary(self) -> str:
        return (
            f"{self.games} games, score {self.score:.3f}, "
            f"LLR {self.llr:+.2f} "
            f"[{self.lower:.2f}, {self.upper:.2f}] -> {self.verdict()}"
        )


class League:
    """Every promoted generation, kept on disk.

    The loop used to write one weights file and overwrite it on each
    promotion, which made the ladder impossible to build: once generation 3
    was installed, generation 2 no longer existed to play against. Keeping
    them costs a few hundred bytes each and is what makes "is this actually
    better than where we started" answerable at all.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or LEAGUE_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, generation: int) -> Path:
        return self.directory / f"gen_{generation:03d}.json"

    def add(self, generation: int, model: Model) -> Path:
        path = self._path(generation)
        path.write_text(
            json.dumps(
                {
                    "generation": generation,
                    "weights": list(model.weights),
                    "bias": model.bias,
                    "fitted": model.fitted,
                    "trained_on_games": getattr(model, "trained_on_games", 0),
                },
                indent=2,
            )
            + "\n"
        )
        return path

    def generations(self) -> list[int]:
        out = []
        for path in self.directory.glob("gen_*.json"):
            try:
                out.append(int(path.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return sorted(out)

    def get(self, generation: int) -> Model:
        raw = json.loads(self._path(generation).read_text())
        return Model(
            weights=tuple(raw["weights"]),
            bias=raw["bias"],
            fitted=raw.get("fitted", False),
            trained_on_games=raw.get("trained_on_games", 0),
        )

    def gauntlet(self) -> list[tuple[str, Model | None]]:
        """The fixed reference opponents, oldest first.

        `None` means "not a weighted model" -- the random agent, which is the
        one anchor whose strength cannot drift. Generations are appended in
        order, so an entry's position never changes as the league grows and
        older columns of a results table stay meaningful.
        """
        entries: list[tuple[str, Model | None]] = [("random", None)]
        for generation in self.generations():
            entries.append((f"gen{generation}", self.get(generation)))
        return entries
