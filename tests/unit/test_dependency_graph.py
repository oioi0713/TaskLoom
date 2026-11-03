from app.contracts.task import TaskSpec
from app.planning.cycle_detector import find_cycle, has_cycle
from app.planning.dependency_graph import (
    build_dependency_graph,
    find_missing_dependencies,
    find_ready_task_ids,
)


def make_task(
    task_id: str,
    *,
    dependencies: list[str] | None = None,
    priority: int = 0,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title=task_id,
        description=f"Execute {task_id}",
        task_type="test",
        dependencies=dependencies or [],
        priority=priority,
        assigned_agent_id=f"agent_{task_id}",
        output_contract="research_result_v1",
    )


def test_dependency_graph_finds_missing_references() -> None:
    graph = build_dependency_graph(
        [
            make_task("task_a"),
            make_task("task_b", dependencies=["task_a", "missing_task"]),
        ]
    )

    assert find_missing_dependencies(graph) == {"task_b": ["missing_task"]}


def test_cycle_detector_returns_closed_cycle_path() -> None:
    graph = build_dependency_graph(
        [
            make_task("task_a", dependencies=["task_b"]),
            make_task("task_b", dependencies=["task_a"]),
        ]
    )

    cycle = find_cycle(graph)

    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {"task_a", "task_b"}
    assert has_cycle(graph) is True


def test_ready_tasks_are_sorted_by_priority_then_id() -> None:
    tasks = [
        make_task("task_low", priority=1),
        make_task("task_high_b", priority=10),
        make_task("task_high_a", priority=10),
        make_task("task_waiting", dependencies=["task_low"], priority=100),
    ]

    assert find_ready_task_ids(tasks) == ["task_high_a", "task_high_b", "task_low"]
