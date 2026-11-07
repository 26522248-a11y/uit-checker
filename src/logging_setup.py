from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo


class RedactFilter(logging.Filter):
    """Best-effort redaction for secrets that might slip into log messages."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 4]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "***REDACTED***")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class _TZFormatter(logging.Formatter):
    def __init__(self, fmt: str, datefmt: str, tz: ZoneInfo):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._tz = tz

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, self._tz).strftime(
            datefmt or "%Y-%m-%d %H:%M:%S"
        )


def setup_logging(
    level: str,
    logs_dir: Path,
    secrets: list[str],
    tz_name: str = "Asia/Ho_Chi_Minh",
) -> None:
    # Windows consoles often default to cp1252; Vietnamese log messages need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    fmt = _TZFormatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        tz=ZoneInfo(tz_name),
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(RedactFilter(secrets))
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        logs_dir / "app.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactFilter(secrets))
    root.addHandler(file_handler)

    # These libraries can log raw URLs/headers at DEBUG; keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
