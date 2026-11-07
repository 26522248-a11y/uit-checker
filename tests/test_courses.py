from __future__ import annotations

from src.config import Config
from src.courses_checker import CoursesChecker
from src.courses_client import parse_courses_cfg
from src.courses_formatter import format_courses_message, format_courses_notification
from src.state import State


def notification(item_id: int, subject: str = "Thông báo mới") -> dict:
    return {
        "id": item_id,
        "subject": subject,
        "smallmessage": subject,
        "contexturl": "https://courses.uit.edu.vn/mod/assign/view.php?id=1",
        "contexturlname": "Bài tập 1",
        "timecreated": 1793864013,
        "component": "mod_assign",
        "eventtype": "assign_notification",
    }


class FakeClient:
    def __init__(self) -> None:
        self.user_id = 24513
        self.consecutive_auth_failures = 0
        self.notifications: list[dict] = []
        self.conversations: list[dict] = []

    def login(self, force: bool = False) -> None:
        return

    def ensure_session(self, force: bool = False) -> None:
        return

    def get_notifications(self, limit: int = 50, offset: int = 0) -> dict:
        return {
            "notifications": list(self.notifications),
            "unreadcount": len(self.notifications),
        }

    def get_conversations(self, limit: int = 50) -> dict:
        return {"conversations": list(self.conversations)}

    def get_conversation_messages(self, conversation_id: int, limit: int = 10) -> dict:
        for conversation in self.conversations:
            if int(conversation.get("id")) == int(conversation_id):
                return {"messages": list(conversation.get("messages") or [])}
        return {"messages": []}


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, text, *, event_key=None, kind="general", payload=None):
        self.sent.append((event_key or "", text))
        return True


def test_parse_courses_cfg():
    html = (
        '<script>M.cfg = {"sesskey":"abc123","sessiontimeout":"28800",'
        '"userId":24513,"usertimezone":"Asia/Ho_Chi_Minh"};</script>'
    )
    cfg = parse_courses_cfg(html)
    assert cfg == {"sesskey": "abc123", "user_id": 24513, "sessiontimeout": 28800}


def test_courses_formatters():
    text = format_courses_notification(notification(1, "Bạn đã nộp bài"), Config.load().tz)
    assert "COURSES UIT" in text
    assert "Bạn đã nộp bài" in text
    assert "courses.uit.edu.vn" in text

    message = {
        "id": 1651,
        "useridfrom": 6928,
        "text": "<p>Chào các em,<br>T5 học bình thường</p>",
        "timecreated": 1790073886,
    }
    conversation = {"id": 4448, "name": "", "members": [{"id": 6928, "fullname": "Dương Ngọc Hảo"}]}
    msg_text = format_courses_message(message, conversation, "Dương Ngọc Hảo", Config.load().tz)
    assert "TIN NHẮN MỚI" in msg_text
    assert "Dương Ngọc Hảo" in msg_text
    assert "T5 học bình thường" in msg_text
    assert "conversationid=4448" in msg_text


def test_courses_checker_sends_once(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        config = Config(
            uit_username="x",
            uit_password="x",
            telegram_bot_token="x",
            telegram_chat_id="x",
            state_db_path=tmp_path / "state.db",
            courses_enabled=True,
            courses_include_messages=False,
        )
        notifier = FakeNotifier()
        checker = CoursesChecker(config, state, notifier)  # type: ignore[arg-type]
        client = FakeClient()
        checker.client = client  # type: ignore[assignment]

        client.notifications = [notification(1, "Thông báo cũ")]
        checker.bootstrap()
        assert notifier.sent == []
        assert state.courses_notification_is_processed(1)

        client.notifications = [notification(2, "Thông báo mới"), notification(1, "Thông báo cũ")]
        checker.poll()
        assert len(notifier.sent) == 1
        event_key, text = notifier.sent[0]
        assert event_key == "courses:noti:2"
        assert "Thông báo mới" in text
        assert state.courses_notification_is_processed(2)

        notifier.sent.clear()
        checker.poll()
        assert notifier.sent == []
    finally:
        state.close()
