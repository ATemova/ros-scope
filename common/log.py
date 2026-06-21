"""Tiny logging setup shared by every service.

Gives each service a named, timestamped logger instead of bare prints, with
the level controlled by the LOG_LEVEL env var. One line to adopt:

    from common.log import get_logger
    log = get_logger("ingest")
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        _CONFIGURED = True
    return logging.getLogger(name)
