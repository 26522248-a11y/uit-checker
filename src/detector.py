from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import Announcement, Exam, TkbSession
from .parsers import class_code_matches, extract_class_code


@dataclass(slots=True)
class TkbDiff:
    added: list[TkbSession] = field(default_factory=list)
    removed: list[TkbSession] = field(default_factory=list)
    changed: list[tuple[TkbSession, TkbSession]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.changed

    @property
    def has_removals_or_changes(self) -> bool:
        return bool(self.removed or self.changed)


@dataclass(slots=True)
class ExamDiff:
    added: list[Exam] = field(default_factory=list)
    removed: list[Exam] = field(default_factory=list)
    changed: list[tuple[Exam, Exam]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.changed

    @property
    def has_removals_or_changes(self) -> bool:
        return bool(self.removed or self.changed)


def diff_tkb(
    old: Iterable[TkbSession],
    new: Iterable[TkbSession],
    min_date: str | None = None,
) -> TkbDiff:
    old_map = {session.key: session for session in old}
    new_map = {session.key: session for session in new}
    result = TkbDiff()

    for key, session in new_map.items():
        if key not in old_map and (min_date is None or session.date >= min_date):
            result.added.append(session)

    for key, session in old_map.items():
        if key not in new_map and (min_date is None or session.date >= min_date):
            result.removed.append(session)

    for key in old_map.keys() & new_map.keys():
        old_session = old_map[key]
        new_session = new_map[key]
        if old_session.comparable() != new_session.comparable():
            if min_date is None or new_session.date >= min_date:
                result.changed.append((old_session, new_session))

    result.added.sort(key=lambda item: (item.date, item.start_period, item.class_code))
    result.removed.sort(key=lambda item: (item.date, item.start_period, item.class_code))
    result.changed.sort(key=lambda pair: (pair[1].date, pair[1].start_period, pair[1].class_code))
    return result


def diff_exams(
    old: Iterable[Exam],
    new: Iterable[Exam],
    min_date: str | None = None,
) -> ExamDiff:
    old_map = {exam.match_key: exam for exam in old}
    new_map = {exam.match_key: exam for exam in new}
    result = ExamDiff()

    for key, exam in new_map.items():
        if key not in old_map and (min_date is None or exam.date >= min_date):
            result.added.append(exam)

    for key, exam in old_map.items():
        if key not in new_map and (min_date is None or exam.date >= min_date):
            result.removed.append(exam)

    for key in old_map.keys() & new_map.keys():
        old_exam = old_map[key]
        new_exam = new_map[key]
        if old_exam.comparable() != new_exam.comparable():
            if min_date is None or new_exam.date >= min_date:
                result.changed.append((old_exam, new_exam))

    result.added.sort(key=lambda item: (item.date, item.start_time, item.subject_code))
    result.removed.sort(key=lambda item: (item.date, item.start_time, item.subject_code))
    result.changed.sort(key=lambda pair: (pair[1].date, pair[1].start_time, pair[1].subject_code))
    return result


def is_class_specific(announcement: Announcement) -> bool:
    return "thong-bao-nghi-bu" in (announcement.category_slugs or [])


def announcement_affects_student(
    announcement: Announcement, student_codes: set[str]
) -> bool:
    if not is_class_specific(announcement):
        return False
    code = extract_class_code(announcement.title)
    if not code:
        return False
    return class_code_matches(code, student_codes)


def filter_relevant_announcements(
    announcements: Iterable[Announcement],
    student_codes: set[str],
    mode: str,
) -> list[Announcement]:
    if mode == "all":
        return list(announcements)

    result: list[Announcement] = []
    for announcement in announcements:
        if not is_class_specific(announcement):
            result.append(announcement)
            continue
        code = extract_class_code(announcement.title)
        if not code:
            # A cancellation notice without a class code is treated as general.
            result.append(announcement)
            continue
        if class_code_matches(code, student_codes):
            result.append(announcement)
    return result
