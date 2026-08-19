"""Fixed benchmark scenarios (`TCG_AI_BUILD.md` section 27).

A win rate says an agent is better. A scenario suite says *what* it got wrong,
which is the only kind of signal that survives a refactor of the evaluator.
Each scenario is a hand-built position with a small set of defensible answers
and a rationale citing the rule that makes them defensible.

Three properties are asserted about the suite itself in
`tests/test_scenarios.py`, before anything is asserted about an agent:

* every position passes `engine.invariants.check`, so no scenario grades an
  agent on a board the rules cannot reach;
* every accepted answer is legal in its position, so no scenario is unpassable;
* every scenario rejects at least one legal action, so none is free to pass.

The last one matters more than it sounds. A scenario that accepts everything
looks like a test and measures nothing, and it is the easy mistake to make
when the accepted set is written by the same hand that built the board.

**These are judgements, not rules.** The engine can say what is *legal*; it
cannot say what is *good*. Each `why` below is an argument, and a stronger
agent that disagrees with one of them is evidence about the scenario, not
only about the agent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

from analysis.scenarios.build import (
    channel,
    clear_hand,
    control,
    deploy_champion,
    exhaust_runes,
    give,
    opening,
    place,
    points,
    to_hand,
    to_main,
)
from engine.zones import BASE_LOCATION


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    player: int
    why: str
    _build: Callable[[], tuple]
    tags: tuple[str, ...] = ()

    def build(self):
        state, _meta = self._build()
        return state

    def accepted(self, state) -> set[str]:
        """The action reprs this scenario counts as right, in `state`."""
        _fresh, meta = self._build()
        return set(meta["accept"])


@dataclass
class Row:
    scenario_id: str
    name: str
    chosen: str
    accepted: tuple[str, ...]
    passed: bool
    why: str


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.rows:
            return 0.0
        return sum(1 for row in self.rows if row.passed) / len(self.rows)

    def render(self) -> str:
        lines = [f"score {self.score:.2f}  ({sum(r.passed for r in self.rows)}"
                 f"/{len(self.rows)})", ""]
        for row in self.rows:
            mark = "pass" if row.passed else "FAIL"
            lines.append(f"  {mark}  {row.scenario_id}  {row.name}")
            if not row.passed:
                lines.append(f"        chose {row.chosen}, wanted one of "
                             f"{', '.join(row.accepted)}")
                lines.append(f"        {row.why}")
        return "\n".join(lines)


def run_scenario(agent, scenario: Scenario) -> Row:
    """Ask an agent for one decision in a scenario position."""
    state = scenario.build()
    accepted = scenario.accepted(state)
    chosen = repr(agent.act(copy.deepcopy(state)))
    return Row(
        scenario_id=scenario.scenario_id,
        name=scenario.name,
        chosen=chosen,
        accepted=tuple(sorted(accepted)),
        passed=chosen in accepted,
        why=scenario.why,
    )


def grade(agent, scenarios=None) -> Report:
    return Report([run_scenario(agent, s) for s in (scenarios or SCENARIOS)])


# ---------------------------------------------------------------------------
# The scenarios
# ---------------------------------------------------------------------------

# Cards used below, with the stats the arguments depend on:
#   OGN-175 Shipyard Skulker   unit, 3 energy, 3 Might
#   OGN-013 Pouty Poro         unit, 2 energy, 2 Might
#   OGN-142 Mountain Drake     unit, 9 energy, 10 Might
#   OGN-009 Hextech Ray        spell, 1 energy + 1 power, "Deal 3 to a unit at
#                              a battlefield"


def _s001():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    deploy_champion(state, 0)
    exhaust_runes(state, 0)
    mine = place(state, 0, "OGN-175")            # ready, in my Base
    # One battlefield stands open; the other is theirs and defended by a unit
    # that eats mine for nothing.
    control(state, 1, controller=1)
    place(state, 1, "OGN-142", location="bf:1")
    return state, {"accept": [f"move:{mine}->bf:0"]}


def _s002():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    to_hand(state, 0, "OGN-013")                 # 2 energy, no power
    give(state, 0, energy=1)
    runes = state.players[0].channeled_runes
    ready = [r for r in runes if not state.cards[r].exhausted]
    return state, {"accept": [f"tap_energy:{r}" for r in ready]}


def _s003():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    # Exhausted, so "move the champion instead" is not on the menu: this
    # scenario is about whether to spend the removal, not about the champion.
    deploy_champion(state, 0, exhausted=True)
    exhaust_runes(state, 0)
    place(state, 0, "OGN-175", location="bf:0")
    small = place(state, 1, "OGN-013", location="bf:0")   # 2 Might
    big = place(state, 1, "OGN-175", location="bf:0")     # 3 Might
    place(state, 1, "OGN-142", location="bf:0")           # 10 Might, survives
    ray = to_hand(state, 0, "OGN-009")
    give(state, 0, energy=2, power={"Fury": 2})
    # Playing it is the decision here; which unit it hits is the next one, and
    # scenario 007 covers that. Accepting only the play keeps one decision per
    # scenario.
    return state, {"accept": [f"play:{ray}"], "small": small, "big": big}


def _s004():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    deploy_champion(state, 0)
    exhaust_runes(state, 0)
    mine = place(state, 0, "OGN-175")
    # They hold bf:0 with nothing standing on it. 190.3 -- moving in contests
    # it; leaving it alone concedes the score every turn. bf:1 is mine and
    # already defended, so reinforcing it adds nothing.
    control(state, 0, controller=1)
    control(state, 1, controller=0)
    place(state, 0, "OGN-142", location="bf:1")
    return state, {"accept": [f"move:{mine}->bf:0"]}


def _s005():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    to_hand(state, 0, "OGN-013")                 # costs 2 energy
    give(state, 0, energy=0)
    ready = [r for r in state.players[0].channeled_runes
             if not state.cards[r].exhausted]
    # Nothing can be done without energy, so declining is the one clear error.
    return state, {"accept": [f"tap_energy:{r}" for r in ready]
                             + [f"recycle_power:{r}" for r in ready]}


def _s006():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    deploy_champion(state, 0)
    exhaust_runes(state, 0)
    unit = to_hand(state, 0, "OGN-013")
    give(state, 0, energy=4)
    return state, {"accept": [f"play:{unit}"]}


def _s007():
    """The target-selection decision, reached by playing the removal first."""
    state, meta = _s003()
    ray = [i for i in state.players[0].hand][0]
    from engine.actions import PlayCard
    state.apply(PlayCard(instance_id=ray))
    # 3 damage kills the 3-Might unit and the 2-Might one; the 10-Might one
    # shrugs it off. Taking the bigger of the two it can actually kill is the
    # only answer that is better than the alternatives on every axis.
    return state, {"accept": [f"choose:{meta['big']}"]}


def _s008():
    state = to_main(opening(), 0)
    clear_hand(state, 0)
    deploy_champion(state, 0)
    exhaust_runes(state, 0)
    mine = place(state, 0, "OGN-013")            # 2 Might
    # Both battlefields are theirs, one guarded by something that kills the
    # Poro for free. Contesting the empty one is right; feeding the Drake is
    # not, and neither is doing nothing while they score.
    control(state, 0, controller=1)
    control(state, 1, controller=1)
    place(state, 1, "OGN-142", location="bf:1")
    return state, {"accept": [f"move:{mine}->bf:0"]}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("001", "conquer the open battlefield", 0,
             "190 -- an uncontrolled battlefield is free to contest, and the "
             "other one is guarded by a 10-Might unit that kills the mover "
             "for nothing.", _s001, ("objective",)),
    Scenario("002", "exhaust the rune, do not recycle it", 0,
             "164.2.a exhausts a rune for energy and readies again next turn; "
             "164.2.b recycles it off the board permanently. With energy the "
             "only thing needed, recycling spends a permanent for nothing.",
             _s002, ("resource",)),
    Scenario("003", "spend the removal while it has a target", 0,
             "Three enemy units stand at a battlefield and two of them die to "
             "3 damage. Holding the spell for a better turn is a real line in "
             "some games; here the board is already as good as it gets.",
             _s003, ("tempo", "removal")),
    Scenario("004", "contest the battlefield they are scoring", 0,
             "190.3 -- moving in contests it. Leaving an uncontested "
             "battlefield in their hands concedes a point every Beginning "
             "Phase (467).", _s004, ("objective",)),
    Scenario("005", "make the resource you cannot act without", 0,
             "The pool is empty and the only card in hand costs 2 energy. "
             "Passing the phase (305) forfeits the turn for nothing.",
             _s005, ("resource", "sequencing")),
    Scenario("006", "play the unit you can afford", 0,
             "4 energy in the pool and a 2-cost unit in hand, with an empty "
             "board. Declining develops nothing and the energy is lost at the "
             "next Main Phase (316.3).", _s006, ("tempo",)),
    Scenario("007", "aim the removal at the biggest thing it kills", 0,
             "417 -- 3 damage kills the 3-Might and the 2-Might unit and "
             "leaves the 10-Might one standing. Killing the bigger of the two "
             "reachable ones dominates the alternatives.",
             _s007, ("targeting",)),
    Scenario("008", "contest what you can, not what kills you", 0,
             "Both battlefields are theirs; one is empty and one is held by a "
             "10-Might unit. A 2-Might unit takes the empty one and dies for "
             "nothing at the other.", _s008, ("objective", "combat")),
)
