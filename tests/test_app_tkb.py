from __future__ import annotations

from src.app import App
from src.config import Config
from src.models import TkbSession
from src.state import State


def tkb_item(room: str) -> dict:
    return {
        "maLop": "IT001.R124",
        "tenMonHoc": "Nhập môn lập trình",
        "ngay": "2026-11-09",
        "tietBatDau": 6,
        "tietKetThuc": 8,
        "phong": room,
        "tenGiangVien": "Mai Tiến Dũng",
        "lecturerTitle": "TS",
        "trangThai": "normal",
    }


class FakePortal:
    def __init__(self) -> None:
        self.sessions: dict[str, list[dict]] = {}

    def ensure_session(self) -> None:
        return

    def get_tkb_meta(self):
        return {"hoc_ky": 1, "nam_hoc": 2026, "year_id": 47}

    def get_tkb_week(self, week: str, meta=None):
        return {
            "hocKy": 1,
            "namHoc": 2026,
            "tiets": list(self.sessions.get(week, [])),
            "ngayLe": [],
            "startOfWeek": week,
        }

    def get_registration(self):
        return {"hocKy": 1, "yearId": 47, "rows": [{"code": "IT001.R124"}]}


class FakePublic:
    def get_announcements(self, page=1, limit=50):
        return {"items": [], "total": 0, "page": page, "limit": limit}


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, text, *, event_key=None, kind="general", payload=None):
        self.sent.append((event_key or "", text))
        return True

    def send_announcements(self, announcements, student_codes):
        return


def test_tkb_change_is_sent_once(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        config = Config(
            uit_username="x",
            uit_password="x",
            telegram_bot_token="x",
            telegram_chat_id="x",
            state_db_path=tmp_path / "state.db",
            tkb_poll_next_week=False,
            tkb_confirm_removals=False,
            confirm_removal_delay_seconds=0,
        )
        portal = FakePortal()
        notifier = FakeNotifier()
        app = App(config, state, portal, FakePublic(), notifier)  # type: ignore[arg-type]

        week = app.current_week_start()
        old = TkbSession(
            date="2026-11-09",
            class_code="IT001.R124",
            subject="Nhập môn lập trình",
            start_period=6,
            end_period=8,
            room="B1.18",
            lecturer="TS Mai Tiến Dũng",
            status="normal",
            week_start=week,
        )
        state.save_week(week, [old])
        portal.sessions[week] = [tkb_item("B2.12")]

        app._poll_tkb()
        assert len(notifier.sent) == 1
        event_key, text = notifier.sent[0]
        assert event_key.startswith("tkb:week:")
        assert "B1.18" in text and "B2.12" in text
        assert state.get_week(week)[0].room == "B2.12"

        notifier.sent.clear()
        app._poll_tkb()
        assert notifier.sent == []
    finally:
        state.close()
