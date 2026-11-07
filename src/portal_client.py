from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config
from .state import State

logger = logging.getLogger(__name__)

BASE_URL = "https://portal.uit.edu.vn"
LOGIN_URL = BASE_URL + "/api/auth/login?returnTo=%2Fsinh-vien%2Ftkb"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


class PortalError(RuntimeError):
    pass


class AuthError(PortalError):
    pass


class PortalDataError(PortalError):
    pass


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi,en;q=0.9",
        }
    )
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _extract_balanced_json(text: str, marker: str) -> dict[str, Any] | None:
    """Extract a JSON object embedded in an escaped Next.js RSC payload."""
    marker_index = text.find(marker)
    if marker_index < 0:
        return None
    start = text.find("{", marker_index)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : index + 1]
                try:
                    return json.loads(raw.replace('\\"', '"').replace("\\\\", "\\"))
                except json.JSONDecodeError:
                    logger.warning("Không parse được JSON nhúng trong trang TKB")
                    return None
    return None


def parse_tkb_meta_html(html: str) -> dict[str, Any] | None:
    year_list = _extract_balanced_json(html, 'yearList\\":')
    hoc_ky_match = re.search(r'initialHocKy\\":(\d+)', html)
    nam_hoc_match = re.search(r'initialNamHoc\\":(\d+)', html)
    if not year_list:
        return None
    hoc_ky = int(hoc_ky_match.group(1)) if hoc_ky_match else None
    nam_hoc = int(nam_hoc_match.group(1)) if nam_hoc_match else None
    default_year_id = year_list.get("defaultYearId")
    default_semester = year_list.get("defaultSemester")
    if hoc_ky is None and default_semester is not None:
        hoc_ky = int(default_semester)
    return {
        "year_list": year_list,
        "hoc_ky": hoc_ky,
        "nam_hoc": nam_hoc,
        "year_id": int(default_year_id) if default_year_id is not None else None,
    }


class PortalClient:
    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self._session = _build_session()
        self._login_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._logged_in_at: float | None = None
        self._deadline: float | None = None
        self._meta_cache: dict[str, Any] | None = None
        self._meta_cache_at: float = 0.0
        self.consecutive_auth_failures = 0
        self.last_auth_error: str | None = None

    # ---------- session ----------

    def _margin_seconds(self) -> int:
        return max(60, self.config.auth_relogin_margin_minutes * 60)

    def _compute_deadline(self, now: float) -> float:
        expiry: float | None = None
        try:
            for cookie in self._session.cookies:
                if cookie.name == "ums_portal_session" and cookie.expires:
                    expiry = float(cookie.expires)
                    break
        except Exception:
            expiry = None
        # Observed portal session lifetime is ~8h.
        fallback = now + 8 * 3600
        deadline = min(expiry, fallback) if expiry else fallback
        return deadline - self._margin_seconds()

    def _is_login_page(self, response: requests.Response) -> bool:
        text = response.text or ""
        return (
            "kc-form-login" in text
            or "kc-page-title" in text
            or "sso.uit.edu.vn/realms/UIT" in response.url
        )

    def _login_error_text(self, response: requests.Response) -> str:
        try:
            soup = BeautifulSoup(response.text, "lxml")
            selectors = [
                "#input-error",
                "#kc-error-message",
                ".pf-c-form__helper-text.pf-m-error",
                ".kc-feedback-text",
                ".alert-error",
            ]
            for selector in selectors:
                node = soup.select_one(selector)
                if node and node.get_text(strip=True):
                    return node.get_text(" ", strip=True)[:400]
            text = soup.get_text(" ", strip=True)
            return text[:400] if text else f"HTTP {response.status_code}"
        except Exception:
            return f"HTTP {response.status_code}"

    def _validate_session(self) -> bool:
        try:
            response = self._session.get(
                BASE_URL + "/api/sinh-vien/tkb/dang-ky",
                timeout=(10, 30),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning("Không kiểm tra được phiên: %s", exc)
            return False
        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            return "application/json" in content_type
        return False

    def login(self, force: bool = False) -> None:
        with self._login_lock:
            now = time.time()
            if (
                not force
                and self._logged_in_at is not None
                and self._deadline is not None
                and now < self._deadline
            ):
                return

            logger.info("Bắt đầu đăng nhập cổng UIT...")
            try:
                response = self._session.get(LOGIN_URL, timeout=(10, 30), allow_redirects=True)
                form = BeautifulSoup(response.text, "lxml").select_one("#kc-form-login")
                if form is None:
                    if self._validate_session():
                        self._mark_logged_in(time.time())
                        logger.info("Phiên portal vẫn còn hiệu lực, không cần đăng nhập lại")
                        return
                    raise AuthError(
                        "Không tìm thấy form đăng nhập SSO và phiên hiện tại không hợp lệ "
                        f"(HTTP {response.status_code}, URL {response.url.split('?')[0]})"
                    )

                action = urljoin(response.url, form.get("action", ""))
                if not action:
                    raise AuthError("Form đăng nhập SSO không có action")

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

                if self._is_login_page(post) or not self._validate_session():
                    self.consecutive_auth_failures += 1
                    self.last_auth_error = self._login_error_text(post)
                    logger.error(
                        "Đăng nhập thất bại (lần %s): %s",
                        self.consecutive_auth_failures,
                        self.last_auth_error,
                    )
                    raise AuthError(self.last_auth_error or "Đăng nhập thất bại")

                self.consecutive_auth_failures = 0
                self.last_auth_error = None
                self._mark_logged_in(time.time())
                logger.info("Đăng nhập thành công")
            except requests.RequestException as exc:
                self.consecutive_auth_failures += 1
                self.last_auth_error = str(exc)
                logger.error("Lỗi mạng khi đăng nhập: %s", exc)
                raise AuthError(f"Lỗi mạng khi đăng nhập: {exc}") from exc

    def _mark_logged_in(self, now: float) -> None:
        self._logged_in_at = now
        self._deadline = self._compute_deadline(now)
        self.state.set_meta("last_auth_success", datetime.now().astimezone().isoformat(timespec="seconds"))

    def ensure_session(self, force: bool = False) -> None:
        if force:
            self.login(force=True)
            return
        if self._logged_in_at is None or self._deadline is None:
            self.login()
            return
        if time.time() >= self._deadline:
            logger.info("Phiên portal sắp/đã hết hạn, đăng nhập lại")
            self.login(force=True)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        with self._request_lock:
            self.ensure_session()
            url = path if path.startswith("http") else BASE_URL + path
            try:
                response = self._session.request(method, url, timeout=(10, 30), **kwargs)
            except requests.RequestException as exc:
                raise PortalError(f"Lỗi mạng: {exc}") from exc

            if response.status_code in (401, 403) or (
                response.status_code == 200 and "kc-form-login" in (response.text or "")
            ):
                logger.warning("Phiên portal hết hiệu lực (HTTP %s), đăng nhập lại", response.status_code)
                self.login(force=True)
                try:
                    response = self._session.request(method, url, timeout=(10, 30), **kwargs)
                except requests.RequestException as exc:
                    raise PortalError(f"Lỗi mạng sau khi đăng nhập lại: {exc}") from exc

            if response.status_code >= 500:
                raise PortalError(f"Portal trả HTTP {response.status_code}")
            return response

    # ---------- metadata ----------

    def get_tkb_meta(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._meta_cache and now - self._meta_cache_at < 6 * 3600:
            return self._meta_cache

        meta: dict[str, Any] | None = None
        try:
            response = self._request("GET", "/sinh-vien/tkb")
            if response.status_code == 200:
                meta = parse_tkb_meta_html(response.text)
                if meta:
                    logger.info(
                        "TKB metadata: hocKy=%s namHoc=%s yearId=%s",
                        meta.get("hoc_ky"),
                        meta.get("nam_hoc"),
                        meta.get("year_id"),
                    )
        except PortalError as exc:
            logger.warning("Không lấy được metadata trang TKB: %s", exc)

        if meta is None:
            meta = {
                "year_list": self.state.get_meta_json("year_list", {"years": []}),
                "hoc_ky": self.state.get_meta_int("hoc_ky") or self.config.hoc_ky,
                "nam_hoc": self.state.get_meta_int("nam_hoc") or self.config.nam_hoc,
                "year_id": self.state.get_meta_int("year_id") or self.config.year_id,
            }

        # If still missing, use registration data.
        if not meta.get("hoc_ky") or not meta.get("year_id") or not meta.get("nam_hoc"):
            try:
                registration = self.get_registration()
                meta["hoc_ky"] = meta.get("hoc_ky") or registration.get("hocKy")
                meta["year_id"] = meta.get("year_id") or registration.get("yearId")
                if not meta.get("nam_hoc"):
                    meta["nam_hoc"] = self._year_number_from_list(
                        meta.get("year_list"), meta.get("year_id")
                    )
            except PortalError as exc:
                logger.warning("Không lấy được học kỳ từ dang-ky: %s", exc)

        if not meta.get("hoc_ky") or not meta.get("year_id"):
            raise PortalDataError("Không xác định được hocKy/yearId của học kỳ hiện tại")
        if not meta.get("nam_hoc"):
            meta["nam_hoc"] = self._year_number_from_list(
                meta.get("year_list"), meta.get("year_id")
            ) or datetime.now().year

        for key in ("hoc_ky", "nam_hoc", "year_id"):
            if meta.get(key) is not None:
                self.state.set_meta(key, str(meta[key]))
        if meta.get("year_list"):
            self.state.set_meta_json("year_list", meta["year_list"])
        self.state.set_meta("tkb_meta_updated_at", datetime.now().astimezone().isoformat(timespec="seconds"))

        self._meta_cache = meta
        self._meta_cache_at = now
        return meta

    @staticmethod
    def _year_number_from_list(year_list: dict[str, Any] | None, year_id: int | None) -> int | None:
        if not year_list:
            return None
        for year in year_list.get("years") or []:
            if year_id is not None and year.get("id") == year_id:
                return int(year.get("yearNumber")) if year.get("yearNumber") is not None else None
        years = year_list.get("years") or []
        if years and years[0].get("yearNumber") is not None:
            return int(years[0]["yearNumber"])
        return None

    # ---------- TKB / registration ----------

    def get_registration(
        self, hoc_ky: int | None = None, year_id: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if hoc_ky is not None and year_id is not None:
            params = {"hocKy": hoc_ky, "yearId": year_id}
        response = self._request("GET", "/api/sinh-vien/tkb/dang-ky", params=params)
        try:
            data = response.json()
        except ValueError as exc:
            raise PortalDataError("dang-ky không trả JSON") from exc
        if not isinstance(data, dict) or "rows" not in data:
            raise PortalDataError("dang-ky trả dữ liệu không hợp lệ")
        if data.get("hocKy") is not None:
            self.state.set_meta("hoc_ky", str(data["hocKy"]))
        if data.get("yearId") is not None:
            self.state.set_meta("year_id", str(data["yearId"]))
        codes = [
            str(row.get("code")).upper()
            for row in data.get("rows") or []
            if row.get("code")
        ]
        if codes:
            self.state.save_class_codes(codes)
        return data

    def get_tkb_week(self, start_date: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = meta or self.get_tkb_meta()
        expected_monday = (
            date.fromisoformat(start_date) - timedelta(days=date.fromisoformat(start_date).weekday())
        ).isoformat()

        last_error = "TKB trả dữ liệu không hợp lệ"
        for attempt in range(2):
            params = {
                "hocKy": meta["hoc_ky"],
                "namHoc": meta["nam_hoc"],
                "yearId": meta["year_id"],
                "startDate": start_date,
            }
            response = self._request("GET", "/api/sinh-vien/tkb", params=params)
            try:
                data = response.json()
            except ValueError as exc:
                raise PortalDataError("TKB không trả JSON") from exc

            if response.status_code == 400 and isinstance(data, dict):
                error_code = data.get("error")
                if error_code in {"invalid_yearId", "invalid_hocKy"} and attempt == 0:
                    logger.info("Metadata TKB cũ (%s), làm mới và thử lại", error_code)
                    self._meta_cache = None
                    meta = self.get_tkb_meta(force=True)
                    continue
                last_error = f"TKB lỗi: {error_code}"
                raise PortalDataError(last_error)

            if not isinstance(data, dict) or "tiets" not in data:
                raise PortalDataError("TKB trả dữ liệu không hợp lệ")
            actual = data.get("startOfWeek")
            if actual and actual != expected_monday:
                raise PortalDataError(
                    f"TKB trả tuần {actual}, không khớp tuần yêu cầu {expected_monday}"
                )
            return data

        raise PortalDataError(last_error)

    # ---------- exams ----------

    def get_exams(self, year: int, examination: str, semester: str) -> list[dict[str, Any]]:
        response = self._request(
            "POST",
            "/api/sv/exam-schedule",
            json={
                "academic": [],
                "year_name": str(year),
                "examination": examination,
                "semester": semester,
            },
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise PortalDataError("exam-schedule không trả JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise PortalDataError("exam-schedule trả dữ liệu không hợp lệ")
        return data["items"]


class PublicPortalClient:
    """Public announcements feed; no portal session required."""

    def __init__(self, config: Config):
        self.config = config
        self._session = _build_session()
        self._lock = threading.Lock()

    def get_announcements(self, page: int = 1, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            try:
                response = self._session.get(
                    BASE_URL + "/api/public/announcements",
                    params={"page": page, "limit": limit},
                    timeout=(10, 30),
                )
            except requests.RequestException as exc:
                raise PortalError(f"Lỗi mạng khi tải thông báo: {exc}") from exc
        if response.status_code != 200:
            raise PortalError(f"Announcements trả HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise PortalDataError("Announcements không trả JSON") from exc
        if not isinstance(data, dict) or "items" not in data:
            raise PortalDataError("Announcements trả dữ liệu không hợp lệ")
        return data
