"""Select a checkpoint backend from TaskLoom runtime settings."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Literal

from app.checkpoint.memory import create_memory_checkpointer
from app.checkpoint.redis import (
    RedisCheckpointConfigurationError,
    open_redis_checkpointer,
)
from app.config import Settings


CheckpointBackend = Literal["memory", "redis"]


@contextmanager
def open_checkpoint_saver(
    settings: Settings,
    *,
    backend: CheckpointBackend | None = None,
) -> Iterator[tuple[CheckpointBackend, Any]]:
    """Yield a configured saver and close external resources afterwards."""

    selected = backend or settings.checkpoint_backend
    if selected == "memory":
        yield selected, create_memory_checkpointer()
        return

    if settings.redis_url is None:
        raise RedisCheckpointConfigurationError(
            "checkpoint_backend=redis 时必须配置 TASKLOOM_REDIS_URL。"
        )
    redis_url = settings.redis_url.get_secret_value()
    with open_redis_checkpointer(
        redis_url,
        ttl_minutes=settings.checkpoint_ttl_minutes,
    ) as saver:
        yield selected, saver
