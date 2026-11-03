import copy
import json
from pathlib import Path

import pytest

from app.planning.validator import PlanGuard


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "valid_plan.json"


@pytest.fixture
def plan_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def issue_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_plan_is_accepted(plan_data: dict) -> None:
    outcome = PlanGuard().evaluate(plan_data)

    assert outcome.action == "accept"
    assert outcome.executable is True
    assert outcome.plan is not None


def test_dependency_cycle_is_rejected(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["tasks"][0]["dependencies"] = ["write_report"]

    report = PlanGuard().validate(plan)

    assert "dependency_cycle" in issue_codes(report)
    assert "no_ready_task" in issue_codes(report)


def test_missing_dependency_is_rejected(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["tasks"][1]["dependencies"] = ["missing_task"]

    report = PlanGuard().validate(plan)

    assert "missing_dependency" in issue_codes(report)


def test_duplicate_agent_id_is_reported(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["agents"][1]["agent_id"] = plan["agents"][0]["agent_id"]

    report = PlanGuard().validate(plan)

    assert "duplicate_agent_id" in issue_codes(report)


def test_high_risk_write_tool_is_rejected(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["agents"][0]["requested_tools"] = ["file_writer"]

    report = PlanGuard().validate(plan)

    assert "high_risk_tool_requested" in issue_codes(report)
    assert "unknown_tool" in issue_codes(report)


def test_worker_count_cannot_exceed_budget(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["budget"]["max_workers"] = 1
    plan["budget"]["max_concurrency"] = 1

    report = PlanGuard().validate(plan)

    assert "worker_count_exceeded" in issue_codes(report)


def test_worker_count_cannot_exceed_system_limit(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    for index in range(7):
        agent = copy.deepcopy(plan["agents"][0])
        agent["agent_id"] = f"extra_agent_{index}"
        plan["agents"].append(agent)

    report = PlanGuard().validate(plan)

    assert "too_many_workers" in issue_codes(report)


def test_worker_delegation_is_rejected(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["agents"][0]["can_delegate"] = True

    report = PlanGuard().validate(plan)

    assert "delegation_not_allowed" in issue_codes(report)


def test_missing_task_agent_mapping_is_rejected(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["tasks"][0]["assigned_agent_id"] = "missing_agent"

    report = PlanGuard().validate(plan)

    assert "missing_assigned_agent" in issue_codes(report)


def test_task_and_agent_output_contracts_must_match(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["agents"][0]["output_contract"] = "report_result_v1"

    report = PlanGuard().validate(plan)

    assert "output_contract_mismatch" in issue_codes(report)


def test_first_invalid_attempt_returns_specific_revision_feedback(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["tasks"][1]["dependencies"] = ["missing_task"]

    outcome = PlanGuard().evaluate(plan, invalid_attempt=1)

    assert outcome.action == "revise"
    assert outcome.executable is False
    assert "missing_dependency" in issue_codes(outcome)
    assert any("missing_task" in message for message in outcome.feedback)


def test_second_structurally_invalid_attempt_terminates(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["tasks"][0]["dependencies"] = ["write_report"]

    outcome = PlanGuard().evaluate(plan, invalid_attempt=2)

    assert outcome.action == "terminate"
    assert outcome.executable is False
    assert "dependency_cycle" in issue_codes(outcome)


def test_second_permission_invalid_attempt_is_safely_degraded(plan_data: dict) -> None:
    plan = copy.deepcopy(plan_data)
    plan["agents"][0]["requested_tools"] = ["web_search", "file_writer"]
    plan["agents"][0]["can_delegate"] = True
    plan["agents"][0]["timeout_seconds"] = 999
    plan["agents"][0]["max_steps"] = 99
    plan["agents"][0]["token_budget"] = 99999

    outcome = PlanGuard().evaluate(plan, invalid_attempt=2)

    assert outcome.action == "degrade"
    assert outcome.executable is True
    assert outcome.plan is not None
    agent = outcome.plan.agents[0]
    assert agent.requested_tools == ["web_search"]
    assert agent.can_delegate is False
    assert agent.timeout_seconds == 120
    assert agent.max_steps == 8
    assert agent.token_budget == 8000


def test_invalid_json_is_rejected() -> None:
    report = PlanGuard().validate("{not valid json")

    assert report.valid is False
    assert issue_codes(report) == {"invalid_json"}
