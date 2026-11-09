"""Redis-backed Session Memory isolated from LangGraph checkpoint keys."""

from __future__ import annotations

from contextlib import contextmanager
import json
from dataclasses import dataclass
from typing import Any, Iterator

from pydantic import TypeAdapter, ValidationError
from redis import Redis
from redis.exceptions import RedisError

from app.contracts.memory import MemoryItem


class SessionMemoryError(RuntimeError):
    """Raised when Session Memory cannot be read or written safely."""


_MEMORY_ITEMS = TypeAdapter(list[MemoryItem])


@dataclass(slots=True)
class RedisSessionMemoryStore:
    """Store bounded semantic memory by session, independent of thread deletion."""

    client: Any
    ttl_hours: int = 72
    max_items: int = 50
    key_prefix: str = "taskloom:session-memory"

    @property
    def ttl_seconds(self) -> int:
        return self.ttl_hours * 60 * 60

    def _key(self, session_id: str) -> str:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must not be blank")
        return f"{self.key_prefix}:{normalized}"

    def load(self, session_id: str) -> list[MemoryItem]:
        """Load memory and refresh only the memory key's sliding TTL."""

        key = self._key(session_id)
        try:
            raw = self.client.get(key)
        except RedisError as exc:
            raise SessionMemoryError("读取 Redis Session Memory 失败。") from exc
        if raw is None:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            items = _MEMORY_ITEMS.validate_python(json.loads(str(raw)))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise SessionMemoryError(
                f"session_id={session_id} 的 Redis Memory 数据无效。"
            ) from exc
        try:
            self.client.expire(key, self.ttl_seconds)
        except RedisError as exc:
            raise SessionMemoryError("刷新 Session Memory TTL 失败。") from exc
        return items

    def save(self, session_id: str, items: list[MemoryItem]) -> None:
        """Replace one session's bounded semantic memory with a fresh snapshot."""

        key = self._key(session_id)
        bounded = items[-self.max_items :]
        payload = json.dumps(
            [item.model_dump(mode="json") for item in bounded],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self.client.set(key, payload, ex=self.ttl_seconds)
        except RedisError as exc:
            raise SessionMemoryError("写入 Redis Session Memory 失败。") from exc

    def merge(self, session_id: str, items: list[MemoryItem]) -> list[MemoryItem]:
        """Idempotently merge records by memory_id and persist the bounded result."""

        existing = self.load(session_id)
        merged = {item.memory_id: item for item in existing}
        for item in items:
            merged[item.memory_id] = item
        result = list(merged.values())[-self.max_items :]
        self.save(session_id, result)
        return result

    def delete(self, session_id: str) -> None:
        """Delete memory only when the caller explicitly deletes the whole session."""

        try:
            self.client.delete(self._key(session_id))
        except RedisError as exc:
            raise SessionMemoryError("删除 Redis Session Memory 失败。") from exc


@contextmanager
def open_redis_session_memory_store(
    redis_url: str,
    *,
    ttl_hours: int = 72,
    max_items: int = 50,
) -> Iterator[RedisSessionMemoryStore]:
    """Open a Redis Session Memory store without sharing checkpoint key cleanup."""

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except RedisError as exc:
        raise SessionMemoryError("无法连接或读写 Redis Session Memory。") from exc
    try:
        yield RedisSessionMemoryStore(
            client=client,
            ttl_hours=ttl_hours,
            max_items=max_items,
        )
    finally:
        client.close()
