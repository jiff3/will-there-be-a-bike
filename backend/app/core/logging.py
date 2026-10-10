from __future__ import annotations

import logging
from logging.config import dictConfig

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "format": "%(_timestamp)s %(levelname)s %(name)s %(message)s",
                    "rename_fields": {
                        "_timestamp": "timestamp",
                        "levelname": "level",
                        "name": "logger",
                    },
                    "timestamp": True,
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["default"], "level": settings.app_log_level},
            "loggers": {
                # RequestContextMiddleware owns access logs with route templates;
                # uvicorn's raw request target could expose sensitive query strings.
                "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": settings.app_log_level,
                    "propagate": False,
                },
            },
        }
    )
    logging.getLogger(__name__).info("logging_configured", extra={"environment": settings.app_env})
