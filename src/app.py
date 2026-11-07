from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Config
from .courses_checker import CoursesChecker
from .courses_formatter import format_courses_startup_line
from .detector import (
    diff_exams,
    diff_tkb,
    filter_relevant_announcements,
    is_class_specific,
)
from .formatter import (
    format_digest,
    format_exam_change,
    format_tkb_change,
    format_tkb_cleared,
    fmt_date_short,
)
from .models import Exam, TkbSession, stable_hash
from .notifier import TelegramNotifier
from .parsers import parse_announcements_payload, parse_exam_items, parse_tkb_payload
from .portal_client import (
    AuthError,
    PortalClient,
    PortalDataError,
    PortalError,
    PublicPortalClient,
)
from .state import State, utc_now_iso

logger = logging.getLogger(__name__)


def digest_time_label(cron: str) -> str:
    parts = (cron or "").split()
    if len(parts) >= 2:
        try:
            minute, hour = int(parts[0]), int(parts[1])
            return f"{hour:02d}:{minute:02d}"
        except ValueError:
            pass
    return "06:00"


class App:
    def __init__(
        self,
        config: Config,
        state: State,
        portal: PortalClient,
        public: PublicPortalClient,
        notifier: TelegramNotifier,
    ):
        self.config = config
        self.state = state
        self.portal = portal
        self.public = public
        self.notifier = notifier
        self.courses = (
            CoursesChecker(config, state, notifier) if config.courses_enabled else None
        )
        self.tz = config.tz
        self._job_locks: dict[str, threading.Lock] = {
            name: threading.Lock()
            for name in ("announcements", "tkb", "exams", "digest", "cleanup", "heartbeat")
        }
        self._failures: dict[str, int] = {}
        self._last_tkb_was_stale = False
        self._last_tkb_failed_without_snapshot = False

    # ---------- time helpers ----------

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today_iso(self) -> str:
        return self.now().date().isoformat()

    def current_week_start(self) -> str:
        today = self.now().date()
        return (today - timedelta(days=today.weekday())).isoformat()

    def next_week_start(self) -> str:
        today = self.now().date()
        return (today - timedelta(days=today.weekday()) + timedelta(days=7)).isoformat()

    def _is_first_run(self) -> bool:
        return self.state.get_meta("bootstrapped") != "1"

    # ---------- job locking / health ----------

    def _acquire(self, name: str) -> bool:
        lock = self._job_locks[name]
        if not lock.acquire(blocking=False):
            logger.warning("Job %s vẫn đang chạy, bỏ qua lượt này", name)
            return False
        return True

    def _release(self, name: str) -> None:
        try:
            self._job_locks[name].release()
        except RuntimeError:
            pass

    def _record_success(self, kind: str) -> None:
        self._failures[kind] = 0
        self.state.heartbeat()

    def _record_failure(self, kind: str, error: Exception | str) -> None:
        count = self._failures.get(kind, 0) + 1
        self._failures[kind] = count
        logger.warning("Lỗi %s (%s lần): %s", kind, count, error)
        if count < self.config.portal_failure_alert_threshold:
            return
        cooldown_key = f"last_portal_alert_{kind}"
        last_raw = self.state.get_meta(cooldown_key)
        now = time.time()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw).timestamp()
                if now - last < 3600:
                    return
            except ValueError:
                pass
        if self.notifier.send_portal_alert(kind, str(error), count):
            self.state.set_meta(cooldown_key, self.now().isoformat(timespec="seconds"))

    def _handle_auth_failure(self, error: Exception) -> None:
        count = max(1, self.portal.consecutive_auth_failures)
        logger.error("Lỗi xác thực (%s lần): %s", count, error)
        if count < self.config.auth_max_failures:
            return
        last_raw = self.state.get_meta("last_auth_alert_at")
        now = time.time()
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw).timestamp()
                if now - last < self.config.auth_alert_cooldown_minutes * 60:
                    return
            except ValueError:
                pass
        if self.notifier.send_auth_alert(str(error), count):
            self.state.set_meta("last_auth_alert_at", self.now().isoformat(timespec="seconds"))

    def _ensure_auth(self) -> bool:
        try:
            self.portal.ensure_session()
            return True
        except AuthError as exc:
            self._handle_auth_failure(exc)
            return False
        except PortalError as exc:
            self._record_failure("auth", exc)
            return False
        except Exception as exc:
            self._record_failure("auth", exc)
            return False

    # ---------- bootstrap ----------

    def bootstrap(
        self, *, send_startup: bool = True, check_digest: bool = True
    ) -> dict[str, int]:
        first_run = self._is_first_run()
        auth_ok = self._ensure_auth()

        if first_run:
            logger.info("Lần chạy đầu tiên: ghi nhận baseline, không gửi thông báo cũ")
            self._bootstrap_announcements()
            if auth_ok:
                self._bootstrap_tkb()
                self._bootstrap_exams()
            self.state.set_meta("bootstrapped", "1")
        else:
            logger.info("Đã có state baseline; bỏ qua bootstrap")
            if auth_ok:
                self._refresh_class_codes()

        courses_summary = None
        if self.courses:
            try:
                courses_summary = self.courses.startup()
            except Exception as exc:
                logger.exception("Courses startup lỗi, bỏ qua để portal tiếp tục chạy")
                courses_summary = {
                    "ok": False,
                    "notifications": None,
                    "messages": None,
                    "error": str(exc),
                }

        session_count = 0
        for week in (self.current_week_start(), self.next_week_start()):
            session_count += len(self.state.get_week(week) or [])
        exam_count = len(self.state.load_exams())

        if send_startup and self.config.startup_message:
            self._send_startup_message(
                auth_ok, session_count, exam_count, first_run, courses_summary
            )

        if check_digest:
            self.maybe_send_digest_retry()
        self._record_success("bootstrap")
        return {"sessions": session_count, "exams": exam_count, "auth_ok": auth_ok}

    def _bootstrap_announcements(self) -> None:
        try:
            data = self.public.get_announcements(1, self.config.announcement_page_size)
            items = parse_announcements_payload(data)
        except PortalError as exc:
            logger.warning("Không lấy được thông báo baseline: %s", exc)
            return
        ids = [ann.id for ann in items]
        for ann in items:
            self.state.save_announcement(ann)
            self.state.mark_announcement_processed(ann.id)
        self.state.save_seen_announcement_ids(ids)
        if ids:
            self.state.set_announcement_max_id(max(ids))
        logger.info("Baseline thông báo: %s item (max_id=%s)", len(ids), max(ids) if ids else None)

    def _bootstrap_tkb(self) -> None:
        try:
            meta = self.portal.get_tkb_meta(force=True)
        except PortalError as exc:
            logger.warning("Không lấy được metadata TKB: %s", exc)
            return
        weeks = [self.current_week_start()]
        if self.config.tkb_poll_next_week:
            weeks.append(self.next_week_start())
        for week in weeks:
            try:
                data = self.portal.get_tkb_week(week, meta)
                sessions = parse_tkb_payload(data, week)
                self.state.save_week(week, sessions)
                logger.info("Baseline TKB tuần %s: %s buổi", week, len(sessions))
            except PortalError as exc:
                logger.warning("Không lấy được TKB tuần %s: %s", week, exc)
        self._refresh_class_codes()

    def _bootstrap_exams(self) -> None:
        all_exams: list[Exam] = []
        success = 0
        for year, examination, semester in self._exam_combos():
            try:
                items = self.portal.get_exams(year, examination, semester)
                exams = parse_exam_items(
                    items, year=year, semester=semester, examination=examination
                )
                all_exams.extend(exams)
                success += 1
            except PortalError as exc:
                logger.warning(
                    "Không lấy được lịch thi %s/%s/%s: %s",
                    year,
                    semester,
                    examination,
                    exc,
                )
        if success:
            self.state.save_exams(all_exams)
            self.state.set_meta("last_exam_poll_success", self.now().isoformat(timespec="seconds"))
            logger.info("Baseline lịch thi: %s item", len(all_exams))

    # ---------- announcements ----------

    def job_announcements(self) -> None:
        if not self._acquire("announcements"):
            return
        try:
            self._poll_announcements()
        finally:
            self._release("announcements")

    def _poll_announcements(self) -> None:
        seen = self.state.get_seen_announcement_ids()
        max_id = self.state.get_announcement_max_id()
        if max_id is None and not seen:
            # Bootstrap failed earlier; treat this as baseline.
            logger.info("Chưa có baseline thông báo, ghi nhận baseline")
            self._bootstrap_announcements()
            return

        new_items = []
        fetched_ids: list[int] = []
        try:
            for page in range(1, self.config.announcement_max_pages + 1):
                data = self.public.get_announcements(
                    page, self.config.announcement_page_size
                )
                items = parse_announcements_payload(data)
                if not items:
                    break
                page_all_seen = True
                for ann in items:
                    processed = self.state.announcement_is_processed(ann.id)
                    is_new = not processed
                    self.state.save_announcement(ann)
                    fetched_ids.append(ann.id)
                    seen.add(ann.id)
                    if ann.id > (max_id or 0):
                        max_id = ann.id
                    if is_new:
                        new_items.append(ann)
                        page_all_seen = False
                if len(items) < self.config.announcement_page_size:
                    break
                if page_all_seen:
                    break
        except PortalError as exc:
            self._record_failure("announcements", exc)
            return

        if fetched_ids:
            self.state.save_seen_announcement_ids(seen)
            if max_id is not None:
                self.state.set_announcement_max_id(max_id)

        if not new_items:
            logger.debug("Không có thông báo mới")
            self._record_success("announcements")
            return

        student_codes = self.get_student_class_codes()
        deferred_ids: set[int] = set()
        if not student_codes and self.config.announcement_mode != "all":
            # Without class codes we can still send general notices, but keep
            # class-specific notices pending until codes are available.
            relevant = [ann for ann in new_items if not is_class_specific(ann)]
            deferred_ids = {ann.id for ann in new_items if is_class_specific(ann)}
            if deferred_ids:
                logger.warning(
                    "Chưa có mã lớp; tạm hoãn %s thông báo nghỉ/bù để lọc sau",
                    len(deferred_ids),
                )
        else:
            relevant = filter_relevant_announcements(
                new_items, student_codes, self.config.announcement_mode
            )
        logger.info(
            "Có %s thông báo mới, %s ảnh hưởng/đáng gửi (mode=%s)",
            len(new_items),
            len(relevant),
            self.config.announcement_mode,
        )
        relevant_ids = {ann.id for ann in relevant}
        for ann in new_items:
            if ann.id not in relevant_ids and ann.id not in deferred_ids:
                self.state.mark_announcement_processed(ann.id)

        if relevant:
            self.notifier.send_announcements(relevant, student_codes)
            for ann in relevant:
                status = self.state.get_notification_status(f"announcement:{ann.id}")
                if self.config.dry_run or status == "sent":
                    self.state.mark_announcement_processed(ann.id)
        self._record_success("announcements")

    # ---------- TKB ----------

    def job_tkb(self) -> None:
        if not self._acquire("tkb"):
            return
        try:
            self._poll_tkb()
        finally:
            self._release("tkb")

    def _poll_tkb(self) -> None:
        if not self._ensure_auth():
            return
        today = self.today_iso()
        weeks = [self.current_week_start()]
        if self.config.tkb_poll_next_week:
            weeks.append(self.next_week_start())

        try:
            meta = self.portal.get_tkb_meta()
        except AuthError as exc:
            self._handle_auth_failure(exc)
            return
        except PortalError as exc:
            self._record_failure("tkb", exc)
            return

        had_error = False
        for week in weeks:
            try:
                old = self.state.get_week(week)
                data = self.portal.get_tkb_week(week, meta)
                new = parse_tkb_payload(data, week)
            except PortalError as exc:
                logger.warning("Lỗi tải TKB tuần %s: %s", week, exc)
                self._record_failure("tkb", exc)
                had_error = True
                continue

            if old is None:
                self.state.save_week(week, new)
                logger.info("Baseline TKB tuần %s: %s buổi", week, len(new))
                continue

            # Whole-week removal is suspicious; confirm twice before alerting.
            if old and not new:
                if self.config.confirm_removal_delay_seconds > 0:
                    time.sleep(self.config.confirm_removal_delay_seconds)
                try:
                    data2 = self.portal.get_tkb_week(week, meta)
                    new2 = parse_tkb_payload(data2, week)
                except PortalError as exc:
                    logger.warning("Không xác nhận được tuần rỗng %s: %s", week, exc)
                    self._record_failure("tkb", exc)
                    continue
                if not new2:
                    event_key = f"tkb:cleared:{week}:{len(old)}"
                    text = format_tkb_cleared(week, len(old))
                    if self.notifier.send_text(
                        text,
                        event_key=event_key,
                        kind="tkb_cleared",
                        payload={"week": week, "previous_count": len(old)},
                    ):
                        self.state.save_week(week, new2)
                        self._record_success("tkb")
                    continue
                new = new2

            diff = diff_tkb(old, new, min_date=today)
            if diff.is_empty:
                self.state.save_week(week, new)
                continue

            if self.config.tkb_confirm_removals and diff.has_removals_or_changes:
                if self.config.confirm_removal_delay_seconds > 0:
                    time.sleep(self.config.confirm_removal_delay_seconds)
                try:
                    data2 = self.portal.get_tkb_week(week, meta)
                    new2 = parse_tkb_payload(data2, week)
                except PortalError as exc:
                    logger.warning("Không xác nhận được thay đổi TKB %s: %s", week, exc)
                    self._record_failure("tkb", exc)
                    continue
                diff = diff_tkb(old, new2, min_date=today)
                new = new2
                if diff.is_empty:
                    self.state.save_week(week, new)
                    continue

            event_hash = stable_hash(
                {
                    "week": week,
                    "added": [session.key for session in diff.added],
                    "removed": [session.key for session in diff.removed],
                    "changed": [
                        (old_session.key, new_session.comparable())
                        for old_session, new_session in diff.changed
                    ],
                }
            )
            event_key = f"tkb:week:{week}:{event_hash}"
            text = format_tkb_change(diff)
            if self.notifier.send_text(
                text,
                event_key=event_key,
                kind="tkb",
                payload={"week": week, "event_hash": event_hash},
            ):
                self.state.save_week(week, new)
                self._record_success("tkb")
            else:
                logger.warning("Chưa gửi được thay đổi TKB tuần %s, sẽ thử lại", week)

        if had_error:
            return
        self.state.set_meta("last_tkb_poll_success", self.now().isoformat(timespec="seconds"))
        self._record_success("tkb")

    # ---------- exams ----------

    def job_exams(self) -> None:
        if not self._acquire("exams"):
            return
        try:
            self._poll_exams()
        finally:
            self._release("exams")

    def _poll_exams(self) -> None:
        if not self._ensure_auth():
            return
        today = self.today_iso()
        success: dict[tuple[int, str, str], list[Exam]] = {}
        errors: dict[tuple[int, str, str], str] = {}

        for combo in self._exam_combos():
            year, examination, semester = combo
            try:
                items = self.portal.get_exams(year, examination, semester)
                success[combo] = parse_exam_items(
                    items, year=year, semester=semester, examination=examination
                )
            except AuthError as exc:
                errors[combo] = str(exc)
                self._handle_auth_failure(exc)
                logger.warning(
                    "Lỗi xác thực khi lấy lịch thi %s/%s/%s: %s",
                    year,
                    semester,
                    examination,
                    exc,
                )
                break
            except PortalError as exc:
                errors[combo] = str(exc)
                logger.warning(
                    "Lỗi lịch thi %s/%s/%s: %s", year, semester, examination, exc
                )

        if not success:
            for error in errors.values():
                self._record_failure("exams", error)
            return

        old = self.state.load_exams()
        for combo, new_for in success.items():
            year, examination, semester = combo
            old_for = [
                exam
                for exam in old
                if exam.year == year
                and exam.semester == semester
                and exam.examination == examination
            ]
            diff = diff_exams(old_for, new_for, min_date=today)

            if self.config.tkb_confirm_removals and diff.has_removals_or_changes:
                if self.config.confirm_removal_delay_seconds > 0:
                    time.sleep(self.config.confirm_removal_delay_seconds)
                try:
                    items2 = self.portal.get_exams(year, examination, semester)
                    new2 = parse_exam_items(
                        items2, year=year, semester=semester, examination=examination
                    )
                except PortalError as exc:
                    logger.warning(
                        "Không xác nhận được thay đổi lịch thi %s/%s/%s: %s",
                        year,
                        semester,
                        examination,
                        exc,
                    )
                    self._record_failure("exams", exc)
                    continue
                diff = diff_exams(old_for, new2, min_date=today)
                new_for = new2

            if diff.is_empty:
                self.state.save_exams(new_for)
                old_keys = {exam.exam_key for exam in old_for}
                new_keys = {exam.exam_key for exam in new_for}
                self.state.delete_exams(old_keys - new_keys)
                continue

            event_hash = stable_hash(
                {
                    "combo": combo,
                    "added": [exam.exam_key for exam in diff.added],
                    "removed": [exam.exam_key for exam in diff.removed],
                    "changed": [
                        (old_exam.exam_key, new_exam.comparable())
                        for old_exam, new_exam in diff.changed
                    ],
                }
            )
            event_key = (
                f"exam:combo:{year}:{semester}:{examination}:{event_hash}"
            )
            text = format_exam_change(diff)
            if self.notifier.send_text(
                text,
                event_key=event_key,
                kind="exam",
                payload={"combo": combo, "event_hash": event_hash},
            ):
                self.state.save_exams(new_for)
                old_keys = {exam.exam_key for exam in old_for}
                new_keys = {exam.exam_key for exam in new_for}
                self.state.delete_exams(old_keys - new_keys)

        if errors:
            for error in errors.values():
                self._record_failure("exams", error)
        else:
            self.state.set_meta(
                "last_exam_poll_success", self.now().isoformat(timespec="seconds")
            )
            self._record_success("exams")

    # ---------- digest ----------

    def job_digest(self) -> None:
        if not self._acquire("digest"):
            return
        try:
            self.send_digest(catchup=False)
        finally:
            self._release("digest")

    def job_digest_retry(self) -> None:
        if not self._acquire("digest"):
            return
        try:
            self.maybe_send_digest_retry()
        finally:
            self._release("digest")

    def maybe_send_digest_retry(self) -> None:
        today = self.today_iso()
        if self.state.get_last_digest_date() == today:
            return
        now = self.now()
        if now.hour < 6:
            return
        if now.time() > self.config.digest_catchup_until:
            logger.info("Đã quá giờ gửi bù digest (%s), bỏ qua", self.config.digest_catchup_until)
            return
        self.send_digest(catchup=True)

    def send_digest(self, *, force: bool = False, catchup: bool = False) -> bool:
        today = self.today_iso()
        if not force and self.state.get_last_digest_date() == today:
            return True

        sessions = self._digest_tkb_sessions()
        exams, exam_warning = self._digest_exams()
        today_sessions = [session for session in sessions if session.date == today]
        today_exams = [exam for exam in exams if exam.date == today]

        if not today_sessions and not today_exams and not self.config.digest_send_empty:
            logger.info("Hôm nay không có lịch; DIGEST_SEND_EMPTY=false nên không gửi")
            if not force:
                self.state.set_last_digest_date(today)
            return True

        if (
            self._last_tkb_failed_without_snapshot
            and not today_sessions
            and not today_exams
        ):
            text = (
                "⚠️ <b>UIT CHECKER</b>\n\n"
                "Không tải được thời khóa biểu hôm nay và cũng chưa có dữ liệu lưu trước đó.\n"
                "Vui lòng kiểm tra cổng UIT hoặc thử lại sau."
            )
        else:
            text = format_digest(
                today_sessions,
                today_exams,
                today,
                send_empty=self.config.digest_send_empty,
                catchup=catchup,
                stale_warning=self._last_tkb_was_stale,
                tkb_warning=self._last_tkb_failed_without_snapshot,
                exam_warning=exam_warning,
                tz=self.tz,
            )
        if not text:
            if not force:
                self.state.set_last_digest_date(today)
            return True

        event_key = f"digest:{today}" if not force else f"digest_manual:{today}:{int(time.time())}"
        ok = self.notifier.send_text(
            text,
            event_key=event_key,
            kind="digest",
            payload={"date": today, "catchup": catchup},
        )
        if ok:
            if not force:
                self.state.set_last_digest_date(today)
            self._record_success("digest")
        return ok

    def _digest_tkb_sessions(self) -> list[TkbSession]:
        week = self.current_week_start()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.portal.ensure_session()
                meta = self.portal.get_tkb_meta()
                data = self.portal.get_tkb_week(week, meta)
                sessions = parse_tkb_payload(data, week)
                self._last_tkb_was_stale = False
                self._last_tkb_failed_without_snapshot = False
                self._record_success("digest_tkb")
                return sessions
            except AuthError as exc:
                self._handle_auth_failure(exc)
                last_error = exc
                break
            except PortalError as exc:
                last_error = exc
                logger.warning("Digest: lỗi tải TKB (lần %s): %s", attempt + 1, exc)
                time.sleep(2 * (attempt + 1))

        snapshot = self.state.get_week(week)
        if snapshot is not None:
            logger.warning("Digest: dùng snapshot TKB cũ do lỗi: %s", last_error)
            self._last_tkb_was_stale = True
            self._last_tkb_failed_without_snapshot = False
            return snapshot
        logger.error("Digest: không có TKB và cũng không có snapshot: %s", last_error)
        self._last_tkb_was_stale = True
        self._last_tkb_failed_without_snapshot = True
        return []

    def _digest_exams(self) -> tuple[list[Exam], bool]:
        exams = self.state.load_exams()
        last_success = self.state.get_meta("last_exam_poll_success")
        stale = True
        if last_success:
            try:
                stale = (
                    self.now() - datetime.fromisoformat(last_success)
                ) > timedelta(hours=2)
            except ValueError:
                stale = True

        if exams and not stale:
            return exams, False

        # Try a quiet refresh; do not save/notify here.
        if self._ensure_auth():
            refreshed: list[Exam] = []
            any_success = False
            all_success = True
            for year, examination, semester in self._exam_combos():
                try:
                    items = self.portal.get_exams(year, examination, semester)
                    refreshed.extend(
                        parse_exam_items(
                            items,
                            year=year,
                            semester=semester,
                            examination=examination,
                        )
                    )
                    any_success = True
                except PortalError:
                    all_success = False
                    continue
            if any_success:
                return refreshed, not all_success
        return exams, True

    # ---------- class codes ----------

    def get_student_class_codes(self) -> set[str]:
        codes = self.state.get_class_codes()
        updated_raw = self.state.get_meta("class_codes_updated_at")
        stale = True
        if updated_raw:
            try:
                stale = (self.now() - datetime.fromisoformat(updated_raw)) > timedelta(hours=12)
            except ValueError:
                stale = True
        registration_refreshed = False
        if stale or not codes:
            try:
                self.portal.get_registration()
                codes = self.state.get_class_codes()
                registration_refreshed = True
            except PortalError as exc:
                logger.warning("Không refresh được mã lớp: %s", exc)

        # Also merge class codes from TKB snapshots (covers class code variants).
        for week in (self.current_week_start(), self.next_week_start()):
            for session in self.state.get_week(week) or []:
                codes.add(session.class_code.upper())
        if registration_refreshed and codes:
            self.state.save_class_codes(codes)
        return codes

    def _refresh_class_codes(self) -> None:
        try:
            self.portal.get_registration()
        except PortalError as exc:
            logger.warning("Không refresh được mã lớp: %s", exc)

    # ---------- startup message ----------

    def _send_startup_message(
        self,
        auth_ok: bool,
        session_count: int,
        exam_count: int,
        first_run: bool,
        courses_summary: dict[str, Any] | None = None,
    ) -> None:
        last_raw = self.state.get_meta("last_startup_message_at")
        if not first_run and last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                if (self.now() - last) < timedelta(minutes=30):
                    logger.info("Bỏ qua startup message (vừa gửi cách đây <30 phút)")
                    return
            except ValueError:
                pass
        courses_line = None
        if courses_summary is not None:
            courses_line = format_courses_startup_line(
                ok=bool(courses_summary.get("ok")),
                notification_count=courses_summary.get("notifications"),
                message_count=courses_summary.get("messages"),
            )
        ok = self.notifier.send_startup(
            username=self.config.uit_username,
            auth_ok=auth_ok,
            announcement_mode=self.config.announcement_mode,
            digest_time=digest_time_label(self.config.digest_cron),
            session_count=session_count,
            exam_count=exam_count,
            courses_line=courses_line,
        )
        if ok:
            self.state.set_meta(
                "last_startup_message_at", self.now().isoformat(timespec="seconds")
            )

    # ---------- exam combo helpers ----------

    def _exam_years(self) -> list[int]:
        current = self.now().year
        if self.config.exam_years != "auto":
            years = []
            for part in self.config.exam_years.split(","):
                part = part.strip()
                if part:
                    try:
                        years.append(int(part))
                    except ValueError:
                        pass
            return sorted(set(years)) or [current]
        years = {current}
        if self.now().month >= 11:
            years.add(current + 1)
        if self.now().month <= 1:
            years.add(current - 1)
        return sorted(years)

    def _exam_semesters(self) -> list[str]:
        if self.config.exam_semesters == "all":
            return ["semester_1", "semester_2", "semester_3"]
        semesters = []
        for part in self.config.exam_semesters.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if part.isdigit():
                semesters.append(f"semester_{part}")
            elif part.startswith("semester_"):
                semesters.append(part)
        return semesters or ["semester_1", "semester_2", "semester_3"]

    def _exam_combos(self) -> list[tuple[int, str, str]]:
        return [
            (year, examination, semester)
            for year in self._exam_years()
            for examination in ("midterm", "final_term")
            for semester in self._exam_semesters()
        ]

    # ---------- maintenance ----------

    def job_cleanup(self) -> None:
        if not self._acquire("cleanup"):
            return
        try:
            self.state.prune(180)
            self.state.heartbeat()
            logger.info("Đã dọn dẹp dữ liệu cũ")
        finally:
            self._release("cleanup")

    def job_heartbeat(self) -> None:
        if not self._acquire("heartbeat"):
            return
        try:
            self.state.heartbeat()
        finally:
            self._release("heartbeat")

    # ---------- scheduler ----------

    def run_forever(self) -> None:
        self.bootstrap(send_startup=True)
        scheduler = BackgroundScheduler(timezone=self.tz)
        now = self.now()

        scheduler.add_job(
            self.job_announcements,
            IntervalTrigger(seconds=self.config.announcement_poll_seconds),
            id="announcements",
            next_run_time=now + timedelta(seconds=30),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True,
        )
        scheduler.add_job(
            self.job_tkb,
            IntervalTrigger(seconds=self.config.tkb_poll_seconds),
            id="tkb",
            next_run_time=now + timedelta(seconds=60),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True,
        )
        scheduler.add_job(
            self.job_exams,
            IntervalTrigger(seconds=self.config.exam_poll_seconds),
            id="exams",
            next_run_time=now + timedelta(seconds=90),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True,
        )
        if self.courses:
            scheduler.add_job(
                self.courses.job,
                IntervalTrigger(seconds=self.config.courses_poll_seconds),
                id="courses",
                next_run_time=now + timedelta(seconds=120),
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
                replace_existing=True,
            )
        try:
            digest_trigger = CronTrigger.from_crontab(
                self.config.digest_cron, timezone=self.tz
            )
        except ValueError:
            logger.error(
                "DIGEST_CRON không hợp lệ (%s), dùng mặc định 0 6 * * *",
                self.config.digest_cron,
            )
            digest_trigger = CronTrigger(hour=6, minute=0, timezone=self.tz)
        scheduler.add_job(
            self.job_digest,
            digest_trigger,
            id="digest",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
        )
        scheduler.add_job(
            self.job_digest_retry,
            IntervalTrigger(minutes=5),
            id="digest_retry",
            next_run_time=now + timedelta(minutes=5),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True,
        )
        scheduler.add_job(
            self.job_cleanup,
            CronTrigger(hour=3, minute=0, timezone=self.tz),
            id="cleanup",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
        )
        scheduler.add_job(
            self.job_heartbeat,
            IntervalTrigger(minutes=1),
            id="heartbeat",
            next_run_time=now + timedelta(minutes=1),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
            replace_existing=True,
        )

        scheduler.start()
        logger.info(
            "Scheduler đã chạy: announcements=%ss tkb=%ss exams=%ss courses=%ss digest=%s tz=%s",
            self.config.announcement_poll_seconds,
            self.config.tkb_poll_seconds,
            self.config.exam_poll_seconds,
            self.config.courses_poll_seconds if self.courses else 0,
            self.config.digest_cron,
            self.config.tz_name,
        )

        stop_event = threading.Event()

        def _stop(signum: int, frame: Any) -> None:
            logger.info("Nhận tín hiệu dừng (%s)", signum)
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

        try:
            while not stop_event.wait(1):
                pass
        finally:
            logger.info("Đang dừng scheduler...")
            scheduler.shutdown(wait=False)
            self.state.close()
            logger.info("Đã dừng")

    def run_once(self) -> None:
        self.bootstrap(send_startup=False, check_digest=False)
        self.job_announcements()
        self.job_tkb()
        self.job_exams()
        if self.courses:
            self.courses.job()
        logger.info("Chạy một lượt hoàn tất")
