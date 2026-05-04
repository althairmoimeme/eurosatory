from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from app.config import settings


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> <cyan>{name}</cyan> | {message}",
    )
    log_path = Path(settings.log_dir) / "eurosatory.log"
    logger.add(
        log_path,
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        enqueue=True,
        encoding="utf-8",
    )
