from __future__ import annotations

from src.app import App
from src.config import Config
from src.portal_client import PublicPortalClient
from src.state import State


def ann_payload(ann_id: int, title: str, categories: list[str] | None = None) -> dict:
    return {
        "id": ann_id,
        "slug": f"slug-{ann_id}",
        "title": title,
        "excerpt": "",
        "category_slugs": categories or ["thong-bao"],
        "category_names": ["Thông báo"],
        "publish_date": "2026-11-07T00:00:00Z",
        "department_name": "",
    }


class FakePublic:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def get_announcements(self, page: int = 1, limit: int = 50) -> dict:
        if page > 1:
            return {"items": [], "total": len(self.items), "page": page, "limit": limit}
        return {
            "items": list(self.items),
            "total": len(self.items),
            "page": page,
            "limit": limit,
        }


class FakePortal:
    def __init__(self, state: State) -> None:
        self.state = state

    def get_registration(self) -> dict:
        self.state.save_class_codes(["IT001.R124"])
        return {
            "hocKy": 1,
            "yearId": 47,
            "rows": [{"code": "IT001.R124"}],
        }


class FakeNotifier:
    def __init__(self, state: State) -> None:
        self.state = state
        self.sent: list[int] = []

    def send_announcements(self, announcements, student_codes):
        for ann in announcements:
            key = f"announcement:{ann.id}"
            self.state.ensure_notification(key, "announcement", {})
            self.state.mark_notification_sent(key)
            self.sent.append(ann.id)


def test_relevant_filter_and_no_duplicate(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        config = Config(
            uit_username="x",
            uit_password="x",
            telegram_bot_token="x",
            telegram_chat_id="x",
            state_db_path=tmp_path / "state.db",
            announcement_mode="relevant",
            announcement_page_size=50,
        )
        public = FakePublic()
        portal = FakePortal(state)
        notifier = FakeNotifier(state)
        app = App(config, state, portal, public, notifier)  # type: ignore[arg-type]

        public.items = [ann_payload(1, "Thông báo chung")]
        state.set_meta("bootstrapped", "1")
        app._bootstrap_announcements()
        assert state.announcement_is_processed(1)
        assert notifier.sent == []

        public.items = [
            ann_payload(2, "Thông báo nghỉ lớp (IT001.R122) ngày 09/11/2026", ["thong-bao-nghi-bu"]),
            ann_payload(3, "Thông báo nghỉ lớp (IT001.R124) ngày 09/11/2026", ["thong-bao-nghi-bu"]),
            ann_payload(4, "Thông báo học phí học kỳ 1"),
        ]
        app._poll_announcements()
        assert notifier.sent == [3, 4]
        assert all(state.announcement_is_processed(i) for i in (1, 2, 3, 4))

        notifier.sent.clear()
        app._poll_announcements()
        assert notifier.sent == []
    finally:
        state.close()
