from __future__ import annotations

import pytest

from app.checkpoint.session import RedisSessionRegistry, SessionCheckpointError


class FakePipeline:
    def __init__(self, client: "FakeRedis") -> None:
        self.client = client
        self.commands: list[tuple] = []

    def getset(self, key: str, value: str) -> "FakePipeline":
        self.commands.append(("getset", key, value))
        return self

    def expire(self, key: str, seconds: int) -> "FakePipeline":
        self.commands.append(("expire", key, seconds))
        return self

    def sadd(self, key: str, value: str) -> "FakePipeline":
        self.commands.append(("sadd", key, value))
        return self

    def execute(self) -> list:
        results = []
        for command in self.commands:
            if command[0] == "getset":
                _, key, value = command
                old = self.client.values.get(key)
                self.client.values[key] = value
                results.append(old)
            elif command[0] == "expire":
                _, key, seconds = command
                self.client.ttls[key] = seconds
                results.append(True)
            else:
                _, key, value = command
                self.client.sets.setdefault(key, set()).add(value)
                results.append(1)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def delete(self, key: str) -> int:
        existed = key in self.sets or key in self.values
        self.sets.pop(key, None)
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


def test_new_thread_replaces_and_deletes_previous_session_thread() -> None:
    redis = FakeRedis()
    checkpointer = FakeCheckpointer()
    registry = RedisSessionRegistry(redis, checkpointer, ttl_minutes=1440)

    assert registry.activate("chat-1", "thread-1") is None
    assert registry.activate("chat-1", "thread-2") == "thread-1"

    assert registry.resolve_latest("chat-1") == "thread-2"
    assert checkpointer.deleted_threads == ["thread-1"]
    key = "taskloom:session:chat-1:latest_thread_id"
    assert redis.ttls[key] == 86400


def test_replacing_parent_thread_deletes_registered_worker_checkpoints() -> None:
    redis = FakeRedis()
    checkpointer = FakeCheckpointer()
    registry = RedisSessionRegistry(redis, checkpointer)

    registry.activate("chat-1", "outer-1")
    registry.register_child_thread("outer-1", "outer-1:worker:a")
    registry.register_child_thread("outer-1", "outer-1:worker:b")
    registry.activate("chat-1", "outer-2")

    assert set(checkpointer.deleted_threads[:-1]) == {
        "outer-1:worker:a",
        "outer-1:worker:b",
    }
    assert checkpointer.deleted_threads[-1] == "outer-1"
    assert not redis.smembers("taskloom:checkpoint-children:outer-1")


def test_different_sessions_keep_independent_latest_threads() -> None:
    redis = FakeRedis()
    checkpointer = FakeCheckpointer()
    registry = RedisSessionRegistry(redis, checkpointer)

    registry.activate("chat-a", "thread-a")
    registry.activate("chat-b", "thread-b")

    assert registry.resolve_latest("chat-a") == "thread-a"
    assert registry.resolve_latest("chat-b") == "thread-b"
    assert checkpointer.deleted_threads == []


def test_superseded_thread_cannot_resume_through_session() -> None:
    registry = RedisSessionRegistry(FakeRedis(), FakeCheckpointer())
    registry.activate("chat-1", "thread-new")

    with pytest.raises(SessionCheckpointError, match="已不是"):
        registry.require_latest("chat-1", "thread-old")

    registry.require_latest("chat-1", "thread-new")
