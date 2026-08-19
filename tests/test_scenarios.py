"""`TCG_AI_BUILD.md` section 27 -- fixed benchmark scenarios.

The plan asks for a set of hand-built positions with a known right answer, so
that any agent or checkpoint can be graded on decisions that matter rather
than only on win rate. A win rate says an agent is better; a scenario suite
says *what* it got wrong.

Two things are asserted about the suite itself, before anything is asserted
about an agent:

* **Every scenario is a legal position.** A hand-built state can encode
  something the rules cannot reach, and an agent graded on an impossible
  position is being graded on nothing. `engine.invariants.assert_ok` runs on
  every one.
* **Every scenario is answerable.** The accepted actions must be legal in the
  position, or the scenario is unpassable and its failures mean nothing.
"""

from __future__ import annotations

import pytest

from agents.random_agent import RandomAgent
from agents.greedy_agent import GreedyAgent
from agents.styles import AggroAgent, ConservativeAgent, ObjectiveAgent
from analysis.scenarios import SCENARIOS, grade, run_scenario
from engine import invariants


def test_the_suite_is_not_empty():
    assert len(SCENARIOS) >= 8
    assert len({s.scenario_id for s in SCENARIOS}) == len(SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.scenario_id)
def test_every_scenario_is_a_legal_position(scenario):
    state = scenario.build()
    invariants.assert_ok(state, f"scenario {scenario.scenario_id}")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.scenario_id)
def test_every_scenario_is_answerable(scenario):
    """The right answers must actually be on the menu."""
    state = scenario.build()
    legal = {repr(a) for a in state.legal_actions()}
    assert legal, "no legal actions in a scenario position"
    accepted = scenario.accepted(state)
    assert accepted, f"{scenario.scenario_id} accepts nothing"
    assert accepted <= legal, (
        f"{scenario.scenario_id} accepts {sorted(accepted - legal)}, which is "
        f"not legal here"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.scenario_id)
def test_every_scenario_can_be_failed(scenario):
    """A scenario every action passes measures nothing."""
    state, accepted = scenario.position()
    legal = {repr(a) for a in state.legal_actions()}
    assert len(legal - accepted) > 0, (
        f"{scenario.scenario_id} accepts every legal action"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.scenario_id)
def test_the_acting_player_is_the_one_being_tested(scenario):
    state = scenario.build()
    assert state.current_player == scenario.player


def test_grading_an_agent_returns_one_row_per_scenario():
    report = grade(GreedyAgent(0), SCENARIOS)
    assert len(report.rows) == len(SCENARIOS)
    assert 0.0 <= report.score <= 1.0
    for row in report.rows:
        assert row.chosen
        assert isinstance(row.passed, bool)


def test_a_random_agent_does_not_ace_the_suite():
    """A suite Random passes is not measuring skill.

    Not a strict inequality against Greedy -- that would be a claim about the
    evaluator, and this file is about the scenarios. It is a floor: if random
    play scores near the top, the scenarios are too easy to be worth running.
    """
    random_score = grade(RandomAgent(0), SCENARIOS).score
    assert random_score < 0.9, (
        f"RandomAgent scored {random_score:.2f} -- the suite is too easy"
    )


def test_a_scenario_is_reproducible():
    """Each `position()` is a fresh state built the same way every time."""
    scenario = SCENARIOS[0]
    first = run_scenario(GreedyAgent(0), scenario)
    second = run_scenario(GreedyAgent(0), scenario)
    assert first.chosen == second.chosen
    assert first.passed == second.passed


def test_every_style_agent_can_be_graded():
    """The suite is the thing section 27 grades checkpoints with, so it has to

    accept any agent, not just the one it was written against.
    """
    for agent in (AggroAgent(1), ConservativeAgent(1), ObjectiveAgent(1)):
        report = grade(agent, SCENARIOS)
        assert len(report.rows) == len(SCENARIOS)
