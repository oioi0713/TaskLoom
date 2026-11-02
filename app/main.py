"""TaskLoom command-line entry point."""

import logging

from app.config import get_settings
from app.logging import configure_logging


def main() -> int:
    """Load configuration, initialize logging, and start the application skeleton."""

    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)
    logger.info(
        "%s initialized (environment=%s, debug=%s)",
        settings.app_name,
        settings.environment,
        settings.debug,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
