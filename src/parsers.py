from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from .models import Announcement, Exam, TkbSession, normalize_text

logger = logging.getLogger(__name__)

EXAM_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
CLASS_CODE_RE = re.compile(
    r"\(([A-Z]{2,}\d*(?:\.[A-Z0-9]+)+)\)", re.IGNORECASE
)
CLASS_CODE_FALLBACK_RE = re.compile(
    r"\b([A-Z]{2,}\d+\.[A-Z0-9.]+)\b", re.IGNORECASE
)


def parse_tkb_payload(data: dict[str, Any], requested_week_start: str) -> list[TkbSession]:
    sessions: list[TkbSession] = []
    week_start = normalize_text(data.get("startOfWeek")) or requested_week_start
    for item in data.get("tiets") or []:
        try:
            session_date = normalize_text(item.get("ngay"))
            class_code = normalize_text(item.get("maLop"))
            start_period = int(item.get("tietBatDau"))
            end_period = int(item.get("tietKetThuc"))
        except (TypeError, ValueError):
            logger.warning("Bỏ qua buổi học thiếu dữ liệu: %s", item)
            continue
        if not session_date or not class_code:
            continue
        lecturer_title = normalize_text(item.get("lecturerTitle"))
        lecturer_name = normalize_text(item.get("tenGiangVien"))
        lecturer = " ".join(part for part in (lecturer_title, lecturer_name) if part).strip()
        subject = normalize_text(item.get("tenMonHoc")) or class_code
        sessions.append(
            TkbSession(
                date=session_date,
                class_code=class_code,
                subject=subject,
                start_period=start_period,
                end_period=end_period,
                room=normalize_text(item.get("phong")),
                lecturer=lecturer,
                status=normalize_text(item.get("trangThai")) or "normal",
                week_start=week_start,
                raw=item,
            )
        )
    # Stable order for diffs/messages
    sessions.sort(key=lambda s: (s.date, s.start_period, s.class_code))
    return sessions


def parse_exam_date(value: str) -> tuple[str, str] | None:
    """Return (YYYY-MM-DD, dd/mm/yyyy) or None."""
    raw = normalize_text(value)
    match = EXAM_DATE_RE.match(raw)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        iso = date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None
    return iso, raw


def parse_exam_items(
    items: list[dict[str, Any]],
    *,
    year: int,
    semester: str,
    examination: str,
) -> list[Exam]:
    parsed: dict[str, Exam] = {}
    for item in items:
        date_pair = parse_exam_date(item.get("date", ""))
        if not date_pair:
            logger.warning("Bỏ qua lịch thi có ngày không hợp lệ: %s", item.get("date"))
            continue
        iso_date, display_date = date_pair
        subject_code = normalize_text(item.get("subject_code"))
        class_code = normalize_text(
            item.get("section_class_code") or item.get("section_class_id")
        )
        shift = normalize_text(item.get("shift"))
        room = normalize_text(item.get("room"))
        exam_key = "|".join(
            [examination, subject_code, class_code, iso_date, shift, room]
        )
        exam = Exam(
            exam_key=exam_key,
            date=iso_date,
            date_display=display_date,
            subject_code=subject_code,
            subject_name=normalize_text(item.get("subject_name")) or subject_code,
            class_code=class_code,
            format=normalize_text(item.get("format")),
            shift=shift,
            start_time=normalize_text(item.get("start_time")),
            end_time=normalize_text(item.get("end_time")),
            weekday=normalize_text(item.get("weekday")),
            room=room,
            absent=normalize_text(item.get("absent")),
            note=normalize_text(item.get("note")),
            examination=normalize_text(item.get("examination")) or examination,
            semester=semester,
            year=year,
            raw=item,
        )
        parsed[exam_key] = exam
    return sorted(parsed.values(), key=lambda e: (e.date, e.start_time, e.subject_code))


def parse_announcements_payload(data: dict[str, Any]) -> list[Announcement]:
    result: list[Announcement] = []
    for item in data.get("items") or []:
        try:
            ann_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        slug = normalize_text(item.get("slug"))
        if not slug:
            continue
        result.append(
            Announcement(
                id=ann_id,
                slug=slug,
                title=normalize_text(item.get("title")),
                excerpt=normalize_text(item.get("excerpt")),
                category_slugs=list(item.get("category_slugs") or []),
                category_names=list(item.get("category_names") or []),
                publish_date=normalize_text(item.get("publish_date")),
                department_name=normalize_text(item.get("department_name")),
                url=f"https://portal.uit.edu.vn/bai-viet/{slug}",
            )
        )
    return result


def extract_class_code(title: str) -> str | None:
    for regex in (CLASS_CODE_RE, CLASS_CODE_FALLBACK_RE):
        match = regex.search(title or "")
        if match:
            return match.group(1).upper()
    return None


def class_code_matches(candidate: str, student_codes: set[str]) -> bool:
    candidate = candidate.upper()
    for code in student_codes:
        code = code.upper()
        if candidate == code:
            return True
        if candidate.startswith(code + ".") or code.startswith(candidate + "."):
            return True
    return False
