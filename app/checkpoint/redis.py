"""Redis-backed outer-workflow checkpoint support."""

from __future__ import annotations

from contextlib import contextmanager
import sys
from typing import Any, Iterator

from redis import Redis
from redis.exceptions import RedisError


class RedisCheckpointError(RuntimeError):
    """Base error for Redis checkpoint configuration and connectivity."""


class RedisCheckpointConfigurationError(RedisCheckpointError):
    """Raised when Redis cannot support the official LangGraph saver."""


def _module_names(client: Redis) -> set[str]:
    names: set[str] = set()
    for module in client.module_list():
        raw_name = module.get(b"name") or module.get("name")
        if isinstance(raw_name, bytes):
            raw_name = raw_name.decode("utf-8", errors="replace")
        if raw_name:
            names.add(str(raw_name).lower())
    return names


def validate_redis_checkpoint_server(client: Redis) -> str:
    """Ping Redis and verify modules required by the official RedisSaver."""

    try:
        client.ping()
        version = str(client.info("server").get("redis_version", "unknown"))
        major_version = int(version.split(".", maxsplit=1)[0])
        modules = _module_names(client)
    except (RedisError, ValueError, TypeError) as exc:
        raise RedisCheckpointError(
            "无法连接或识别 Redis Checkpoint 服务，请检查 TASKLOOM_REDIS_URL。"
        ) from exc

    if major_version < 8:
        has_json = bool({"rejson", "json"} & modules)
        has_search = bool({"search", "ft"} & modules)
        missing: list[str] = []
        if not has_json:
            missing.append("RedisJSON")
        if not has_search:
            missing.append("RediSearch")
        if missing:
            raise RedisCheckpointConfigurationError(
                f"Redis {version} 缺少 {', '.join(missing)}；"
                "官方 LangGraph RedisSaver 需要 Redis 8+ 或 Redis Stack。"
            )
    return version


@contextmanager
def open_redis_checkpointer(
    redis_url: str,
    *,
    ttl_minutes: int = 1440,
) -> Iterator[Any]:
    """Open, initialize, and close the official synchronous RedisSaver."""

    from langgraph.checkpoint.redis import RedisSaver

    probe = Redis.from_url(redis_url, decode_responses=False)
    try:
        validate_redis_checkpoint_server(probe)
    finally:
        probe.close()

    saver_context = RedisSaver.from_conn_string(
        redis_url,
        ttl={
            "default_ttl": ttl_minutes,
            "refresh_on_read": True,
        },
    )
    try:
        saver = saver_context.__enter__()
        saver.setup()
    except Exception as exc:
        saver_context.__exit__(*sys.exc_info())
        raise RedisCheckpointError(
            "Redis Checkpointer 初始化或读写失败，请检查 Redis 模块和连接配置。"
        ) from exc

    try:
        yield saver
    finally:
        saver_context.__exit__(None, None, None)
