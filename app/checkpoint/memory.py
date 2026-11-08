"""In-process checkpoint support for the TaskLoom runtime graph.

The memory backend is intentionally process-local.  It is useful for proving
pause/resume semantics before a durable Redis backend is introduced, but its
contents disappear when the Python process exits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START

from app.graph.state import Stage6State
from app.graph.rehydration import rehydrate_stage6_state


def create_memory_checkpointer() -> InMemorySaver:
    """Create an isolated, process-local LangGraph checkpoint store."""

    return InMemorySaver()


@dataclass(slots=True)
class CheckpointSession:
    """Operate one workflow thread through any LangGraph checkpointer."""

    graph: Any
    thread_id: str
    recursion_limit: int = 100

    @property
    def config(self) -> dict[str, Any]:
        """Return the stable LangGraph config required by the checkpointer."""

        return {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": self.recursion_limit,
        }

    def start(self, initial_state: Stage6State) -> Stage6State:
        """Start a new thread and run until completion or a graph interrupt."""

        state_thread_id = initial_state.get("thread_id")
        if state_thread_id != self.thread_id:
            raise ValueError(
                "Initial state's thread_id must match the checkpoint session: "
                f"{state_thread_id!r} != {self.thread_id!r}"
            )
        result = self.graph.invoke(initial_state, config=self.config)
        return rehydrate_stage6_state(result)

    def initialize(self, initial_state: Stage6State) -> Stage6State:
        """Persist a new thread before running its first real graph node."""

        state_thread_id = initial_state.get("thread_id")
        if state_thread_id != self.thread_id:
            raise ValueError(
                "Initial state's thread_id must match the checkpoint session: "
                f"{state_thread_id!r} != {self.thread_id!r}"
            )
        self.graph.update_state(
            self.config,
            initial_state,
            as_node=START,
        )
        return rehydrate_stage6_state(self.snapshot().values)

    def resume(self) -> Stage6State:
        """Resume from the latest checkpoint without replaying completed nodes."""

        result = self.graph.invoke(None, config=self.config)
        return rehydrate_stage6_state(result)

    def snapshot(self) -> Any:
        """Return the latest LangGraph state snapshot for inspection."""

        return self.graph.get_state(self.config)

    def history(self) -> list[Any]:
        """Return newest-first checkpoint history for this thread."""

        return list(self.graph.get_state_history(self.config))

    @property
    def is_paused(self) -> bool:
        """Whether the checkpoint has one or more nodes waiting to run."""

        return bool(self.snapshot().next)


# Backwards-compatible name retained for the stage-nine memory tests and API.
MemoryCheckpointSession = CheckpointSession
