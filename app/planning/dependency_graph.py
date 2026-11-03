"""Dependency-graph helpers used by the deterministic Plan Guard."""

from collections.abc import Iterable

from app.contracts.task import TaskSpec

DependencyGraph = dict[str, frozenset[str]]


def build_dependency_graph(tasks: Iterable[TaskSpec]) -> DependencyGraph:
    """Build a task-to-dependencies adjacency mapping."""

    return {task.task_id: frozenset(task.dependencies) for task in tasks}


def find_missing_dependencies(graph: DependencyGraph) -> dict[str, list[str]]:
    """Return missing dependency IDs grouped by the task that references them."""

    task_ids = set(graph)
    return {
        task_id: sorted(set(dependencies) - task_ids)
        for task_id, dependencies in graph.items()
        if set(dependencies) - task_ids
    }


def find_ready_task_ids(tasks: Iterable[TaskSpec]) -> list[str]:
    """Return initially runnable tasks in deterministic priority order."""

    ready = [
        task
        for task in tasks
        if not task.dependencies and task.status in {"pending", "ready"}
    ]
    return [
        task.task_id
        for task in sorted(ready, key=lambda item: (-item.priority, item.task_id))
    ]
