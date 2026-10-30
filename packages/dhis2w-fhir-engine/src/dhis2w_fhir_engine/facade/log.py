"""The logger every module in this package writes through, and the configuration a server process runs it under."""

from __future__ import annotations

import logging
import sys

#: The logger every module in this package writes through.
LOGGER_NAME = "dhis2w_fhir_engine"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

logger = logging.getLogger(LOGGER_NAME)


def configure_logging(level: int = logging.INFO) -> None:
    """Send this package's log to stderr under a plain formatter, leaving the root logger alone."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
