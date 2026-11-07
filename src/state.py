from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Announcement, Exam, TkbSession

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tkb_weeks (
    week_start TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exams (
    exam_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    year INTEGER NOT NULL,
    semester TEXT NOT NULL,
    examination TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    category_slugs TEXT,
    publish_date TEXT,
    first_seen_at TEXT NOT NULL,
    notified_at TEXT,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    event_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS courses_notifications (
    id INTEGER PRIMARY KEY,
    subject TEXT,
    component TEXT,
    eventtype TEXT,
    contexturl TEXT,
    timecreated INTEGER,
    payload TEXT,
    first_seen_at TEXT NOT NULL,
    notified_at TEXT,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS courses_messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    useridfrom INTEGER,
    sender_name TEXT,
    text TEXT,
    timecreated INTEGER,
    payload TEXT,
    first_seen_at TEXT NOT NULL,
    notified_at TEXT,
    processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_exams_combo ON exams(year, semester, examination);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);
CREATE INDEX IF NOT EXISTS idx_courses_notifications_processed
    ON courses_notifications(processed_at);
CREATE INDEX IF NOT EXISTS idx_courses_messages_processed
    ON courses_messages(processed_at);
"""


class State:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()
        logger.info("SQLite state: %s", self.db_path)

    def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(announcements)").fetchall()
        }
        if "processed_at" not in columns:
            self._conn.execute("ALTER TABLE announcements ADD COLUMN processed_at TEXT")
        # One-time backfill: announcements from before this migration are considered
        # processed (baseline / already evaluated), not new. Do not run this on every
        # startup, otherwise a pending announcement from a crash could be lost.
        migrated = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'migration_processed_at_v1'"
        ).fetchone()
        if not migrated:
            self._conn.execute(
                "UPDATE announcements SET processed_at = first_seen_at "
                "WHERE processed_at IS NULL"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) "
                "VALUES('migration_processed_at_v1', '1')"
            )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------- meta ----------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_meta_int(self, key: str) -> int | None:
        raw = self.get_meta(key)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def get_meta_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_meta(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_meta_json(self, key: str, value: Any) -> None:
        self.set_meta(key, json.dumps(value, ensure_ascii=False))

    # ---------- TKB snapshots ----------

    def get_week(self, week_start: str) -> list[TkbSession] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM tkb_weeks WHERE week_start = ?", (week_start,)
            ).fetchone()
        if not row:
            return None
        try:
            return [TkbSession.from_dict(item) for item in json.loads(row["payload"])]
        except Exception:
            logger.exception("Không đọc được snapshot TKB tuần %s", week_start)
            return None

    def save_week(self, week_start: str, sessions: Iterable[TkbSession]) -> None:
        payload = json.dumps(
            [session.to_dict() for session in sessions], ensure_ascii=False
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tkb_weeks(week_start, payload, fetched_at) VALUES(?, ?, ?) "
                "ON CONFLICT(week_start) DO UPDATE SET "
                "payload = excluded.payload, fetched_at = excluded.fetched_at",
                (week_start, payload, utc_now_iso()),
            )

    # ---------- exams ----------

    def load_exams(self) -> list[Exam]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM exams ORDER BY exam_key"
            ).fetchall()
        result: list[Exam] = []
        for row in rows:
            try:
                result.append(Exam.from_dict(json.loads(row["payload"])))
            except Exception:
                logger.exception("Không đọc được exam row")
        return result

    def has_any_exams(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM exams LIMIT 1").fetchone()
        return row is not None

    def save_exams(self, exams: Iterable[Exam]) -> None:
        now = utc_now_iso()
        rows = [
            (
                exam.exam_key,
                json.dumps(exam.to_dict(), ensure_ascii=False),
                exam.year,
                exam.semester,
                exam.examination,
                now,
                now,
            )
            for exam in exams
        ]
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO exams(exam_key, payload, year, semester, examination, "
                "first_seen_at, last_seen_at) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(exam_key) DO UPDATE SET "
                "payload = excluded.payload, last_seen_at = excluded.last_seen_at",
                rows,
            )

    def delete_exams(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM exams WHERE exam_key = ?", [(k,) for k in keys])

    # ---------- announcements ----------

    def get_seen_announcement_ids(self) -> set[int]:
        values = self.get_meta_json("announcement_seen_ids", [])
        if not isinstance(values, list):
            return set()
        result: set[int] = set()
        for value in values:
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
        return result

    def save_seen_announcement_ids(self, ids: Iterable[int], keep: int = 500) -> None:
        values = sorted({int(value) for value in ids}, reverse=True)[:keep]
        self.set_meta_json("announcement_seen_ids", values)

    def get_announcement_max_id(self) -> int | None:
        return self.get_meta_int("announcement_max_id")

    def set_announcement_max_id(self, value: int) -> None:
        current = self.get_announcement_max_id()
        if current is None or value > current:
            self.set_meta("announcement_max_id", str(value))

    def announcement_exists(self, ann_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM announcements WHERE id = ?", (ann_id,)
            ).fetchone()
        return row is not None

    def announcement_is_processed(self, ann_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT processed_at FROM announcements WHERE id = ?", (ann_id,)
            ).fetchone()
        return bool(row and row["processed_at"])

    def mark_announcement_processed(self, ann_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE announcements SET processed_at = ? WHERE id = ?",
                (utc_now_iso(), ann_id),
            )

    def save_announcement(self, ann: Announcement) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO announcements"
                "(id, slug, title, category_slugs, publish_date, first_seen_at, notified_at) "
                "VALUES(?, ?, ?, ?, ?, ?, NULL)",
                (
                    ann.id,
                    ann.slug,
                    ann.title,
                    json.dumps(ann.category_slugs, ensure_ascii=False),
                    ann.publish_date,
                    utc_now_iso(),
                ),
            )

    def mark_announcement_notified(self, ann_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE announcements SET notified_at = ? WHERE id = ?",
                (utc_now_iso(), ann_id),
            )

    # ---------- notifications ----------

    def get_notification_status(self, event_key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM notifications WHERE event_key = ?", (event_key,)
            ).fetchone()
        return row["status"] if row else None

    def ensure_notification(self, event_key: str, kind: str, payload: Any) -> str:
        """Create a pending row if missing; return current status."""
        status = self.get_notification_status(event_key)
        if status is not None:
            return status
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO notifications"
                "(event_key, kind, payload, status, attempts, created_at) "
                "VALUES(?, ?, ?, 'pending', 0, ?)",
                (
                    event_key,
                    kind,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    utc_now_iso(),
                ),
            )
        return "pending"

    def mark_notification_sent(self, event_key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE notifications SET status = 'sent', sent_at = ?, error = NULL "
                "WHERE event_key = ?",
                (utc_now_iso(), event_key),
            )

    def mark_notification_failed(self, event_key: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE notifications SET status = 'failed', attempts = attempts + 1, "
                "error = ? WHERE event_key = ?",
                (error[:1000], event_key),
            )

    # ---------- digest / cache ----------

    def get_last_digest_date(self) -> str | None:
        return self.get_meta("last_digest_date")

    def set_last_digest_date(self, value: str) -> None:
        self.set_meta("last_digest_date", value)

    # ---------- Courses (Moodle) ----------

    def save_courses_notification(self, notification: dict) -> None:
        try:
            ann_id = int(notification.get("id"))
        except (TypeError, ValueError):
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO courses_notifications"
                "(id, subject, component, eventtype, contexturl, timecreated, "
                "payload, first_seen_at, notified_at, processed_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    ann_id,
                    notification.get("subject") or "",
                    notification.get("component") or "",
                    notification.get("eventtype") or "",
                    notification.get("contexturl") or "",
                    int(notification.get("timecreated") or 0),
                    json.dumps(notification, ensure_ascii=False, default=str),
                    utc_now_iso(),
                ),
            )

    def courses_notification_is_processed(self, notification_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT processed_at FROM courses_notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
        return bool(row and row["processed_at"])

    def mark_courses_notification_processed(self, notification_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE courses_notifications SET processed_at = ? WHERE id = ?",
                (utc_now_iso(), notification_id),
            )

    def mark_courses_notification_notified(self, notification_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE courses_notifications SET notified_at = ? WHERE id = ?",
                (utc_now_iso(), notification_id),
            )

    def save_courses_message(
        self, message: dict, conversation_id: int, sender_name: str
    ) -> None:
        try:
            message_id = int(message.get("id"))
        except (TypeError, ValueError):
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO courses_messages"
                "(id, conversation_id, useridfrom, sender_name, text, timecreated, "
                "payload, first_seen_at, notified_at, processed_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    message_id,
                    int(conversation_id),
                    int(message.get("useridfrom") or 0),
                    sender_name or "",
                    message.get("text") or "",
                    int(message.get("timecreated") or 0),
                    json.dumps(message, ensure_ascii=False, default=str),
                    utc_now_iso(),
                ),
            )

    def courses_message_is_processed(self, message_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT processed_at FROM courses_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return bool(row and row["processed_at"])

    def mark_courses_message_processed(self, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE courses_messages SET processed_at = ? WHERE id = ?",
                (utc_now_iso(), message_id),
            )

    def mark_courses_message_notified(self, message_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE courses_messages SET notified_at = ? WHERE id = ?",
                (utc_now_iso(), message_id),
            )

    def get_class_codes(self) -> set[str]:
        values = self.get_meta_json("class_codes", [])
        if not isinstance(values, list):
            return set()
        return {str(value).upper() for value in values if value}

    def save_class_codes(self, codes: Iterable[str]) -> None:
        self.set_meta_json("class_codes", sorted({str(c).upper() for c in codes if c}))
        self.set_meta("class_codes_updated_at", utc_now_iso())

    # ---------- maintenance ----------

    def prune(self, days: int = 180) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        week_cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM announcements WHERE first_seen_at < ?", (cutoff,)
            )
            self._conn.execute(
                "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
            )
            self._conn.execute(
                "DELETE FROM tkb_weeks WHERE fetched_at < ?", (week_cutoff,)
            )
            self._conn.execute(
                "DELETE FROM exams WHERE last_seen_at < ?", (cutoff,)
            )
            self._conn.execute(
                "DELETE FROM courses_notifications WHERE first_seen_at < ?", (cutoff,)
            )
            self._conn.execute(
                "DELETE FROM courses_messages WHERE first_seen_at < ?", (cutoff,)
            )

    def heartbeat(self) -> None:
        path = self.db_path.parent / "heartbeat"
        path.write_text(datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
