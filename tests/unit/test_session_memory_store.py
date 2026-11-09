from __future__ import annotations

from app.contracts.memory import MemoryItem
from app.checkpoint.session import RedisSessionRegistry
from app.memory.store import RedisSessionMemoryStore


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

    def execute(self) -> list[object]:
        results: list[object] = []
        for command in self.commands:
            if command[0] == "getset":
                _, key, value = command
                results.append(self.client.values.get(key))
                self.client.values[key] = value
            else:
                _, key, seconds = command
                self.client.ttls[key] = seconds
                results.append(True)
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.deleted: list[str] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        self.values.pop(key, None)
        return 1


def item(memory_id: str, content: str) -> MemoryItem:
    return MemoryItem(memory_id=memory_id, content=content)


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


def test_memory_uses_independent_key_and_72_hour_sliding_ttl() -> None:
    redis = FakeRedis()
    store = RedisSessionMemoryStore(redis)

    store.save("chat-1", [item("m1", "Redis memory")])

    key = "taskloom:session-memory:chat-1"
    assert redis.ttls[key] == 72 * 60 * 60
    loaded = store.load("chat-1")
    assert [value.content for value in loaded] == ["Redis memory"]
    assert redis.ttls[key] == 72 * 60 * 60


def test_merge_is_idempotent_and_bounded() -> None:
    redis = FakeRedis()
    store = RedisSessionMemoryStore(redis, max_items=2)

    store.save("chat-1", [item("m1", "old")])
    merged = store.merge(
        "chat-1",
        [item("m1", "updated"), item("m2", "second"), item("m3", "third")],
    )

    assert [value.memory_id for value in merged] == ["m2", "m3"]
    assert [value.memory_id for value in store.load("chat-1")] == ["m2", "m3"]


def test_memory_is_deleted_only_by_explicit_memory_delete() -> None:
    redis = FakeRedis()
    store = RedisSessionMemoryStore(redis)
    store.save("chat-1", [item("m1", "keep me")])

    assert store.load("chat-1")
    store.delete("chat-1")

    assert redis.deleted == ["taskloom:session-memory:chat-1"]
    assert store.load("chat-1") == []


def test_replacing_latest_thread_does_not_delete_session_memory() -> None:
    redis = FakeRedis()
    checkpointer = FakeCheckpointer()
    registry = RedisSessionRegistry(redis, checkpointer)
    store = RedisSessionMemoryStore(redis)
    store.save("chat-1", [item("m1", "survives thread replacement")])

    registry.activate("chat-1", "thread-old")
    registry.activate("chat-1", "thread-new")

    assert checkpointer.deleted_threads == ["thread-old"]
    assert [value.memory_id for value in store.load("chat-1")] == ["m1"]
