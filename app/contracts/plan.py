"""Complete execution-plan envelope used for stage-one validation."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.budget import ExecutionBudget
from app.contracts.subagent import SubAgentSpec
from app.contracts.task import TaskSpec
from app.contracts.types import NonEmptyStr


class ExecutionPlan(BaseModel):
    """A planner output with schema-level and registry-level guarantees.

    Dependency existence, cycle detection, and repair behavior intentionally belong
    to the stage-two Plan Guard rather than this stage-one data contract.
    """

    model_config = ConfigDict(extra="forbid")

    goal_summary: NonEmptyStr
    assumptions: list[NonEmptyStr] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=8)
    agents: list[SubAgentSpec] = Field(min_length=1, max_length=8)
    expected_deliverable: NonEmptyStr
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @model_validator(mode="after")
    def ids_must_be_unique(self) -> "ExecutionPlan":
        task_ids = [task.task_id for task in self.tasks]
        duplicate_tasks = sorted(
            task_id for task_id in set(task_ids) if task_ids.count(task_id) > 1
        )
        if duplicate_tasks:
            raise ValueError(f"Duplicate task_id values: {', '.join(duplicate_tasks)}")

        agent_ids = [agent.agent_id for agent in self.agents]
        duplicate_agents = sorted(
            agent_id for agent_id in set(agent_ids) if agent_ids.count(agent_id) > 1
        )
        if duplicate_agents:
            raise ValueError(f"Duplicate agent_id values: {', '.join(duplicate_agents)}")
        return self
