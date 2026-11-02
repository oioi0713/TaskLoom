import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.plan import ExecutionPlan


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "valid_plan.json"


@pytest.fixture
def valid_plan_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_complete_handwritten_plan_json_is_valid() -> None:
    plan = ExecutionPlan.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert plan.goal_summary.startswith("调研")
    assert len(plan.tasks) == 2
    assert len(plan.agents) == 2
    assert plan.agents[0].requested_tools == ["web_search", "web_page_reader"]


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        (
            lambda plan: plan["agents"][0].update(
                {"required_capabilities": ["invented_capability"]}
            ),
            "Unknown capabilities",
        ),
        (
            lambda plan: plan["tasks"][0].update(
                {"output_contract": "unknown_result_v1"}
            ),
            "Input should be",
        ),
        (
            lambda plan: plan["agents"][0].update({"can_delegate": True}),
            "Input should be False",
        ),
        (
            lambda plan: plan["agents"][0].update({"timeout_seconds": 121}),
            "less than or equal to 120",
        ),
        (
            lambda plan: plan["agents"][0].update(
                {"requested_tools": ["nonexistent_tool"]}
            ),
            "Unknown requested tools",
        ),
        (
            lambda plan: plan["agents"][0].update(
                {"requested_tools": ["dataset_reader"]}
            ),
            "not allowed by the selected capabilities",
        ),
    ],
)
def test_invalid_plan_fields_are_rejected(
    valid_plan_data: dict,
    mutation,
    expected_message: str,
) -> None:
    plan = copy.deepcopy(valid_plan_data)
    mutation(plan)

    with pytest.raises(ValidationError, match=expected_message):
        ExecutionPlan.model_validate(plan)


def test_duplicate_task_id_is_rejected(valid_plan_data: dict) -> None:
    plan = copy.deepcopy(valid_plan_data)
    plan["tasks"][0]["task_id"] = plan["tasks"][1]["task_id"]

    with pytest.raises(ValidationError, match="Duplicate task_id"):
        ExecutionPlan.model_validate(plan)


def test_duplicate_agent_id_is_rejected(valid_plan_data: dict) -> None:
    plan = copy.deepcopy(valid_plan_data)
    plan["agents"][1]["agent_id"] = plan["agents"][0]["agent_id"]

    with pytest.raises(ValidationError, match="Duplicate agent_id"):
        ExecutionPlan.model_validate(plan)
