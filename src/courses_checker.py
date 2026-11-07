from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from .config import Config
from .courses_client import CoursesAuthError, CoursesClient, CoursesError
from .courses_formatter import (
    COURSES_URL,
    format_courses_auth_alert,
    format_courses_message,
    format_courses_notification,
    format_courses_portal_alert,
)
from .notifier import TelegramNotifier
from .state import State

logger = logging.getLogger(__name__)


class CoursesChecker:
    def __init__(self, config: Config, state: State, notifier: TelegramNotifier):
        self.config = config
        self.state = state
        self.notifier = notifier
        self.client = CoursesClient(config, state)
        self._lock = threading.Lock()
        self._failures = 0
        self._last_summary = {"ok": False, "notifications": 0, "messages": 0}

    # ---------- bootstrap / poll ----------

    def startup(self) -> dict[str, Any]:
        """Validate login; baseline only on first run, never re-baseline silently."""
        if not self.config.courses_enabled:
            return {"ok": False, "notifications": 0, "messages": 0}
        if self.state.get_meta("courses_bootstrapped") != "1":
            return self.bootstrap()
        try:
            self.client.login()
        except CoursesAuthError as exc:
            logger.error("Không đăng nhập được Courses khi khởi động: %s", exc)
            return {"ok": False, "notifications": None, "messages": None}
        return {"ok": True, "notifications": None, "messages": None}

    def bootstrap(self) -> dict[str, Any]:
        if not self.config.courses_enabled:
            return {"ok": False, "notifications": 0, "messages": 0}
        try:
            self.client.login()
        except CoursesAuthError as exc:
            logger.error("Không đăng nhập được Courses: %s", exc)
            self._last_summary = {"ok": False, "notifications": 0, "messages": 0}
            return self._last_summary

        notification_count = 0
        message_count = 0
        try:
            data = self.client.get_notifications(self.config.courses_notification_limit, 0)
            items = data.get("notifications") or []
            max_id = self.state.get_meta_int("courses_notification_max_id") or 0
            for item in items:
                try:
                    item_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                self.state.save_courses_notification(item)
                self.state.mark_courses_notification_processed(item_id)
                max_id = max(max_id, item_id)
                notification_count += 1
            if max_id:
                self.state.set_meta("courses_notification_max_id", str(max_id))

            conversations = self.client.get_conversations(50).get("conversations") or []
            max_message_id = self.state.get_meta_int("courses_message_max_id") or 0
            for conversation in conversations:
                messages = conversation.get("messages") or []
                if not messages:
                    continue
                conv_id = int(conversation.get("id"))
                try:
                    detail = self.client.get_conversation_messages(conv_id, 10)
                    detail_messages = detail.get("messages") or []
                except CoursesError:
                    detail_messages = messages
                members = {
                    int(member.get("id")): member.get("fullname") or ""
                    for member in (conversation.get("members") or [])
                }
                for message in detail_messages:
                    try:
                        message_id = int(message.get("id"))
                    except (TypeError, ValueError):
                        continue
                    sender = members.get(int(message.get("useridfrom") or 0), "")
                    self.state.save_courses_message(message, conv_id, sender)
                    self.state.mark_courses_message_processed(message_id)
                    max_message_id = max(max_message_id, message_id)
                    message_count += 1
            if max_message_id:
                self.state.set_meta("courses_message_max_id", str(max_message_id))
            self.state.set_meta("courses_bootstrapped", "1")
            self.state.set_meta(
                "courses_last_poll_success",
                datetime.now(self.config.tz).isoformat(timespec="seconds"),
            )
            self._last_summary = {
                "ok": True,
                "notifications": notification_count,
                "messages": message_count,
            }
            logger.info(
                "Baseline Courses: %s thông báo, %s tin nhắn",
                notification_count,
                message_count,
            )
            return self._last_summary
        except CoursesError as exc:
            logger.error("Lỗi baseline Courses: %s", exc)
            self._last_summary = {"ok": False, "notifications": 0, "messages": 0}
            return self._last_summary

    def poll(self) -> None:
        if not self.config.courses_enabled:
            return
        if self.state.get_meta("courses_bootstrapped") != "1":
            self.bootstrap()
            return
        try:
            self.client.ensure_session()
            self._poll_notifications()
            if self.config.courses_include_messages:
                self._poll_messages()
            self.state.set_meta(
                "courses_last_poll_success",
                datetime.now(self.config.tz).isoformat(timespec="seconds"),
            )
            self._failures = 0
        except CoursesAuthError as exc:
            self._handle_auth_error(exc)
        except CoursesError as exc:
            self._record_failure(exc)
        except Exception as exc:
            logger.exception("Lỗi không mong đợi khi poll Courses")
            self._record_failure(exc)

    def job(self) -> None:
        if not self._lock.acquire(blocking=False):
            logger.warning("Job Courses vẫn đang chạy, bỏ qua lượt này")
            return
        try:
            self.poll()
        finally:
            self._lock.release()

    # ---------- notifications ----------

    def _poll_notifications(self) -> None:
        limit = self.config.courses_notification_limit
        max_id = self.state.get_meta_int("courses_notification_max_id") or 0
        new_items: list[dict[str, Any]] = []
        fetched_any = False

        for page in range(5):
            offset = page * limit
            data = self.client.get_notifications(limit, offset)
            items = data.get("notifications") or []
            if not items:
                break
            fetched_any = True
            new_on_page = 0
            for item in items:
                try:
                    item_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                processed = self.state.courses_notification_is_processed(item_id)
                if not processed:
                    new_items.append(item)
                    new_on_page += 1
                self.state.save_courses_notification(item)
                max_id = max(max_id, item_id)
            if len(items) < limit:
                break
            if new_on_page == 0:
                break

        if max_id:
            self.state.set_meta("courses_notification_max_id", str(max_id))
        if not fetched_any:
            return
        if not new_items:
            logger.debug("Courses: không có thông báo mới")
            return

        new_items.sort(key=lambda item: (int(item.get("timecreated") or 0), int(item.get("id") or 0)))
        logger.info("Courses: có %s thông báo mới", len(new_items))
        for item in new_items:
            item_id = int(item["id"])
            context_url = item.get("contexturl")
            if context_url and not str(context_url).startswith("http"):
                item["contexturl"] = urljoin(COURSES_URL, str(context_url))
            text = format_courses_notification(item, self.config.tz)
            ok = self.notifier.send_text(
                text,
                event_key=f"courses:noti:{item_id}",
                kind="courses_notification",
                payload=item,
            )
            if ok or self.config.dry_run:
                self.state.mark_courses_notification_processed(item_id)
                if not self.config.dry_run:
                    self.state.mark_courses_notification_notified(item_id)

    # ---------- messages ----------

    def _poll_messages(self) -> None:
        conversations = self.client.get_conversations(50).get("conversations") or []
        max_id = self.state.get_meta_int("courses_message_max_id") or 0
        new_messages: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        fetched_any = False

        for conversation in conversations:
            latest_messages = conversation.get("messages") or []
            if not latest_messages:
                continue
            try:
                latest_id = int(latest_messages[-1].get("id") or 0)
            except (TypeError, ValueError):
                latest_id = 0
            if latest_id and latest_id <= max_id and self.state.courses_message_is_processed(latest_id):
                continue

            conv_id = int(conversation.get("id"))
            try:
                detail = self.client.get_conversation_messages(conv_id, 10)
                messages = detail.get("messages") or []
            except CoursesError as exc:
                logger.warning("Courses: không lấy được tin nhắn hội thoại %s: %s", conv_id, exc)
                continue
            if not messages:
                continue
            fetched_any = True
            members = {
                int(member.get("id")): member.get("fullname") or ""
                for member in (conversation.get("members") or [])
            }
            for message in sorted(messages, key=lambda item: int(item.get("id") or 0)):
                try:
                    message_id = int(message.get("id"))
                except (TypeError, ValueError):
                    continue
                processed = self.state.courses_message_is_processed(message_id)
                sender_id = int(message.get("useridfrom") or 0)
                sender_name = members.get(sender_id, "")
                self.state.save_courses_message(message, conv_id, sender_name)
                max_id = max(max_id, message_id)
                if processed:
                    continue
                if sender_id == self.client.user_id or sender_id <= 0:
                    self.state.mark_courses_message_processed(message_id)
                    continue
                new_messages.append((message, conversation, sender_name))

        if max_id:
            self.state.set_meta("courses_message_max_id", str(max_id))
        if not fetched_any:
            return
        if not new_messages:
            logger.debug("Courses: không có tin nhắn mới")
            return

        logger.info("Courses: có %s tin nhắn mới", len(new_messages))
        for message, conversation, sender_name in new_messages:
            message_id = int(message["id"])
            text = format_courses_message(
                message, conversation, sender_name, self.config.tz
            )
            ok = self.notifier.send_text(
                text,
                event_key=f"courses:msg:{message_id}",
                kind="courses_message",
                payload=message,
            )
            if ok or self.config.dry_run:
                self.state.mark_courses_message_processed(message_id)
                if not self.config.dry_run:
                    self.state.mark_courses_message_notified(message_id)

    # ---------- errors ----------

    def _handle_auth_error(self, error: Exception) -> None:
        count = max(1, self.client.consecutive_auth_failures)
        logger.error("Lỗi xác thực Courses (%s lần): %s", count, error)
        if count < 3:
            return
        last_raw = self.state.get_meta("courses_last_auth_alert_at")
        now = time.time()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw).timestamp()
                if now - last < 3600:
                    return
            except ValueError:
                pass
        if self.notifier.send_text(
            format_courses_auth_alert(str(error), count, self.config.tz),
            kind="courses_auth_alert",
        ):
            self.state.set_meta(
                "courses_last_auth_alert_at",
                datetime.now(self.config.tz).isoformat(timespec="seconds"),
            )

    def _record_failure(self, error: Exception) -> None:
        self._failures += 1
        logger.warning("Lỗi Courses (%s lần): %s", self._failures, error)
        if self._failures < 6:
            return
        last_raw = self.state.get_meta("courses_last_portal_alert_at")
        now = time.time()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw).timestamp()
                if now - last < 3600:
                    return
            except ValueError:
                pass
        if self.notifier.send_text(
            format_courses_portal_alert("Courses", str(error), self._failures, self.config.tz),
            kind="courses_portal_alert",
        ):
            self.state.set_meta(
                "courses_last_portal_alert_at",
                datetime.now(self.config.tz).isoformat(timespec="seconds"),
            )

    @property
    def summary(self) -> dict[str, Any]:
        return self._last_summary
