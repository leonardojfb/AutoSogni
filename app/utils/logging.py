from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.utils.paths import logs_dir


def configure_logging() -> None:
    log_path = logs_dir() / "autosogni.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
