from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def normalize_text(value: str | None) -> str:
    return (value or "").replace("\xa0", " ").strip()


@dataclass(slots=True)
class TkbSession:
    date: str  # YYYY-MM-DD
    class_code: str
    subject: str
    start_period: int
    end_period: int
    room: str
    lecturer: str
    status: str
    week_start: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.date}|{self.class_code}|{self.start_period}-{self.end_period}"

    def comparable(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "class_code": self.class_code,
            "subject": self.subject,
            "start_period": self.start_period,
            "end_period": self.end_period,
            "room": self.room,
            "lecturer": self.lecturer,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "class_code": self.class_code,
            "subject": self.subject,
            "start_period": self.start_period,
            "end_period": self.end_period,
            "room": self.room,
            "lecturer": self.lecturer,
            "status": self.status,
            "week_start": self.week_start,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TkbSession":
        return cls(
            date=data["date"],
            class_code=data["class_code"],
            subject=data["subject"],
            start_period=int(data["start_period"]),
            end_period=int(data["end_period"]),
            room=data.get("room", ""),
            lecturer=data.get("lecturer", ""),
            status=data.get("status", "normal"),
            week_start=data.get("week_start", ""),
            raw=data.get("raw") or {},
        )


@dataclass(slots=True)
class Exam:
    exam_key: str
    date: str  # YYYY-MM-DD
    date_display: str  # dd/mm/yyyy
    subject_code: str
    subject_name: str
    class_code: str
    format: str
    shift: str
    start_time: str
    end_time: str
    weekday: str
    room: str
    absent: str
    note: str
    examination: str
    semester: str
    year: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def match_key(self) -> str:
        """Stable identity for diffing; room is excluded so room changes are visible."""
        return "|".join(
            [
                self.examination,
                self.subject_code,
                self.class_code,
                self.date,
                self.shift,
            ]
        )

    def comparable(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "subject_code": self.subject_code,
            "subject_name": self.subject_name,
            "class_code": self.class_code,
            "format": self.format,
            "shift": self.shift,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "weekday": self.weekday,
            "room": self.room,
            "absent": self.absent,
            "note": self.note,
            "examination": self.examination,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "exam_key": self.exam_key,
            "date": self.date,
            "date_display": self.date_display,
            "subject_code": self.subject_code,
            "subject_name": self.subject_name,
            "class_code": self.class_code,
            "format": self.format,
            "shift": self.shift,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "weekday": self.weekday,
            "room": self.room,
            "absent": self.absent,
            "note": self.note,
            "examination": self.examination,
            "semester": self.semester,
            "year": self.year,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Exam":
        return cls(
            exam_key=data["exam_key"],
            date=data["date"],
            date_display=data["date_display"],
            subject_code=data.get("subject_code", ""),
            subject_name=data.get("subject_name", ""),
            class_code=data.get("class_code", ""),
            format=data.get("format", ""),
            shift=data.get("shift", ""),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            weekday=data.get("weekday", ""),
            room=data.get("room", ""),
            absent=data.get("absent", ""),
            note=data.get("note", ""),
            examination=data.get("examination", ""),
            semester=data.get("semester", ""),
            year=int(data.get("year", 0)),
            raw=data.get("raw") or {},
        )


@dataclass(slots=True)
class Announcement:
    id: int
    slug: str
    title: str
    excerpt: str
    category_slugs: list[str]
    category_names: list[str]
    publish_date: str
    department_name: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "excerpt": self.excerpt,
            "category_slugs": self.category_slugs,
            "category_names": self.category_names,
            "publish_date": self.publish_date,
            "department_name": self.department_name,
            "url": self.url,
        }


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except Exception:
        return None
