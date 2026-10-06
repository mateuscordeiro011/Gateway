import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from app.core.config import get_settings


class SensitiveDataFilter(logging.Filter):
    """Filter to remove sensitive data from logs."""

    SENSITIVE_KEYS = {
        "access_token", "client_secret", "app_secret", "verify_token",
        "api_key", "authorization", "password", "secret", "token",
        "x-hub-signature-256", "x-api-key"
    }

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, dict):
            record.msg = self._sanitize_dict(record.msg)
        elif hasattr(record, "args") and record.args:
            record.args = tuple(
                self._sanitize_dict(arg) if isinstance(arg, dict) else arg
                for arg in record.args
            )
        return True

    def _sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in self.SENSITIVE_KEYS):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_dict(value)
            else:
                sanitized[key] = value
        return sanitized


def setup_logging() -> None:
    """Configure structured JSON logging."""
    settings = get_settings()

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Create JSON formatter
    formatter = jsonlogger.JsonFormatter(
        fmt="%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={
            "timestamp": "timestamp",
            "level": "level",
            "name": "logger",
            "message": "message",
        },
        timestamp=True,
    )

    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveDataFilter())

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [handler]

    # Reduce noise from third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Ensure uvicorn uses our formatter
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(logger_name)
        logger.handlers = [handler]
        logger.setLevel(log_level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
