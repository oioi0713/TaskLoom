import json
import logging

from app.config import Settings
from app.logging import JsonFormatter, configure_logging


def test_configure_logging_sets_requested_root_level() -> None:
    settings = Settings(_env_file=None, log_level="DEBUG")

    configure_logging(settings)

    assert logging.getLogger().level == logging.DEBUG


def test_json_formatter_emits_structured_record() -> None:
    record = logging.LogRecord(
        name="taskloom.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ready",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "taskloom.test"
    assert payload["message"] == "ready"
    assert "timestamp" in payload
