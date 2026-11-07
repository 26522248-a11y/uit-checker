from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import Config
from .portal_client import _build_session
from .state import State

logger = logging.getLogger(__name__)


class CoursesError(RuntimeError):
    pass


class CoursesAuthError(CoursesError):
    pass


def parse_courses_cfg(html: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    sesskey = re.search(r'"sesskey":"([^"]+)"', html or "")
    user_id = re.search(r'"userId":(\d+)', html or "")
    timeout = re.search(r'"sessiontimeout":"?(\d+)"?', html or "")
    if sesskey:
        result["sesskey"] = sesskey.group(1)
    if user_id:
        result["user_id"] = int(user_id.group(1))
    if timeout:
        result["sessiontimeout"] = int(timeout.group(1))
    return result


class CoursesClient:
    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self.base = config.courses_base_url.rstrip("/")
        self._session = _build_session()
        self._session.headers.update({"Accept-Language": "vi,en;q=0.9"})
        self._login_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._sesskey: str | None = None
        self._user_id: int | None = None
        self._logged_in_at: float | None = None
        self._deadline: float | None = None
        self.consecutive_auth_failures = 0
        self.last_auth_error: str | None = None

    # ---------- login ----------

    def _is_login_page(self, response: requests.Response) -> bool:
        text = response.text or ""
        return (
            "/login/index.php" in response.url
            or "kc-form-login" in text
            or "UIT SSO" in text
            and "M.cfg" in text
            and '"userId":0' in text
        )

    def _login_error_text(self, response: requests.Response) -> str:
        try:
            soup = BeautifulSoup(response.text, "lxml")
            for selector in (
                "#input-error",
                "#kc-error-message",
                ".pf-c-form__helper-text.pf-m-error",
                ".kc-feedback-text",
                ".alert-error",
            ):
                node = soup.select_one(selector)
                if node and node.get_text(strip=True):
                    return node.get_text(" ", strip=True)[:400]
            return soup.get_text(" ", strip=True)[:400]
        except Exception:
            return f"HTTP {response.status_code}"

    def _find_oauth_link(self, response: requests.Response) -> str | None:
        soup = BeautifulSoup(response.text, "lxml")
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            if "/auth/oauth2/login.php" in href:
                return urljoin(response.url, href)
        cfg = parse_courses_cfg(response.text)
        if cfg.get("sesskey"):
            return (
                f"{self.base}/auth/oauth2/login.php?id=1&sesskey={cfg['sesskey']}"
            )
        return None

    def login(self, force: bool = False) -> None:
        with self._login_lock:
            now = time.time()
            if (
                not force
                and self._sesskey
                and self._user_id
                and self._deadline
                and now < self._deadline
            ):
                return

            logger.info("Bắt đầu đăng nhập Courses UIT...")
            try:
                response = self._session.get(
                    self.base + "/?redirect=0",
                    timeout=(10, 30),
                    allow_redirects=True,
                )
                cfg = parse_courses_cfg(response.text)
                if cfg.get("user_id", 0) > 0 and not self._is_login_page(response):
                    self._mark_logged_in(cfg, time.time())
                    logger.info("Phiên Courses còn hiệu lực")
                    return

                oauth_link = self._find_oauth_link(response)
                if not oauth_link:
                    raise CoursesAuthError(
                        "Không tìm thấy link đăng nhập UIT SSO trên Courses"
                    )

                oauth = self._session.get(
                    oauth_link, timeout=(10, 30), allow_redirects=True
                )
                form = BeautifulSoup(oauth.text, "lxml").select_one("#kc-form-login")
                if form is not None:
                    action = urljoin(oauth.url, form.get("action", ""))
                    if not action:
                        raise CoursesAuthError("Form SSO Courses không có action")
                    post = self._session.post(
                        action,
                        data={
                            "username": self.config.uit_username,
                            "password": self.config.uit_password,
                            "login": "Đăng nhập",
                        },
                        timeout=(10, 30),
                        allow_redirects=True,
                    )
                    if self._is_login_page(post):
                        self.consecutive_auth_failures += 1
                        self.last_auth_error = self._login_error_text(post)
                        raise CoursesAuthError(
                            self.last_auth_error or "Đăng nhập Courses thất bại"
                        )

                dashboard = self._session.get(
                    self.base + "/?redirect=0",
                    timeout=(10, 30),
                    allow_redirects=True,
                )
                cfg = parse_courses_cfg(dashboard.text)
                if not cfg.get("user_id") or not cfg.get("sesskey"):
                    self.consecutive_auth_failures += 1
                    self.last_auth_error = self._login_error_text(dashboard)
                    raise CoursesAuthError(
                        self.last_auth_error or "Không lấy được sesskey/userId của Courses"
                    )
                self._mark_logged_in(cfg, time.time())
                self.consecutive_auth_failures = 0
                self.last_auth_error = None
                logger.info("Đăng nhập Courses thành công (userId=%s)", cfg.get("user_id"))
            except requests.RequestException as exc:
                self.consecutive_auth_failures += 1
                self.last_auth_error = str(exc)
                raise CoursesAuthError(f"Lỗi mạng khi đăng nhập Courses: {exc}") from exc

    def _mark_logged_in(self, cfg: dict[str, Any], now: float) -> None:
        self._sesskey = cfg["sesskey"]
        self._user_id = int(cfg["user_id"])
        timeout = int(cfg.get("sessiontimeout") or 28800)
        margin = max(60, self.config.courses_session_refresh_margin_minutes * 60)
        self._logged_in_at = now
        self._deadline = now + min(timeout, 8 * 3600) - margin
        self.consecutive_auth_failures = 0
        self.state.set_meta("courses_user_id", str(self._user_id))
        self.state.set_meta(
            "courses_last_auth_success",
            time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        )

    def ensure_session(self, force: bool = False) -> None:
        if force:
            self.login(force=True)
            return
        if not self._sesskey or not self._user_id or not self._deadline:
            self.login()
            return
        if time.time() >= self._deadline:
            logger.info("Phiên Courses sắp/đã hết hạn, đăng nhập lại")
            self.login(force=True)

    @property
    def user_id(self) -> int:
        if not self._user_id:
            self.ensure_session()
        return int(self._user_id or 0)

    # ---------- AJAX ----------

    def _ajax(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        with self._request_lock:
            self.ensure_session()
            last_error = "unknown"
            for attempt in range(2):
                if not self._sesskey:
                    self.login(force=True)
                try:
                    response = self._session.post(
                        self.base + "/lib/ajax/service.php",
                        params={"sesskey": self._sesskey, "info": method},
                        json=[{"index": 0, "methodname": method, "args": args}],
                        timeout=(10, 30),
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    raise CoursesError(f"Lỗi mạng Courses: {exc}") from exc

                if response.status_code in (301, 302, 303, 307, 308):
                    if attempt == 0:
                        self.login(force=True)
                        continue
                    raise CoursesAuthError("Courses chuyển hướng đăng nhập")

                try:
                    payload = response.json()
                except ValueError as exc:
                    if attempt == 0 and ("kc-form-login" in (response.text or "") or "login/index.php" in response.url):
                        self.login(force=True)
                        continue
                    raise CoursesError("Courses không trả JSON") from exc

                if isinstance(payload, list) and payload:
                    item = payload[0]
                    if item.get("error"):
                        exception = item.get("exception") or {}
                        errorcode = exception.get("errorcode") or ""
                        last_error = exception.get("message") or str(exception)
                        if errorcode in {"servicerequireslogin", "invalidsesskey"}:
                            if attempt == 0:
                                logger.warning("Phiên Courses hết hạn (%s), đăng nhập lại", errorcode)
                                self.login(force=True)
                                continue
                            raise CoursesAuthError(last_error)
                        raise CoursesError(last_error)
                    return item.get("data") or {}

                raise CoursesError(f"Courses trả dữ liệu không hợp lệ: {str(payload)[:200]}")

            raise CoursesError(last_error)

    def get_notifications(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._ajax(
            "message_popup_get_popup_notifications",
            {
                "useridto": self.user_id,
                "limit": limit,
                "offset": offset,
                "newestfirst": True,
            },
        )

    def get_conversations(self, limit: int = 50) -> dict[str, Any]:
        return self._ajax(
            "core_message_get_conversations",
            {
                "userid": self.user_id,
                "limitfrom": 0,
                "limitnum": limit,
            },
        )

    def get_conversation_messages(
        self, conversation_id: int, limit: int = 10
    ) -> dict[str, Any]:
        return self._ajax(
            "core_message_get_conversation_messages",
            {
                "currentuserid": self.user_id,
                "convid": int(conversation_id),
                "limitfrom": 0,
                "limitnum": limit,
                "newest": True,
            },
        )

    def get_unread_counts(self) -> dict[str, Any]:
        return self._ajax(
            "core_message_get_unread_conversation_counts",
            {"userid": self.user_id},
        )
