from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from .detector import ExamDiff, TkbDiff
from .models import Announcement, Exam, TkbSession

DEFAULT_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

PERIOD_START = {
    1: "07:30",
    2: "08:15",
    3: "09:00",
    4: "09:55",
    5: "10:40",
    6: "13:00",
    7: "13:45",
    8: "14:30",
    9: "15:25",
    10: "16:10",
    11: "16:55",
    12: "17:45",
    13: "18:30",
    14: "19:15",
    15: "20:00",
}
PERIOD_END = {
    1: "08:15",
    2: "09:00",
    3: "09:45",
    4: "10:40",
    5: "11:25",
    6: "13:45",
    7: "14:30",
    8: "15:15",
    9: "16:10",
    10: "16:55",
    11: "17:40",
    12: "18:30",
    13: "19:15",
    14: "20:00",
    15: "20:45",
}
WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

EXAM_FORMAT_NAMES = {
    "essay": "Tự luận",
    "tu_luan": "Tự luận",
    "tl": "Tự luận",
    "tn_tl": "Tự luận",
    "multiple_choice": "Trắc nghiệm",
    "trac_nghiem": "Trắc nghiệm",
    "mcq": "Trắc nghiệm",
    "tn": "Trắc nghiệm",
    "oral": "Vấn đáp",
    "van_dap": "Vấn đáp",
    "qna": "Vấn đáp",
    "practice": "Thực hành",
    "thuc_hanh": "Thực hành",
    "practical": "Thực hành",
    "th": "Thực hành",
    "project": "Tiểu luận / ĐATN",
    "tieu_luan": "Tiểu luận / ĐATN",
    "do_an": "Tiểu luận / ĐATN",
    "da": "Tiểu luận / ĐATN",
    "datn": "Tiểu luận / ĐATN",
}


def h(value: object) -> str:
    return escape(str(value or ""), quote=False)


def fmt_date_vi(iso_date: str) -> str:
    try:
        parsed = date.fromisoformat(iso_date)
    except ValueError:
        return iso_date
    return f"{WEEKDAYS[parsed.weekday()]}, {parsed.strftime('%d/%m/%Y')}"


def fmt_date_short(iso_date: str) -> str:
    try:
        return date.fromisoformat(iso_date).strftime("%d/%m/%Y")
    except ValueError:
        return iso_date


def fmt_period_range(start: int, end: int) -> str:
    start_time = PERIOD_START.get(int(start), "")
    end_time = PERIOD_END.get(int(end), "")
    if start_time and end_time:
        return f"{start_time}–{end_time}"
    return ""


def fmt_datetime_local(iso_value: str, tz: ZoneInfo = DEFAULT_TZ) -> str:
    raw = (iso_value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(tz).strftime("%H:%M, %d/%m/%Y")
    except ValueError:
        return raw


def exam_format_name(code: str) -> str:
    raw = (code or "").strip().lower()
    return EXAM_FORMAT_NAMES.get(raw, code or "—")


def _lecturer_line(session: TkbSession) -> str:
    parts = [f"📍 {h(session.room)}"] if session.room else []
    if session.lecturer:
        parts.append(f"GV: {h(session.lecturer)}")
    return " · ".join(parts)


def _session_block(session: TkbSession) -> str:
    time_range = fmt_period_range(session.start_period, session.end_period)
    first = f"🕐 <b>{h(time_range)}</b> · Tiết {session.start_period}–{session.end_period}"
    second = f"📘 <b>{h(session.class_code)}</b> – {h(session.subject)}"
    third = _lecturer_line(session)
    lines = [first, second]
    if third:
        lines.append(third)
    if session.status and session.status.lower() != "normal":
        lines.append(f"⚠️ Trạng thái: {h(session.status)}")
    return "\n".join(lines)


def _exam_block(exam: Exam) -> str:
    time_range = f"{exam.start_time}–{exam.end_time}" if exam.start_time or exam.end_time else "—"
    lines = [
        f"🕐 <b>{h(time_range)}</b> · {h(exam.weekday)}, {h(exam.date_display)}",
        f"📘 <b>{h(exam.subject_code)}</b> – {h(exam.subject_name)}",
    ]
    details = []
    if exam.room:
        details.append(f"📍 {h(exam.room)}")
    if exam.shift:
        details.append(f"Ca {h(exam.shift)}")
    details.append(h(exam_format_name(exam.format)))
    if exam.absent and exam.absent.lower() not in {"không", "khong", "no"}:
        details.append(f"Vắng: {h(exam.absent)}")
    if details:
        lines.append(" · ".join(details))
    if exam.note:
        lines.append(f"📝 {h(exam.note)}")
    return "\n".join(lines)


def format_digest(
    sessions: Iterable[TkbSession],
    exams: Iterable[Exam],
    today_iso: str,
    *,
    send_empty: bool = True,
    catchup: bool = False,
    stale_warning: bool = False,
    tkb_warning: bool = False,
    exam_warning: bool = False,
    tz: ZoneInfo = DEFAULT_TZ,
) -> str:
    sessions = sorted(sessions, key=lambda s: (s.start_period, s.class_code))
    exams = sorted(exams, key=lambda e: (e.start_time, e.subject_code))
    has_schedule = bool(sessions or exams)

    if not has_schedule and not send_empty:
        return ""

    parts: list[str] = []
    if catchup:
        parts.append("⏰ <i>Tin gửi bù cho digest 06:00.</i>")
    if stale_warning:
        parts.append("⚠️ <i>Dữ liệu TKB có thể chưa được cập nhật mới nhất.</i>")
    if tkb_warning:
        parts.append("⚠️ <i>Không tải được TKB hôm nay.</i>")

    if not has_schedule:
        parts.append("☀️ <b>HÔM NAY KHÔNG CÓ LỊCH</b>")
        parts.append(f"{fmt_date_vi(today_iso)}\n\nKhông có lớp học hoặc lịch thi nào trong ngày.\nChúc bạn một ngày tốt lành!")
    else:
        if sessions:
            parts.append(f"📅 <b>LỊCH HỌC HÔM NAY</b>\n{fmt_date_vi(today_iso)}")
            parts.append("\n\n".join(_session_block(session) for session in sessions))
        if exams:
            parts.append("📝 <b>LỊCH THI HÔM NAY</b>")
            parts.append("\n\n".join(_exam_block(exam) for exam in exams))

    if exam_warning:
        parts.append("⚠️ <i>Không kiểm tra được lịch thi hôm nay.</i>")

    links = []
    if sessions:
        links.append("🔗 https://portal.uit.edu.vn/sinh-vien/tkb")
    if exams:
        links.append("🔗 https://portal.uit.edu.vn/sinh-vien/lich-thi")
    if links:
        parts.append("\n".join(links))

    return "\n\n".join(parts).strip()


def format_announcement(announcement: Announcement, affects_student: bool) -> str:
    header = (
        "⚠️ <b>THÔNG BÁO ẢNH HƯỞNG LỚP CỦA BẠN</b>"
        if affects_student
        else "🔔 <b>THÔNG BÁO MỚI</b>"
    )
    parts = [header, f"<b>{h(announcement.title)}</b>"]
    if announcement.excerpt:
        excerpt = announcement.excerpt[:600]
        parts.append(h(excerpt))
    meta = []
    if announcement.category_names:
        meta.append("🏷 " + h(" · ".join(announcement.category_names)))
    if announcement.department_name:
        meta.append("🏢 " + h(announcement.department_name))
    published = fmt_datetime_local(announcement.publish_date)
    if published:
        meta.append("🕐 " + h(published))
    if meta:
        parts.append("\n".join(meta))
    parts.append(f"🔗 {h(announcement.url)}")
    return "\n\n".join(parts)


def format_announcement_batch(announcements: list[Announcement]) -> str:
    header = f"🔔 <b>{len(announcements)} THÔNG BÁO MỚI</b>"
    lines = [header]
    shown = announcements[:10]
    for index, announcement in enumerate(shown, start=1):
        lines.append(f"{index}. <a href=\"{h(announcement.url)}\">{h(announcement.title)}</a>")
    if len(announcements) > len(shown):
        lines.append(f"… và {len(announcements) - len(shown)} thông báo khác.")
    return "\n".join(lines)


def format_tkb_change(diff: TkbDiff) -> str:
    if diff.is_empty:
        return ""
    parts = ["⚠️ <b>THAY ĐỔI THỜI KHÓA BIỂU</b>"]
    for session in diff.added:
        parts.append(
            f"✅ <b>THÊM</b> · {fmt_date_vi(session.date)}\n{_session_block(session)}"
        )
    for session in diff.removed:
        parts.append(
            f"❌ <b>HỦY</b> · {fmt_date_vi(session.date)}\n{_session_block(session)}"
        )
    for old, new in diff.changed:
        changes: list[str] = []
        if old.room != new.room:
            changes.append(f"Phòng: {h(old.room or '—')} → {h(new.room or '—')}")
        if (old.start_period, old.end_period) != (new.start_period, new.end_period):
            changes.append(
                f"Tiết: {old.start_period}–{old.end_period} → {new.start_period}–{new.end_period}"
            )
        if old.subject != new.subject:
            changes.append(f"Môn: {h(old.subject)} → {h(new.subject)}")
        if old.lecturer != new.lecturer:
            changes.append(f"GV: {h(old.lecturer or '—')} → {h(new.lecturer or '—')}")
        if old.status != new.status:
            changes.append(f"Trạng thái: {h(old.status)} → {h(new.status)}")
        if not changes:
            changes.append("Thông tin buổi học đã thay đổi")
        parts.append(
            f"🔄 <b>THAY ĐỔI</b> · {fmt_date_vi(new.date)}\n"
            f"📘 <b>{h(new.class_code)}</b> – {h(new.subject)}\n"
            + "\n".join(changes)
        )
    parts.append("🔗 https://portal.uit.edu.vn/sinh-vien/tkb")
    return "\n\n".join(parts)


def format_tkb_cleared(week_start: str, previous_count: int) -> str:
    return (
        "⚠️ <b>LỊCH TUẦN ĐÃ BỊ XÓA</b>\n\n"
        f"Tuần {fmt_date_short(week_start)} trước đó có {previous_count} buổi học, "
        "hiện không còn buổi nào trên hệ thống.\n\n"
        "🔗 https://portal.uit.edu.vn/sinh-vien/tkb"
    )


def format_exam_change(diff: ExamDiff) -> str:
    if diff.is_empty:
        return ""
    parts: list[str] = []
    for exam in diff.added:
        parts.append(f"📝 <b>LỊCH THI MỚI</b>\n{_exam_block(exam)}")
    for old, new in diff.changed:
        changes: list[str] = []
        if old.date != new.date:
            changes.append(f"Ngày: {h(old.date_display)} → {h(new.date_display)}")
        if (old.start_time, old.end_time) != (new.start_time, new.end_time):
            changes.append(f"Giờ: {h(old.start_time)}–{h(old.end_time)} → {h(new.start_time)}–{h(new.end_time)}")
        if old.room != new.room:
            changes.append(f"Phòng: {h(old.room or '—')} → {h(new.room or '—')}")
        if old.shift != new.shift:
            changes.append(f"Ca: {h(old.shift)} → {h(new.shift)}")
        if old.format != new.format:
            changes.append(f"Hình thức: {h(exam_format_name(old.format))} → {h(exam_format_name(new.format))}")
        if old.note != new.note:
            changes.append(f"Ghi chú: {h(old.note)} → {h(new.note)}")
        if old.absent != new.absent:
            changes.append(f"Vắng: {h(old.absent or '—')} → {h(new.absent or '—')}")
        if not changes:
            changes.append("Thông tin lịch thi đã thay đổi")
        parts.append(
            f"🔄 <b>THAY ĐỔI LỊCH THI</b>\n"
            f"📘 <b>{h(new.subject_code)}</b> – {h(new.subject_name)}\n"
            f"📅 {h(new.weekday)}, {h(new.date_display)}\n"
            + "\n".join(changes)
        )
    for exam in diff.removed:
        parts.append(f"❌ <b>LỊCH THI ĐÃ BỊ XÓA</b>\n{_exam_block(exam)}")
    parts.append("🔗 https://portal.uit.edu.vn/sinh-vien/lich-thi")
    return "\n\n".join(parts)


def format_auth_alert(error: str, attempts: int, tz: ZoneInfo = DEFAULT_TZ) -> str:
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    return (
        "⚠️ <b>UIT CHECKER</b>\n\n"
        f"Không đăng nhập được vào cổng sau {attempts} lần thử.\n"
        f"Lỗi: {h(error[:500])}\n\n"
        f"Thời gian: {now}"
    )


def format_portal_alert(kind: str, error: str, failures: int, tz: ZoneInfo = DEFAULT_TZ) -> str:
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    return (
        "⚠️ <b>UIT CHECKER</b>\n\n"
        f"Không lấy được dữ liệu {h(kind)} liên tiếp {failures} lần.\n"
        f"Lỗi: {h(error[:500])}\n\n"
        f"Thời gian: {now}"
    )


def format_startup(
    *,
    username: str,
    auth_ok: bool,
    announcement_mode: str,
    digest_time: str,
    session_count: int,
    exam_count: int,
    courses_line: str | None = None,
    tz: ZoneInfo = DEFAULT_TZ,
) -> str:
    status = "✅ Đã đăng nhập" if auth_ok else "⚠️ Chưa đăng nhập (chỉ theo dõi thông báo)"
    lines = [
        "✅ <b>UIT CHECKER ĐÃ KHỞI ĐỘNG</b>",
        "",
        f"Tài khoản: <code>{h(username)}</code>",
        "Theo dõi: TKB · Lịch thi · Thông báo portal · Courses UIT",
        f"Chế độ thông báo: <b>{h(announcement_mode)}</b>",
        f"Digest: <b>{h(digest_time)}</b> hằng ngày ({h(tz.key)})",
        f"Trạng thái portal: {status}",
        f"Dữ liệu ghi nhận: {session_count} buổi học · {exam_count} lịch thi",
    ]
    if courses_line:
        lines.append(courses_line)
    return "\n".join(lines)


def format_test_message(username: str) -> str:
    return (
        "✅ <b>UIT CHECKER — TEST TELEGRAM</b>\n\n"
        f"Kết nối Telegram thành công.\nTài khoản: <code>{h(username)}</code>"
    )
