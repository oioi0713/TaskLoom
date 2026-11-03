"""Cycle detection for task dependency graphs."""

from app.planning.dependency_graph import DependencyGraph


def find_cycle(graph: DependencyGraph) -> list[str] | None:
    """Return one closed cycle path, or None when the graph is acyclic."""

    state: dict[str, int] = {task_id: 0 for task_id in graph}
    stack: list[str] = []
    stack_positions: dict[str, int] = {}

    def visit(task_id: str) -> list[str] | None:
        state[task_id] = 1
        stack_positions[task_id] = len(stack)
        stack.append(task_id)

        for dependency in sorted(graph[task_id]):
            if dependency not in graph:
                continue
            if state[dependency] == 0:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
            elif state[dependency] == 1:
                start = stack_positions[dependency]
                return [*stack[start:], dependency]

        stack.pop()
        stack_positions.pop(task_id)
        state[task_id] = 2
        return None

    for task_id in sorted(graph):
        if state[task_id] == 0:
            cycle = visit(task_id)
            if cycle is not None:
                return cycle
    return None


def has_cycle(graph: DependencyGraph) -> bool:
    """Return whether the dependency graph contains a cycle."""

    return find_cycle(graph) is not None
