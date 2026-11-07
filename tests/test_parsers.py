from __future__ import annotations

from src.detector import diff_exams, diff_tkb, filter_relevant_announcements
from src.models import Announcement, TkbSession
from src.parsers import (
    class_code_matches,
    extract_class_code,
    parse_announcements_payload,
    parse_exam_date,
    parse_exam_items,
    parse_tkb_payload,
)


def test_parse_tkb_payload():
    data = {
        "startOfWeek": "2026-11-09",
        "tiets": [
            {
                "maLop": "IT001.R124",
                "tenMonHoc": "Nhập môn lập trình",
                "ngay": "2026-11-09",
                "tietBatDau": 6,
                "tietKetThuc": 8,
                "phong": "B1.18",
                "tenGiangVien": "Mai Tiến Dũng",
                "lecturerTitle": "TS",
                "trangThai": "normal",
            }
        ],
    }
    sessions = parse_tkb_payload(data, "2026-11-09")
    assert len(sessions) == 1
    session = sessions[0]
    assert session.class_code == "IT001.R124"
    assert session.date == "2026-11-09"
    assert session.start_period == 6
    assert session.lecturer == "TS Mai Tiến Dũng"
    assert session.key == "2026-11-09|IT001.R124|6-8"


def test_parse_exam_date_and_items():
    assert parse_exam_date("06/11/2026") == ("2026-11-06", "06/11/2026")
    assert parse_exam_date("2026-11-06") is None
    items = [
        {
            "id": 1,
            "subject_code": "MA006",
            "subject_name": "Giải tích",
            "section_class_code": "MA006.R125",
            "format": "tl",
            "examination": "midterm",
            "shift": "2",
            "start_time": "09:30",
            "end_time": "11:30",
            "weekday": "Thứ 6",
            "date": "06/11/2026",
            "room": "B3.10",
            "absent": "Không",
            "note": "",
        }
    ]
    exams = parse_exam_items(items, year=2026, semester="semester_1", examination="midterm")
    assert len(exams) == 1
    assert exams[0].date == "2026-11-06"
    assert exams[0].class_code == "MA006.R125"
    assert exams[0].exam_key == "midterm|MA006|MA006.R125|2026-11-06|2|B3.10"


def test_parse_announcements_and_class_code():
    payload = {
        "items": [
            {
                "id": 123,
                "slug": "abc",
                "title": "Thông báo nghỉ lớp Nhập môn lập trình (IT001.R124) ngày 12/11/2026",
                "excerpt": "",
                "category_slugs": ["thong-bao-nghi-bu"],
                "category_names": ["Thông báo Nghỉ - Bù"],
                "publish_date": "2026-11-07T09:19:14Z",
                "department_name": "Khoa KHMT",
            }
        ]
    }
    items = parse_announcements_payload(payload)
    assert items[0].url == "https://portal.uit.edu.vn/bai-viet/abc"
    assert extract_class_code(items[0].title) == "IT001.R124"
    assert extract_class_code("Thông báo nghỉ lớp (it001.r124) ngày 12/11/2026") == "IT001.R124"
    assert class_code_matches("IT001.R124", {"IT001.R124.1"})
    assert not class_code_matches("IT001.R122", {"IT001.R124"})


def test_diff_tkb_and_filter_relevant():
    old = [
        TkbSession(
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
    ]
    new = [
        TkbSession(
            date="2026-11-09",
            class_code="IT001.R124",
            subject="Nhập môn lập trình",
            start_period=6,
            end_period=8,
            room="B2.12",
            lecturer="TS Mai Tiến Dũng",
            status="normal",
            week_start="2026-11-09",
        )
    ]
    diff = diff_tkb(old, new, min_date="2026-11-07")
    assert len(diff.changed) == 1
    assert diff.changed[0][0].room == "B1.18"
    assert diff.changed[0][1].room == "B2.12"

    relevant_item = Announcement(
        id=1,
        slug="a",
        title="Thông báo nghỉ lớp (IT001.R124) ngày 09/11/2026",
        excerpt="",
        category_slugs=["thong-bao-nghi-bu"],
        category_names=["Thông báo Nghỉ - Bù"],
        publish_date="",
        department_name="",
        url="https://portal.uit.edu.vn/bai-viet/a",
    )
    irrelevant_item = Announcement(
        id=2,
        slug="b",
        title="Thông báo nghỉ lớp (IT001.R122) ngày 09/11/2026",
        excerpt="",
        category_slugs=["thong-bao-nghi-bu"],
        category_names=["Thông báo Nghỉ - Bù"],
        publish_date="",
        department_name="",
        url="https://portal.uit.edu.vn/bai-viet/b",
    )
    general_item = Announcement(
        id=3,
        slug="c",
        title="Thông báo học phí học kỳ 1",
        excerpt="",
        category_slugs=["thong-bao"],
        category_names=["Thông báo"],
        publish_date="",
        department_name="",
        url="https://portal.uit.edu.vn/bai-viet/c",
    )
    filtered = filter_relevant_announcements(
        [relevant_item, irrelevant_item, general_item], {"IT001.R124"}, "relevant"
    )
    assert [item.id for item in filtered] == [1, 3]


def test_exam_room_change_is_diffed_as_changed():
    base = {
        "subject_code": "MA006",
        "subject_name": "Giải tích",
        "section_class_code": "MA006.R125",
        "format": "tl",
        "examination": "midterm",
        "shift": "2",
        "start_time": "09:30",
        "end_time": "11:30",
        "weekday": "Thứ 6",
        "date": "06/11/2026",
        "absent": "Không",
        "note": "",
    }
    old = parse_exam_items(
        [{**base, "room": "B3.10"}],
        year=2026,
        semester="semester_1",
        examination="midterm",
    )
    new = parse_exam_items(
        [{**base, "room": "B3.12"}],
        year=2026,
        semester="semester_1",
        examination="midterm",
    )
    diff = diff_exams(old, new, min_date="2026-11-01")
    assert len(diff.changed) == 1
    assert diff.changed[0][0].room == "B3.10"
    assert diff.changed[0][1].room == "B3.12"
