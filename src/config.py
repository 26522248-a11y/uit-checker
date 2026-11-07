from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_opt_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


def _env_time(name: str, default: str) -> time:
    raw = (os.getenv(name) or default).strip()
    hh, mm = raw.split(":", 1)
    return time(int(hh), int(mm))


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


@dataclass(slots=True)
class Config:
    # Account
    uit_username: str
    uit_password: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_message_thread_id: int | None = None

    # Polling
    announcement_poll_seconds: int = 120
    tkb_poll_seconds: int = 900
    exam_poll_seconds: int = 1800

    # Digest
    digest_cron: str = "0 6 * * *"
    digest_send_empty: bool = True
    digest_catchup_until: time = field(default_factory=lambda: time(12, 0))

    # Announcements
    announcement_mode: str = "relevant"
    announcement_page_size: int = 50
    announcement_max_pages: int = 5

    # Auth
    auth_relogin_margin_minutes: int = 15
    auth_max_failures: int = 3
    auth_alert_cooldown_minutes: int = 60

    # TKB / Exams
    tkb_poll_next_week: bool = True
    tkb_confirm_removals: bool = True
    exam_years: str = "auto"
    exam_semesters: str = "all"

    # Courses UIT (Moodle)
    courses_enabled: bool = True
    courses_base_url: str = "https://courses.uit.edu.vn"
    courses_poll_seconds: int = 120
    courses_notification_limit: int = 50
    courses_include_messages: bool = True
    courses_session_refresh_margin_minutes: int = 15

    # Runtime
    tz_name: str = "Asia/Ho_Chi_Minh"
    log_level: str = "INFO"
    state_db_path: Path = field(default_factory=lambda: BASE_DIR / "data" / "state.db")
    dry_run: bool = False
    heartbeat_enabled: bool = False
    startup_message: bool = True

    # Fallback metadata
    year_id: int | None = None
    nam_hoc: int | None = None
    hoc_ky: int | None = None

    # Tuning
    confirm_removal_delay_seconds: int = 45
    portal_failure_alert_threshold: int = 6

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def data_dir(self) -> Path:
        return self.state_db_path.parent

    @property
    def logs_dir(self) -> Path:
        return BASE_DIR / "logs"

    @classmethod
    def load(cls) -> "Config":
        username = os.getenv("UIT_USERNAME", "").strip()
        password = os.getenv("UIT_PASSWORD", "")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        cand = cls(
            uit_username=username,
            uit_password=password,
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            telegram_message_thread_id=_env_opt_int("TELEGRAM_MESSAGE_THREAD_ID"),
            announcement_poll_seconds=max(60, _env_int("ANNOUNCEMENT_POLL_SECONDS", 120)),
            tkb_poll_seconds=max(120, _env_int("TKB_POLL_SECONDS", 900)),
            exam_poll_seconds=max(300, _env_int("EXAM_POLL_SECONDS", 1800)),
            digest_cron=os.getenv("DIGEST_CRON", "0 6 * * *").strip(),
            digest_send_empty=_env_bool("DIGEST_SEND_EMPTY", True),
            digest_catchup_until=_env_time("DIGEST_CATCHUP_UNTIL", "12:00"),
            announcement_mode=(os.getenv("ANNOUNCEMENT_MODE", "relevant").strip().lower()),
            announcement_page_size=min(100, max(1, _env_int("ANNOUNCEMENT_PAGE_SIZE", 50))),
            announcement_max_pages=max(1, _env_int("ANNOUNCEMENT_MAX_PAGES", 5)),
            auth_relogin_margin_minutes=max(1, _env_int("AUTH_RELOGIN_MARGIN_MINUTES", 15)),
            auth_max_failures=max(1, _env_int("AUTH_MAX_FAILURES", 3)),
            auth_alert_cooldown_minutes=max(5, _env_int("AUTH_ALERT_COOLDOWN_MINUTES", 60)),
            tkb_poll_next_week=_env_bool("TKB_POLL_NEXT_WEEK", True),
            tkb_confirm_removals=_env_bool("TKB_CONFIRM_REMOVALS", True),
            exam_years=os.getenv("EXAM_YEARS", "auto").strip().lower(),
            exam_semesters=os.getenv("EXAM_SEMESTERS", "all").strip().lower(),
            courses_enabled=_env_bool("COURSES_ENABLED", True),
            courses_base_url=(
                os.getenv("COURSES_BASE_URL", "https://courses.uit.edu.vn").strip().rstrip("/")
                or "https://courses.uit.edu.vn"
            ),
            courses_poll_seconds=max(60, _env_int("COURSES_POLL_SECONDS", 120)),
            courses_notification_limit=min(
                100, max(1, _env_int("COURSES_NOTIFICATION_LIMIT", 50))
            ),
            courses_include_messages=_env_bool("COURSES_INCLUDE_MESSAGES", True),
            courses_session_refresh_margin_minutes=max(
                1, _env_int("COURSES_SESSION_REFRESH_MARGIN_MINUTES", 15)
            ),
            tz_name=os.getenv("TZ", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh",
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            state_db_path=_resolve_path(os.getenv("STATE_DB_PATH", "./data/state.db")),
            dry_run=_env_bool("DRY_RUN", False),
            heartbeat_enabled=_env_bool("HEARTBEAT_ENABLED", False),
            startup_message=_env_bool("STARTUP_MESSAGE", True),
            year_id=_env_opt_int("YEAR_ID"),
            nam_hoc=_env_opt_int("NAM_HOC"),
            hoc_ky=_env_opt_int("HOC_KY"),
            confirm_removal_delay_seconds=max(0, _env_int("CONFIRM_REMOVAL_DELAY_SECONDS", 45)),
            portal_failure_alert_threshold=max(1, _env_int("PORTAL_FAILURE_ALERT_THRESHOLD", 6)),
        )
        if cand.announcement_mode not in {"relevant", "all"}:
            raise ValueError("ANNOUNCEMENT_MODE phải là 'relevant' hoặc 'all'")
        if not cand.dry_run:
            missing = [
                name
                for name, value in (
                    ("UIT_USERNAME", cand.uit_username),
                    ("UIT_PASSWORD", cand.uit_password),
                    ("TELEGRAM_BOT_TOKEN", cand.telegram_bot_token),
                    ("TELEGRAM_CHAT_ID", cand.telegram_chat_id),
                )
                if not value
            ]
            if missing:
                raise ValueError("Thiếu cấu hình bắt buộc: " + ", ".join(missing))
        return cand


def redacted_config_summary(config: Config) -> dict[str, object]:
    return {
        "uit_username": config.uit_username,
        "telegram_chat_id": config.telegram_chat_id,
        "announcement_mode": config.announcement_mode,
        "digest_send_empty": config.digest_send_empty,
        "courses_enabled": config.courses_enabled,
        "tz": config.tz_name,
        "state_db_path": str(config.state_db_path),
        "dry_run": config.dry_run,
    }
