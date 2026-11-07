from __future__ import annotations

from src.models import Announcement, TkbSession
from src.state import State


def make_ann(ann_id: int) -> Announcement:
    return Announcement(
        id=ann_id,
        slug=f"slug-{ann_id}",
        title=f"Thông báo {ann_id}",
        excerpt="",
        category_slugs=["thong-bao"],
        category_names=["Thông báo"],
        publish_date="2026-11-07T00:00:00Z",
        department_name="",
        url=f"https://portal.uit.edu.vn/bai-viet/slug-{ann_id}",
    )


def test_announcement_processed_and_dedup(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.save_announcement(make_ann(1))
        assert not state.announcement_is_processed(1)
        state.mark_announcement_processed(1)
        assert state.announcement_is_processed(1)

        key = "announcement:1"
        assert state.get_notification_status(key) is None
        assert state.ensure_notification(key, "announcement", {}) == "pending"
        state.mark_notification_sent(key)
        assert state.get_notification_status(key) == "sent"
    finally:
        state.close()


def test_week_roundtrip(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        session = TkbSession(
            date="2026-11-09",
            class_code="IT001.R124",
            subject="Nhập môn lập trình",
            start_period=6,
            end_period=8,
            room="B1.18",
            lecturer="TS Mai Tiến Dũng",
            status="normal",
            week_start="2026-11-09",
        )
        state.save_week("2026-11-09", [session])
        loaded = state.get_week("2026-11-09")
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].key == session.key
        assert state.get_week("2026-11-10") is None
    finally:
        state.close()
