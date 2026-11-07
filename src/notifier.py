from __future__ import annotations

import html
import logging
import re
import time
from typing import Iterable

import requests

from .config import Config
from .detector import announcement_affects_student
from .formatter import (
    format_announcement,
    format_announcement_batch,
    format_auth_alert,
    format_portal_alert,
    format_startup,
    format_test_message,
)
from .models import Announcement
from .state import State

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 3800


class TelegramError(RuntimeError):
    pass


def split_text(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if line_len > limit:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            for index in range(0, len(line), limit):
                chunks.append(line[index : index + limit])
            continue
        if current_len + line_len > limit and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:limit]]


def _to_plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class TelegramNotifier:
    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self._api = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"

    def _send_chunk(self, text: str) -> int:
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if self.config.telegram_message_thread_id:
            payload["message_thread_id"] = self.config.telegram_message_thread_id

        last_error = "unknown"
        for attempt in range(1, 5):
            try:
                response = requests.post(self._api, json=payload, timeout=(10, 30))
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("Telegram lỗi mạng (lần %s): %s", attempt, exc)
                time.sleep(min(30, 2 ** attempt))
                continue

            try:
                data = response.json()
            except ValueError:
                data = {}

            if response.status_code == 200 and data.get("ok"):
                return int(data.get("result", {}).get("message_id", 0))

            description = data.get("description") or response.text[:300]
            last_error = f"HTTP {response.status_code}: {description}"

            if response.status_code == 429:
                retry_after = 3
                try:
                    retry_after = int((data.get("parameters") or {}).get("retry_after", 3))
                except (TypeError, ValueError):
                    pass
                retry_after = max(1, min(60, retry_after))
                logger.warning("Telegram rate limit, chờ %ss", retry_after)
                time.sleep(retry_after)
                continue

            if response.status_code == 400 and payload.get("parse_mode"):
                logger.warning("Telegram từ chối HTML, thử lại dạng plain text")
                payload.pop("parse_mode", None)
                payload["text"] = _to_plain(payload["text"])
                continue

            if response.status_code >= 500:
                logger.warning("Telegram 5xx (lần %s): %s", attempt, last_error)
                time.sleep(min(30, 2 ** attempt))
                continue

            break

        raise TelegramError(last_error)

    def send_text(
        self,
        text: str,
        *,
        event_key: str | None = None,
        kind: str = "general",
        payload: dict | None = None,
    ) -> bool:
        if not text.strip():
            return False

        if self.config.dry_run:
            if event_key:
                logger.info("[DRY-RUN] %s: %s", event_key, text[:300].replace("\n", " | "))
            else:
                logger.info("[DRY-RUN] %s", text[:300].replace("\n", " | "))
            return True

        if event_key:
            status = self.state.get_notification_status(event_key)
            if status == "sent":
                return True
            self.state.ensure_notification(event_key, kind, payload or {"text": text[:1000]})

        try:
            for chunk in split_text(text):
                self._send_chunk(chunk)
        except TelegramError as exc:
            if event_key:
                self.state.mark_notification_failed(event_key, str(exc))
            logger.error("Gửi Telegram thất bại (%s): %s", event_key or kind, exc)
            return False

        if event_key:
            self.state.mark_notification_sent(event_key)
        logger.info("Đã gửi Telegram (%s)", event_key or kind)
        return True

    def send_announcements(
        self,
        announcements: list[Announcement],
        student_codes: set[str],
    ) -> None:
        pending = [
            ann
            for ann in announcements
            if self.state.get_notification_status(f"announcement:{ann.id}") != "sent"
        ]
        if not pending:
            return

        if len(pending) <= 3:
            for ann in pending:
                text = format_announcement(
                    ann, announcement_affects_student(ann, student_codes)
                )
                if self.send_text(
                    text,
                    event_key=f"announcement:{ann.id}",
                    kind="announcement",
                    payload=ann.to_dict(),
                ):
                    self.state.mark_announcement_notified(ann.id)
            return

        # Batch more than 3 to avoid spamming.
        for start in range(0, len(pending), 10):
            group = pending[start : start + 10]
            group_key = f"announcement_batch:{group[0].id}-{group[-1].id}"
            status = self.state.get_notification_status(group_key)
            if status == "sent":
                for ann in group:
                    self.state.mark_notification_sent(f"announcement:{ann.id}")
                    self.state.mark_announcement_notified(ann.id)
                continue
            text = format_announcement_batch(group)
            if self.send_text(
                text,
                event_key=group_key,
                kind="announcement_batch",
                payload={"ids": [ann.id for ann in group]},
            ):
                for ann in group:
                    self.state.mark_notification_sent(f"announcement:{ann.id}")
                    self.state.mark_announcement_notified(ann.id)

    def send_auth_alert(self, error: str, attempts: int) -> bool:
        return self.send_text(format_auth_alert(error, attempts), kind="auth_alert")

    def send_portal_alert(self, kind: str, error: str, failures: int) -> bool:
        return self.send_text(
            format_portal_alert(kind, error, failures), kind="portal_alert"
        )

    def send_startup(
        self,
        *,
        username: str,
        auth_ok: bool,
        announcement_mode: str,
        digest_time: str,
        session_count: int,
        exam_count: int,
        courses_line: str | None = None,
    ) -> bool:
        return self.send_text(
            format_startup(
                username=username,
                auth_ok=auth_ok,
                announcement_mode=announcement_mode,
                digest_time=digest_time,
                session_count=session_count,
                exam_count=exam_count,
                courses_line=courses_line,
            ),
            kind="startup",
        )

    def send_test(self) -> bool:
        return self.send_text(format_test_message(self.config.uit_username), kind="test")
