"""Redis mapping from a front-end session to its latest workflow thread."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from redis import Redis


class SessionCheckpointError(RuntimeError):
    """Raised when a session points at a different or missing workflow thread."""


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass(slots=True)
class RedisSessionRegistry:
    """Keep only the latest recoverable thread for each front-end session."""

    client: Any
    checkpointer: Any
    ttl_minutes: int = 1440
    key_prefix: str = "taskloom:session"
    child_key_prefix: str = "taskloom:checkpoint-children"

    @property
    def ttl_seconds(self) -> int:
        return self.ttl_minutes * 60

    def _key(self, session_id: str) -> str:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must not be blank")
        return f"{self.key_prefix}:{normalized}:latest_thread_id"

    def activate(self, session_id: str, thread_id: str) -> str | None:
        """Atomically point a session at a durable new thread and delete the old one."""

        normalized_thread_id = thread_id.strip()
        if not normalized_thread_id:
            raise ValueError("thread_id must not be blank")
        key = self._key(session_id)
        pipeline = self.client.pipeline(transaction=True)
        pipeline.getset(key, normalized_thread_id)
        pipeline.expire(key, self.ttl_seconds)
        old_value, _ = pipeline.execute()
        old_thread_id = _decode(old_value)
        if old_thread_id and old_thread_id != normalized_thread_id:
            self.delete_thread_tree(old_thread_id)
        return old_thread_id

    def register_child_thread(self, parent_thread_id: str, child_thread_id: str) -> None:
        """Track Worker checkpoint threads so parent replacement cleans them too."""

        key = f"{self.child_key_prefix}:{parent_thread_id}"
        pipeline = self.client.pipeline(transaction=True)
        pipeline.sadd(key, child_thread_id)
        pipeline.expire(key, self.ttl_seconds)
        pipeline.execute()

    def delete_thread_tree(self, parent_thread_id: str) -> None:
        """Delete one outer thread and every registered Worker checkpoint thread."""

        key = f"{self.child_key_prefix}:{parent_thread_id}"
        raw_children = self.client.smembers(key)
        for raw_child in raw_children:
            child_thread_id = _decode(raw_child)
            if child_thread_id:
                self.checkpointer.delete_thread(child_thread_id)
        self.checkpointer.delete_thread(parent_thread_id)
        self.client.delete(key)

    def resolve_latest(self, session_id: str) -> str | None:
        """Return and refresh the latest thread pointer for one session."""

        key = self._key(session_id)
        thread_id = _decode(self.client.get(key))
        if thread_id is not None:
            self.client.expire(key, self.ttl_seconds)
        return thread_id

    def require_latest(self, session_id: str, thread_id: str) -> None:
        """Reject attempts to resume a superseded thread through a session."""

        latest = self.resolve_latest(session_id)
        if latest is None:
            raise SessionCheckpointError(
                f"session_id={session_id} 没有可恢复的最新 Thread。"
            )
        if latest != thread_id:
            raise SessionCheckpointError(
                f"thread_id={thread_id} 已不是 session_id={session_id} 的最新 Thread。"
            )


@contextmanager
def open_redis_session_registry(
    redis_url: str,
    *,
    checkpointer: Any,
    ttl_minutes: int = 1440,
) -> Iterator[RedisSessionRegistry]:
    """Open a session registry that shares the checkpoint Redis database."""

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
        yield RedisSessionRegistry(
            client=client,
            checkpointer=checkpointer,
            ttl_minutes=ttl_minutes,
        )
    finally:
        client.close()
