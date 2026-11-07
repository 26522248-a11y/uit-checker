from __future__ import annotations

import argparse
import logging
import sys

from .app import App
from .config import Config, redacted_config_summary
from .lock import AlreadyRunning, SingleInstance
from .logging_setup import setup_logging
from .notifier import TelegramNotifier
from .portal_client import PortalClient, PublicPortalClient
from .state import State

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UIT Portal Checker — theo dõi TKB, lịch thi, thông báo và gửi Telegram"
    )
    parser.add_argument("--once", action="store_true", help="Chạy một lượt rồi thoát")
    parser.add_argument("--dry-run", action="store_true", help="Không gửi Telegram, chỉ log")
    parser.add_argument("--test-telegram", action="store_true", help="Gửi tin test Telegram")
    parser.add_argument("--check-auth", action="store_true", help="Kiểm tra đăng nhập portal")
    parser.add_argument("--digest-now", action="store_true", help="Gửi digest ngay")
    parser.add_argument(
        "--check-courses", action="store_true", help="Kiểm tra đăng nhập Courses UIT"
    )
    parser.add_argument(
        "--courses-once", action="store_true", help="Poll Courses UIT một lượt"
    )
    parser.add_argument("--verbose", action="store_true", help="Log DEBUG")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = Config.load()
    except Exception as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        config.dry_run = True
    if args.verbose:
        config.log_level = "DEBUG"

    setup_logging(
        config.log_level,
        config.logs_dir,
        [config.uit_password, config.telegram_bot_token],
        config.tz_name,
    )
    logger.info("Cấu hình: %s", redacted_config_summary(config))

    long_running = not (
        args.once
        or args.test_telegram
        or args.check_auth
        or args.digest_now
        or args.check_courses
        or args.courses_once
    )
    instance_lock: SingleInstance | None = None
    if long_running:
        instance_lock = SingleInstance(config.data_dir)
        try:
            instance_lock.acquire()
        except AlreadyRunning as exc:
            logger.error("%s", exc)
            return 0

    state = State(config.state_db_path)
    portal = PortalClient(config, state)
    public = PublicPortalClient(config)
    notifier = TelegramNotifier(config, state)
    app = App(config, state, portal, public, notifier)

    try:
        if args.test_telegram:
            ok = notifier.send_test()
            return 0 if ok else 1

        if args.check_auth:
            try:
                portal.login(force=True)
            except Exception as exc:
                logger.error("Kiểm tra đăng nhập thất bại: %s", exc)
                return 1
            logger.info("Đăng nhập portal thành công")
            return 0

        if args.digest_now:
            ok = app.send_digest(force=True, catchup=False)
            return 0 if ok else 1

        if args.check_courses:
            if not app.courses:
                logger.error("COURSES_ENABLED=false, không có CoursesChecker")
                return 1
            try:
                app.courses.client.login()
                notifications = app.courses.client.get_notifications(5, 0)
                conversations = app.courses.client.get_conversations(5)
            except Exception as exc:
                logger.error("Kiểm tra Courses thất bại: %s", exc)
                return 1
            logger.info(
                "Courses OK: %s thông báo gần nhất, unread=%s, %s hội thoại",
                len(notifications.get("notifications") or []),
                notifications.get("unreadcount"),
                len(conversations.get("conversations") or []),
            )
            return 0

        if args.courses_once:
            if not app.courses:
                logger.error("COURSES_ENABLED=false, không có CoursesChecker")
                return 1
            app.courses.poll()
            return 0

        if args.once:
            app.run_once()
            return 0

        app.run_forever()
        return 0
    except KeyboardInterrupt:
        logger.info("Dừng bởi người dùng")
        return 0
    except Exception:
        logger.exception("Lỗi không mong đợi")
        return 1
    finally:
        state.close()
        if instance_lock is not None:
            instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
