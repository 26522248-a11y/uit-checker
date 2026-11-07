from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
COURSES_URL = "https://courses.uit.edu.vn"
NOTIFICATIONS_URL = COURSES_URL + "/message/output/popup/notifications.php"

COMPONENT_LABELS = {
    "mod_assign": "Bài tập",
    "mod_forum": "Diễn đàn",
    "mod_quiz": "Bài kiểm tra",
    "mod_lesson": "Bài học",
    "mod_choice": "Lựa chọn",
    "mod_feedback": "Khảo sát",
    "mod_workshop": "Workshop",
    "moodle": "Hệ thống",
    "core": "Hệ thống",
}


def h(value: object) -> str:
    return escape(str(value or ""), quote=False)


def fmt_epoch_local(epoch: int | str | None, tz: ZoneInfo = DEFAULT_TZ) -> str:
    try:
        timestamp = int(epoch or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz).strftime("%H:%M, %d/%m/%Y")


def strip_html(value: str | None) -> str:
    import re

    text = value or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_module

    text = html_module.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def component_label(component: str, eventtype: str = "") -> str:
    if component in COMPONENT_LABELS:
        return COMPONENT_LABELS[component]
    if eventtype.startswith("assign_"):
        return "Bài tập"
    if eventtype in {"posts", "discussion"}:
        return "Diễn đàn"
    return component or "Thông báo"


def notification_link(notification: dict[str, Any]) -> str:
    return notification.get("contexturl") or NOTIFICATIONS_URL


def format_courses_notification(
    notification: dict[str, Any], tz: ZoneInfo = DEFAULT_TZ
) -> str:
    subject = strip_html(notification.get("subject")) or strip_html(
        notification.get("smallmessage")
    )
    small = strip_html(notification.get("smallmessage"))
    context_name = strip_html(notification.get("contexturlname"))
    label = component_label(
        notification.get("component") or "", notification.get("eventtype") or ""
    )
    created = fmt_epoch_local(notification.get("timecreated"), tz)

    parts = ["🔔 <b>THÔNG BÁO MỚI — COURSES UIT</b>"]
    if subject:
        parts.append(f"<b>{h(subject)}</b>")
    if small and small != subject:
        parts.append(h(small[:800]))
    meta = [f"📚 {h(label)}"]
    if context_name and context_name != subject:
        meta.append(h(context_name))
    if created:
        meta.append(f"🕐 {h(created)}")
    parts.append(" · ".join(meta))
    parts.append(f"🔗 {h(notification_link(notification))}")
    return "\n\n".join(parts)


def format_courses_message(
    message: dict[str, Any],
    conversation: dict[str, Any],
    sender_name: str,
    tz: ZoneInfo = DEFAULT_TZ,
) -> str:
    text = strip_html(message.get("text")) or "(Tin nhắn không có nội dung văn bản)"
    if len(text) > 800:
        text = text[:800] + "…"
    created = fmt_epoch_local(message.get("timecreated"), tz)
    conversation_id = conversation.get("id")
    link = f"{COURSES_URL}/message/index.php?conversationid={conversation_id}"
    conversation_name = strip_html(conversation.get("name"))
    header = "💬 <b>TIN NHẮN MỚI — COURSES UIT</b>"
    parts = [header]
    if conversation_name:
        parts.append(f"👥 {h(conversation_name)}")
    parts.append(f"Từ: <b>{h(sender_name or 'Ai đó')}</b>")
    parts.append(h(text))
    if created:
        parts.append(f"🕐 {h(created)}")
    parts.append(f"🔗 {h(link)}")
    return "\n\n".join(parts)


def format_courses_auth_alert(error: str, attempts: int, tz: ZoneInfo = DEFAULT_TZ) -> str:
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    return (
        "⚠️ <b>COURSES UIT CHECKER</b>\n\n"
        f"Không đăng nhập được Website môn học sau {attempts} lần thử.\n"
        f"Lỗi: {h(error[:500])}\n\n"
        f"Thời gian: {now}"
    )


def format_courses_portal_alert(
    kind: str, error: str, failures: int, tz: ZoneInfo = DEFAULT_TZ
) -> str:
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    return (
        "⚠️ <b>COURSES UIT CHECKER</b>\n\n"
        f"Không lấy được dữ liệu {h(kind)} liên tiếp {failures} lần.\n"
        f"Lỗi: {h(error[:500])}\n\n"
        f"Thời gian: {now}"
    )


def format_courses_startup_line(
    *,
    ok: bool,
    notification_count: int | None,
    message_count: int | None,
) -> str:
    if not ok:
        return "Courses UIT: ⚠️ chưa đăng nhập (sẽ thử lại)"
    if notification_count is None or message_count is None:
        return "Courses UIT: ✅ đã kết nối"
    return (
        "Courses UIT: ✅ đã kết nối · "
        f"{notification_count} thông báo / {message_count} tin nhắn baseline"
    )
