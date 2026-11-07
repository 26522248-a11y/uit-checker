# Spec — UIT Portal Checker & Telegram Notifier

- **Trạng thái:** MVP đã triển khai (2026-11-07)
- **Ngày:** 2026-11-07
- **Thư mục dự án:** `E:\uit_checker`
- **Tài khoản portal:** `26522248` (mật khẩu đã được cung cấp riêng; chỉ lưu trong `.env`, không ghi vào spec/repo)
- **Múi giờ:** `Asia/Ho_Chi_Minh` (UTC+7)

**Quyết định đã chốt (2026-11-07):**

1. Chế độ thông báo: `relevant` — chỉ thông báo chung + thông báo nghỉ/bù đúng lớp của sinh viên.
2. Gửi thông báo ngay khi TKB hoặc lịch thi thay đổi: **Có**.
3. Ngày không có lịch: **Có gửi** tin "Hôm nay không có lịch học/lịch thi" (đặt `DIGEST_SEND_EMPTY=true`).
4. Kênh nhận: Telegram bot `@uit666bot`, chat ID đã cấu hình trong `.env` (không ghi vào spec).

---

## 1. Tổng quan

### 1.1 Vấn đề

Sinh viên phải vào Cổng thông tin UIT mỗi ngày để xem thời khóa biểu (TKB), lịch thi và các thông báo. Việc này dễ bỏ sót các thay đổi quan trọng như:

- Thông báo nghỉ lớp / học bù.
- Đổi phòng, đổi tiết, đổi giảng viên.
- Lịch thi mới hoặc lịch thi thay đổi giờ/phòng.
- Các thông báo học vụ chung.

### 1.2 Mục tiêu

Xây dựng một tool chạy liên tục 24/7, tự động:

1. Đăng nhập Cổng thông tin UIT và duy trì phiên.
2. Kiểm tra định kỳ TKB, lịch thi và thông báo.
3. Gửi Telegram **ngay khi có thông báo mới** (và khi TKB/lịch thi thay đổi).
4. Gửi **digest lúc 06:00 mỗi ngày** về lịch học/lịch thi trong ngày, nếu có.
5. Không gửi trùng, không spam thông báo cũ, không làm giả thông báo khi portal lỗi.

### 1.3 Ngoài phạm vi (MVP)

- Không đăng ký môn, không nộp bài, không sửa điểm.
- Không xử lý captcha/2FA/OTP.
- Không gửi email/SMS/Zalo; chỉ Telegram.
- Không có web UI quản trị trong MVP.
- Không hỗ trợ nhiều tài khoản sinh viên trong MVP.
- Không theo dõi điểm, học phí, khảo sát, giấy xác nhận... (có thể mở rộng sau).

### 1.4 Định nghĩa

| Thuật ngữ | Ý nghĩa |
|---|---|
| TKB | Thời khóa biểu theo tuần của sinh viên |
| Lịch thi | Lịch thi giữa kỳ / cuối kỳ |
| Thông báo | Bài viết trên feed công khai của portal, gồm thông báo chung và thông báo nghỉ/bù theo lớp |
| Digest | Tin nhắn tổng hợp lịch học/lịch thi trong ngày, gửi lúc 06:00 |
| Poll | Một lần gọi API để kiểm tra dữ liệu |
| Baseline | Trạng thái ghi nhận lần đầu, dùng để tránh gửi thông báo cũ |
| Event key | Khóa duy nhất của một sự kiện, dùng để chống gửi trùng |

### 1.5 Mở rộng: Website môn học UIT (Courses)

- Spec riêng: **`E:\uit_checker\spec_courses.md`**.
- Tool hiện tại theo dõi thêm `https://courses.uit.edu.vn` (Moodle):
  - Chuông thông báo Moodle (`message_popup_get_popup_notifications`).
  - Tin nhắn riêng (`core_message_get_conversations` + `core_message_get_conversation_messages`).
- Gửi Telegram ngay khi có notification/tin nhắn mới; poll mỗi 2 phút.
- Dùng chung bot Telegram, tài khoản portal và SQLite state.
- Không đánh dấu đã đọc; baseline lần đầu để tránh spam 28 notification cũ.

---

## 2. Yêu cầu chức năng

### Bảng yêu cầu

| ID | Yêu cầu | Mức ưu tiên |
|---|---|---|
| FR-01 | Đăng nhập portal và duy trì phiên tự động | Must |
| FR-02 | Theo dõi TKB và phát hiện thay đổi | Must |
| FR-03 | Theo dõi lịch thi và phát hiện thay đổi | Must |
| FR-04 | Theo dõi thông báo mới | Must |
| FR-05 | Gửi Telegram, chống trùng, retry | Must |
| FR-06 | Digest 06:00 hằng ngày | Must |
| FR-07 | Lưu trạng thái bền vững (SQLite) | Must |
| FR-08 | Chạy 24/7, tự phục hồi | Must |
| FR-09 | Cấu hình qua `.env` | Must |
| FR-10 | Logging, health check, cảnh báo lỗi | Must |
| FR-11 | Chế độ `relevant` cho thông báo | Should |
| FR-12 | Xác nhận 2 lần trước khi báo xóa/đổi TKB/lịch thi | Should |
| FR-13 | Digest bù nếu restart trước 12:00 | Should |
| FR-14 | Chặn thông báo TKB/lịch thi cho ngày đã qua | Should |
| FR-15 | Tự động lọc thông báo nghỉ lớp theo lớp đang học | Could |

---

### FR-01 — Đăng nhập và duy trì phiên

**Luồng đăng nhập (đã khảo sát thực tế):**

1. `GET https://portal.uit.edu.vn/api/auth/login?returnTo=%2Fsinh-vien%2Ftkb`
2. Portal trả `307` sang Keycloak SSO:
   `https://sso.uit.edu.vn/realms/UIT/protocol/openid-connect/auth?...` (OIDC Authorization Code + PKCE).
3. Trang SSO có form `#kc-form-login`; POST các field:
   - `username`
   - `password`
   - `login` (giá trị bất kỳ, ví dụ `Đăng nhập`)
4. Thành công: `302` về `/api/auth/callback/keycloak?code=...`, sau đó `307` về `/sinh-vien/tkb`.
5. Portal set các cookie, quan trọng nhất:
   - `ums_portal_session` — cookie phiên chính, thời hạn quan sát khoảng **8 giờ** (~28.800 giây).
   - `kc_id_token` — ID token, thời hạn khoảng **15 phút**, **không cần** cho các API TKB/lịch thi.
   - Cookie SSO của `sso.uit.edu.vn` (KEYCLOAK_SESSION...) — khoảng 24 giờ.

**Yêu cầu:**

- Dùng `requests.Session` (hoặc `httpx.Client`) để giữ cookie.
- Không dùng `kc_id_token` để quyết định phiên còn hạn; kiểm tra `ums_portal_session`.
- Trước mỗi request cần auth:
  - Nếu chưa đăng nhập hoặc phiên sắp hết hạn (còn < 15 phút), đăng nhập lại.
  - Nếu API trả `401`, `403`, hoặc trả HTML trang login, đăng nhập lại một lần rồi retry request.
- Nếu đăng nhập thất bại:
  - Retry với backoff (1s, 5s, 15s, 60s...).
  - Sau `AUTH_MAX_FAILURES` lần (mặc định 3) liên tiếp: gửi Telegram cảnh báo (nếu Telegram còn hoạt động), sau đó giãn thời gian thử lại (mặc định 60 phút) để tránh khóa tài khoản.
  - Không thoát process; tiếp tục chạy ở **degraded mode**: feed thông báo công khai vẫn hoạt động, TKB/lịch thi tạm dừng.
- Không log mật khẩu, cookie, token.
- Nếu form SSO thay đổi (có OTP/captcha): báo lỗi rõ ràng và dừng đăng nhập tự động; cần can thiệp thủ công.

**Acceptance criteria:**

- Đăng nhập đúng → gọi được `/api/sinh-vien/tkb/dang-ky` với HTTP 200.
- Xóa cookie `ums_portal_session` → tool tự đăng nhập lại và request thành công.
- Sai mật khẩu 3 lần → có cảnh báo Telegram, không thử lại liên tục.
- Portal trả 503 → tool không crash, retry sau.

---

### FR-02 — Theo dõi thời khóa biểu

**Nguồn dữ liệu:** `GET /api/sinh-vien/tkb` (xem mục 3.2).

**Yêu cầu:**

- Xác định học kỳ/năm hiện tại từ trang `/sinh-vien/tkb` hoặc API `dang-ky`.
- Poll TKB **tuần hiện tại** và **tuần kế tiếp** (mặc định mỗi 15 phút).
- Lưu snapshot theo tuần.
- So sánh snapshot mới với snapshot cũ, phát hiện:
  - **Thêm** buổi học.
  - **Xóa** buổi học.
  - **Đổi** phòng, tiết bắt đầu/kết thúc, giảng viên, trạng thái.
- Gửi Telegram khi có thay đổi, trừ lần chạy đầu tiên (baseline).
- Chỉ báo thay đổi cho ngày **hôm nay hoặc tương lai**; bỏ qua ngày đã qua.
- Với thay đổi dạng xóa/đổi: xác nhận lại sau 60 giây bằng một lần fetch thứ hai. Nếu vẫn khác → mới gửi, tránh false positive do API chập chờn.
- Nếu API trả về tuần rỗng bất thường (trước đó có nhiều buổi, giờ còn 0):
  - Xác nhận 2 lần.
  - Nếu vẫn rỗng, gửi **một** tin tổng hợp "Lịch tuần đã bị xóa/không còn trên hệ thống", không gửi N tin hủy riêng lẻ.
- Nếu API lỗi (5xx/timeout/HTML login): không cập nhật snapshot, không suy luận là "đã hủy".
- Nếu `startOfWeek` trả về không khớp tuần yêu cầu: coi là lỗi, không dùng dữ liệu.
- `ngayLe` nếu có: hiển thị tên ngày lễ trong digest; không tự suy ra nghỉ nếu không có dữ liệu.

**Acceptance criteria:**

- Đổi phòng một buổi học trên portal → Telegram báo đúng buổi, đúng phòng cũ/mới.
- Hủy một buổi học → Telegram báo hủy sau khi xác nhận 2 lần.
- API trả 503 → không gửi tin hủy sai.
- Chạy lại tool → không gửi lại thay đổi đã báo.

---

### FR-03 — Theo dõi lịch thi

**Nguồn dữ liệu:** `POST /api/sv/exam-schedule` (xem mục 3.3).

**Yêu cầu:**

- Poll các tổ hợp:
  - `examination`: `midterm`, `final_term`.
  - `semester`: `semester_1`, `semester_2`, `semester_3`.
  - `year_name`: năm hiện tại; thêm năm kế tiếp nếu tháng hiện tại là 11 hoặc 12 (để bắt lịch thi tháng 1).
- Không dùng `examination: "mcq"` (API hiện trả 500 gateway_error; frontend portal cũng không dùng).
- Dedupe theo khóa:
  `examination|subject_code|section_class_code|date|shift|room` (hoặc `id` nếu ổn định).
- Phát hiện:
  - Lịch thi mới.
  - Đổi ngày/giờ/phòng/ca/hình thức/ghi chú.
  - Lịch thi bị xóa (xác nhận 2 lần).
- Gửi Telegram ngay khi có thay đổi, trừ baseline.
- Chỉ báo cho kỳ thi có ngày **hôm nay hoặc tương lai**.
- Nếu một tổ hợp API lỗi: giữ nguyên dữ liệu cũ của tổ hợp đó, không coi là "đã xóa toàn bộ".
- Không dùng dữ liệu mock. Nếu API lỗi, tuyệt đối không gửi thông báo dựa trên mock.

**Acceptance criteria:**

- Thêm một lịch thi mới → Telegram báo trong vòng 1 chu kỳ poll.
- Đổi phòng thi → Telegram báo đúng phòng cũ/mới.
- API lỗi 500 → không gửi tin "lịch thi bị hủy".
- Lịch thi ngày hôm nay xuất hiện trong digest 06:00.

---

### FR-04 — Theo dõi thông báo

**Nguồn dữ liệu:** `GET /api/public/announcements?page=1&limit=50` (xem mục 3.4).

**Yêu cầu:**

- Poll mỗi **2 phút** (cấu hình được, tối thiểu 60 giây).
- Feed công khai, **không cần đăng nhập**. Dùng client riêng để không phụ thuộc phiên portal.
- Phát hiện thông báo mới:
  - Lưu `max_id` và danh sách ID gần đây (mặc định 500 ID).
  - Item mới nếu `id > max_id` hoặc `id` chưa có trong danh sách đã thấy.
  - Nếu có nhiều item mới, có thể page thêm (tối đa 5 trang) cho đến khi gặp ID đã thấy.
- Lần chạy đầu tiên: ghi nhận baseline, **không gửi** 22.000+ thông báo cũ.
- Gửi Telegram ngay khi phát hiện item mới.
- Link chi tiết: `https://portal.uit.edu.vn/bai-viet/{slug}`.
- **Chế độ lọc `ANNOUNCEMENT_MODE`:**
  - `relevant` (mặc định): chỉ gửi thông báo có khả năng ảnh hưởng đến sinh viên:
    - Thông báo chung (không phải thông báo nghỉ/bù theo lớp) → gửi.
    - Thông báo nghỉ/bù theo lớp (`category_slugs` chứa `thong-bao-nghi-bu`):
      - Trích mã lớp từ tiêu đề (ví dụ `(IT001.R122)`, `(NT204.R11.ANTT.2)`).
      - Chỉ gửi nếu mã lớp khớp với lớp sinh viên đang học (lấy từ `dang-ky` hoặc snapshot TKB).
      - Nếu tiêu đề không có mã lớp → coi là thông báo chung, gửi.
  - `all`: gửi tất cả thông báo mới.
- Nếu nhiều item mới trong cùng một poll:
  - ≤ 3 item: gửi từng tin.
  - > 3 item: gửi một tin tổng hợp (danh sách tiêu đề + link), tránh spam.
- Không gửi lại thông báo đã gửi, kể cả sau restart.

**Acceptance criteria:**

- Có thông báo mới → Telegram trong ≤ 2 chu kỳ poll (≤ 4 phút).
- Restart tool → không gửi lại thông báo cũ.
- Chế độ `relevant` không gửi thông báo nghỉ của lớp khác.
- Thông báo nghỉ đúng lớp của sinh viên → được gửi.
- `category=all` không được dùng (API trả rỗng); luôn gọi không có `category`.

---

### FR-05 — Gửi Telegram

**Yêu cầu:**

- Dùng Telegram Bot API:
  `POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`
  với:
  ```json
  {
    "chat_id": "...",
    "text": "...",
    "parse_mode": "HTML",
    "disable_web_page_preview": true
  }
  ```
- Hỗ trợ `TELEGRAM_MESSAGE_THREAD_ID` (diễn đàn/nhóm).
- Retry:
  - `429`: đọc `parameters.retry_after`, chờ đúng số giây rồi gửi lại.
  - `5xx`/timeout: retry với backoff (1s, 5s, 15s, 60s), tối đa 5 lần.
  - `400` do HTML lỗi: log, gửi lại dạng plain text (không parse_mode).
- Chống trùng:
  - Mỗi sự kiện có `event_key` duy nhất.
  - Ghi `notifications` vào SQLite với unique key trước/sau khi gửi; chỉ gửi nếu chưa có.
  - Nếu gửi thất bại, giữ trạng thái `failed` và retry; không tạo event key mới.
- Giới hạn 4096 ký tự/tin; cắt hoặc chia nhỏ theo dòng.
- Escape HTML cho nội dung động: `&`, `<`, `>`.
- Không gửi tin rỗng.

**Acceptance criteria:**

- Telegram token đúng → nhận được tin.
- Token sai → log lỗi rõ, tool vẫn chạy (không crash), không spam retry vô hạn.
- Telegram 429 → chờ `retry_after` rồi gửi lại.
- Restart giữa lúc gửi → không gửi trùng.

---

### FR-06 — Digest 06:00 hằng ngày

**Yêu cầu:**

- Chạy lúc **06:00** theo `Asia/Ho_Chi_Minh` (cron `0 6 * * *`).
- Nội dung:
  - TKB hôm nay (các buổi học), sắp xếp theo giờ bắt đầu.
  - Lịch thi hôm nay (nếu có), sắp xếp theo giờ bắt đầu.
  - Ngày lễ/thông tin đặc biệt nếu có.
- Chỉ gửi nếu có ít nhất một buổi học hoặc một lịch thi trong ngày.
  - Gửi cả ngày trống (theo yêu cầu đã chốt): `DIGEST_SEND_EMPTY=true`.
- Nếu không có lịch: vẫn gửi tin ngắn "Hôm nay không có lịch" (theo yêu cầu đã chốt).
- Luôn fetch TKB mới cho digest (không chỉ dùng snapshot cũ), với retry.
- Nếu fetch TKB lỗi:
  - Thử lại 3 lần.
  - Nếu vẫn lỗi, dùng snapshot gần nhất của tuần hiện tại (nếu có, ≤ 24 giờ) và ghi rõ "⚠️ Dữ liệu có thể chưa cập nhật".
  - Nếu không có snapshot, gửi tin cảnh báo "Không tải được TKB hôm nay" thay vì nói "không có lịch".
- Nếu lịch thi lỗi nhưng TKB OK: vẫn gửi TKB, kèm dòng "⚠️ Không kiểm tra được lịch thi".
- Digest bù:
  - Nếu process khởi động sau 06:00 nhưng trước `DIGEST_CATCHUP_UNTIL` (mặc định 12:00) và hôm nay chưa gửi digest → gửi bù, ghi rõ "gửi bù".
  - Sau 12:00 → bỏ qua, ghi log.
- Chống gửi trùng: `last_digest_date` trong `meta`.
- Nếu digest gửi thất bại: retry mỗi 5 phút đến 12:00, sau đó bỏ qua và cảnh báo.

**Acceptance criteria:**

- 06:00 có lớp → nhận digest đúng giờ, đúng lớp/phòng/tiết.
- 06:00 không có lớp/lịch thi → nhận tin "Hôm nay không có lịch".
- 06:00 có lịch thi nhưng không có lớp → vẫn nhận digest.
- Restart lúc 08:00 → nhận digest bù (nếu chưa gửi).
- Restart lúc 13:00 → không nhận digest bù.
- API TKB lỗi → không gửi "hôm nay không có lịch" sai.

---

### FR-07 — Lưu trạng thái và chống trùng

**Yêu cầu:**

- Dùng SQLite (`data/state.db`), chế độ WAL.
- Lưu:
  - `meta`: `announcement_max_id`, `announcement_seen_ids`, `last_digest_date`, `last_auth_success`, `schema_version`, `hoc_ky`, `year_id`, `nam_hoc`, `last_tkb_*`, `last_exam_*`.
  - `announcements`: id, slug, title, category, publish_date, first_seen, notified_at, processed_at.
    - `processed_at` dùng để phân biệt bài đã xử lý (baseline/không liên quan) với bài mới chưa xử lý; tránh mất thông báo nếu process crash giữa chừng.
  - `tkb_sessions`: week_start, session_key, payload JSON, first_seen, last_seen.
  - `exams`: exam_key, payload JSON, first_seen, last_seen.
  - `notifications`: event_key (unique), kind, payload, status, attempts, created_at, sent_at, error.
- Mọi thao tác ghi dùng transaction.
- Restart không làm mất baseline/dedup.
- Prune dữ liệu cũ (mặc định > 180 ngày) mỗi ngày lúc 03:00; giữ `notifications` 180 ngày.

**Acceptance criteria:**

- Restart nhiều lần → không gửi lại sự kiện đã gửi.
- DB hỏng/không ghi được → tool báo lỗi rõ, không crash âm thầm.

---

### FR-08 — Chạy 24/7

**Yêu cầu:**

- Chạy dạng daemon/container, tự khởi động lại khi lỗi.
- Graceful shutdown khi nhận SIGTERM/SIGINT: dừng scheduler, đóng DB, không cắt ngang transaction.
- Không exit khi portal/Telegram lỗi tạm thời.
- Có log rotation (mặc định 14 ngày).
- Có heartbeat:
  - Log mỗi chu kỳ poll.
  - Tùy chọn gửi Telegram heartbeat hằng tuần (`HEARTBEAT_ENABLED=false` mặc định).
- Tự động cảnh báo Telegram khi:
  - Đăng nhập thất bại 3 lần.
  - Portal lỗi liên tiếp ≥ 6 lần (cấu hình).
- Telegram gửi thất bại liên tiếp ≥ 5 lần: ghi log `ERROR` (không thể tự cảnh báo qua Telegram); nếu cần, dùng kênh giám sát bên ngoài.

**Acceptance criteria:**

- Kill process → Docker/systemd tự restart, tool tiếp tục chạy.
- Portal down 1 giờ → tool vẫn sống, tự phục hồi khi portal lên.
- Log đủ để debug lỗi gần nhất.

---

### FR-09 — Cấu hình

- Toàn bộ cấu hình qua biến môi trường/`.env`.
- Không hardcode credential trong code.
- Có `.env.example`; `.env` nằm trong `.gitignore`.
- Kiểm tra cấu hình lúc khởi động:
  - Thiếu `UIT_USERNAME`, `UIT_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` → báo lỗi rõ và dừng (trừ chế độ `--dry-run`).
- Hỗ trợ CLI:
  - `--once`: chạy một chu kỳ rồi thoát (test).
  - `--dry-run`: không gửi Telegram, chỉ log.
  - `--test-telegram`: gửi tin test.
  - `--check-auth`: kiểm tra đăng nhập.
  - `--digest-now`: gửi digest ngay (test).

**Acceptance criteria:**

- Đổi `TELEGRAM_CHAT_ID` trong `.env` → tin gửi đến chat mới.
- Thiếu token → lỗi cấu hình rõ ràng, không chạy nửa vời.

---

### FR-10 — Logging và health

- Log ra stdout + file `logs/app.log` (rotating).
- Định dạng: timestamp theo giờ `Asia/Ho_Chi_Minh`, level, module, message, context (không chứa secret).
- Các log quan trọng:
  - Login success/failure (không log password).
  - Poll start/end, số item, thời gian.
  - Event phát hiện, event key.
  - Telegram message_id/status.
  - Retry/backoff.
- Health check (MVP): file `data/heartbeat` cập nhật mỗi phút; log "alive".
- Tùy chọn Phase 2: HTTP `/healthz` trả JSON trạng thái các job.

---

## 3. Nguồn dữ liệu — khảo sát Cổng thông tin UIT

> Tất cả endpoint dưới đây đã được kiểm tra thực tế ngày 2026-11-07 với tài khoản `26522248`.

### 3.1 Xác thực

| Mục | Giá trị |
|---|---|
| Portal | `https://portal.uit.edu.vn` |
| SSO | `https://sso.uit.edu.vn/realms/UIT` |
| Login start | `GET /api/auth/login?returnTo=%2Fsinh-vien%2Ftkb` |
| Login form | `#kc-form-login`, POST `username`, `password`, `login` |
| Callback | `/api/auth/callback/keycloak?code=...` |
| Cookie phiên | `ums_portal_session` (~8 giờ) |
| Cookie SSO | `KEYCLOAK_SESSION` (~24 giờ) |
| API auth check | `GET /api/sinh-vien/tkb/dang-ky` → 200 JSON nếu hợp lệ; 401 `{"error":"unauthenticated"}` nếu hết phiên |

**Lưu ý:**

- `kc_id_token` chỉ sống ~15 phút nhưng **không cần** cho API TKB/lịch thi; chỉ `ums_portal_session` là đủ.
- Khi `ums_portal_session` hết hạn, có thể đăng nhập lại mà không cần nhập lại mật khẩu nếu SSO session còn hiệu lực; code vẫn phải xử lý cả trường hợp phải nhập lại.
- Không dùng `returnTo` tùy tiện; dùng `/sinh-vien/tkb` để kiểm tra đăng nhập.
- Nếu portal đổi form/SSO: cần cập nhật selector; test sẽ phát hiện.

### 3.2 API thời khóa biểu

#### 3.2.1 Metadata học kỳ/năm

Trang `/sinh-vien/tkb` nhúng props:

```json
{
  "initialData": {
    "hocKy": 1,
    "namHoc": 2026,
    "tiets": [],
    "ngayLe": [],
    "startOfWeek": "2026-11-02"
  },
  "yearList": {
    "years": [
      {
        "id": 47,
        "name": "2026-2027",
        "yearNumber": 2026,
        "startDates": { "1": null, "2": null, "3": null },
        "endDates": { "1": null, "2": null, "3": null }
      }
    ],
    "defaultYearId": 47,
    "defaultSemester": 1
  },
  "initialHocKy": 1,
  "initialNamHoc": 2026
}
```

- `yearId` (ví dụ `47`) **bắt buộc** khi gọi API TKB.
- `namHoc` (ví dụ `2026`) là năm học, không phải `yearId`.
- Có thể lấy nhanh `hocKy` + `yearId` hiện tại từ `GET /api/sinh-vien/tkb/dang-ky`.

**Cách lấy metadata trong implementation:**

1. Fetch HTML `/sinh-vien/tkb`.
2. Tìm chuỗi `yearList` trong RSC payload (JSON bị escape trong thẻ `<script>`), giải escape rồi `json.loads`.
3. Lấy `initialHocKy`, `initialNamHoc`, `defaultYearId`; tìm year tương ứng trong `yearList.years` để lấy `yearNumber`.
4. Fallback nếu parse lỗi: dùng `dang-ky` cho `hocKy`/`yearId`, và cho phép cấu hình `YEAR_ID`, `NAM_HOC`, `HOC_KY` trong `.env`.
5. Cache metadata trong `meta`; refresh mỗi ngày hoặc khi API TKB trả lỗi `invalid_yearId`/`invalid_hocKy`.

#### 3.2.2 Đăng ký học phần (dùng để lấy lớp đang học)

```http
GET /api/sinh-vien/tkb/dang-ky
```

Response (rút gọn):

```json
{
  "hocKy": 1,
  "yearId": 47,
  "auto": true,
  "rows": [
    {
      "id": 109237,
      "code": "EC005.R11",
      "subjectCode": "EC005",
      "subjectName": "Giới thiệu ngành Thương mại Điện tử",
      "subjectId": 13262,
      "hasSyllabus": true,
      "credit": 1,
      "lecturer": "ThS Trình Trọng Tín",
      "size": 74,
      "maxSize": 80,
      "status": "active",
      "methodCode": "LT",
      "weekly2": 2,
      "startDate": "2026-09-07",
      "endDate": "2026-11-14",
      "slots": [
        { "thu": 4, "tietBatDau": 6, "tietKetThuc": 8, "room": "B3.22" }
      ]
    }
  ]
}
```

Dùng `rows[].code` làm danh sách mã lớp của sinh viên (phục vụ lọc thông báo nghỉ/bù).

Có thể truyền `?hocKy=1&yearId=47` để lấy học kỳ cụ thể.

#### 3.2.3 TKB theo tuần

```http
GET /api/sinh-vien/tkb?hocKy=1&namHoc=2026&yearId=47&startDate=2026-11-09
```

- `startDate`: `YYYY-MM-DD`; có thể là bất kỳ ngày nào trong tuần, API chuẩn hóa về thứ Hai.
- Thiếu `yearId` → `400 {"error":"invalid_yearId"}`.
- `hocKy` sai → `400 {"error":"invalid_hocKy"}`.

Response (rút gọn):

```json
{
  "hocKy": 1,
  "namHoc": 2026,
  "tiets": [
    {
      "id": "tkb-0-PET01.R116-2026-11-13",
      "maLop": "PET01.R116",
      "maMonHoc": "CQUI",
      "tenMonHoc": "B1 Preliminary 1",
      "ngay": "2026-11-13",
      "thu": 6,
      "tietBatDau": 1,
      "tietKetThuc": 3,
      "phong": "A323",
      "hinhThuc": "LT",
      "maGiangVien": "",
      "tenGiangVien": "Hồ Thị Nhiên Trinh",
      "lecturerTitle": "",
      "siSo": 0,
      "subjectId": 14747,
      "studentCount": 0,
      "hasSyllabus": false,
      "trangThai": "normal"
    }
  ],
  "ngayLe": [],
  "startOfWeek": "2026-11-09"
}
```

**Quy ước:**

- `thu`: `2` = Thứ 2, `3` = Thứ 3, ..., `7` = Thứ 7, `1` = Chủ nhật.
- `ngay`: `YYYY-MM-DD` (dùng để lọc chính xác ngày hôm nay).
- `tietBatDau`, `tietKetThuc`: tiết bắt đầu/kết thúc (1–15).
- `trangThai`: quan sát thấy `normal`; có thể có giá trị khác (hủy/nghỉ). Không hardcode chỉ `normal`; hiển thị giá trị lạ.
- **Quan trọng:** `maMonHoc` có thể bị lỗi mapping (thực tế trả `CQUI` cho nhiều môn). Không dùng `maMonHoc` để hiển thị; dùng `maLop` + `tenMonHoc`.
- `ngayLe`: danh sách ngày lễ (quan sát thấy rỗng trong học kỳ khảo sát).

**Hành vi đã kiểm tra:**

- Tuần 2026-11-02 (tuần thi giữa kỳ) trả `tiets: []` dù trước/sau đó có lớp. Đây là dữ liệu hợp lệ, không phải lỗi.
- API trả đúng tuần khi truyền `startDate` bất kỳ trong tuần.

#### 3.2.4 Bảng tiết học (để hiển thị giờ)

| Tiết | Bắt đầu | Kết thúc |
|---:|---|---|
| 1 | 07:30 | 08:15 |
| 2 | 08:15 | 09:00 |
| 3 | 09:00 | 09:45 |
| 4 | 09:55 | 10:40 |
| 5 | 10:40 | 11:25 |
| 6 | 13:00 | 13:45 |
| 7 | 13:45 | 14:30 |
| 8 | 14:30 | 15:15 |
| 9 | 15:25 | 16:10 |
| 10 | 16:10 | 16:55 |
| 11 | 16:55 | 17:40 |
| 12 | 17:45 | 18:30 |
| 13 | 18:30 | 19:15 |
| 14 | 19:15 | 20:00 |
| 15 | 20:00 | 20:45 |

- Bảng giờ bắt đầu lấy từ JS của portal; giờ kết thúc suy ra theo chuẩn 45 phút/tiết.
- Portal ghi chú: sáng tiết 1–5 (07:30–11:30), chiều 6–10 (13:00–17:00), tối 12–15 (17:45–20:45).
- Với lịch thi, ưu tiên dùng `start_time`/`end_time` do API trả về, không suy ra từ tiết.

### 3.3 API lịch thi

```http
POST /api/sv/exam-schedule
Content-Type: application/json

{
  "academic": [],
  "year_name": "2026",
  "examination": "midterm",
  "semester": "semester_1"
}
```

- `examination`: `midterm` | `final_term`. **Không dùng `mcq`** (hiện trả 500 `gateway_error`).
- `semester`: `semester_1` | `semester_2` | `semester_3`.
- `year_name`: chuỗi năm, ví dụ `"2026"`. Frontend portal dùng `new Date().getFullYear()`.

Response (rút gọn):

```json
{
  "items": [
    {
      "id": 1,
      "stt": 34,
      "subject_code": "MA006",
      "subject_name": "Giải tích",
      "section_class_id": "MA006.R125",
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
      "note": ""
    }
  ]
}
```

**Quy ước:**

- `date`: `dd/mm/yyyy` — phải parse theo định dạng này, không dùng `Date.parse` trực tiếp.
- `weekday`: chuỗi tiếng Việt (hiển thị).
- `start_time`, `end_time`: `HH:MM`.
- `shift`: ca thi (chuỗi).
- `format`: mã hình thức; map hiển thị:
  - `tl`, `tu_luan`, `essay`, `tn_tl` → Tự luận
  - `mcq`, `tn`, `trac_nghiem`, `multiple_choice` → Trắc nghiệm
  - `oral`, `van_dap`, `qna` → Vấn đáp
  - `th`, `thuc_hanh`, `practical`, `practice` → Thực hành
  - `project`, `tieu_luan`, `do_an`, `da`, `datn` → Tiểu luận / ĐATN
- `items` có thể rỗng (`{"items":[]}`) nếu chưa có lịch.
- Nếu API lỗi: `500 {"error":"Hệ thống đang bận, vui lòng thử lại sau.","code":"gateway_error"}`.
- Dedupe theo `subject_code|section_class_code|date|shift|room|examination`; bỏ item không có cả `date`, `room`, `shift` (giống frontend portal).
- Frontend portal coi lỗi API là dữ liệu mock; **tool này không được làm vậy**.

### 3.4 API thông báo

```http
GET /api/public/announcements?page=1&limit=50
```

- **Không truyền `category=all`**: API trả `{"items":[],"total":0}` (đã kiểm tra). Không truyền `category` để lấy tất cả.
- Có thể dùng `category=thong-bao`, `category=tuyen-sinh`, `category=sinh-vien`... nếu cần.
- Feed công khai, không cần đăng nhập.

Response (rút gọn):

```json
{
  "items": [
    {
      "id": 23174,
      "slug": "thong-bao-nghi-lop-he-thong-tim-kiem-phat-hien-va-ngan-ngua-xam-nhap-nt204-r11-antt-2-ngay-09-11-2026",
      "title": "Thông báo nghỉ lớp Hệ thống tìm kiếm, phát hiện và ngăn ngừa xâm nhập (NT204.R11.ANTT.2) ngày 09/11/2026",
      "excerpt": "",
      "cover_image_url": null,
      "category_slugs": ["thong-bao-nghi-bu", "dai-hoc-chinh-quy"],
      "category_names": ["Thông báo Nghỉ - Bù", "Đại học chính quy"],
      "publish_date": "2026-11-07T09:19:14Z",
      "show_publish_date": true,
      "is_featured": false,
      "department_name": "Khoa Mạng máy tính và Truyền thông"
    }
  ],
  "total": 22799,
  "page": 1,
  "limit": 50
}
```

- `publish_date` là UTC (hậu tố `Z`); hiển thị theo `Asia/Ho_Chi_Minh`.
- Link bài viết: `https://portal.uit.edu.vn/bai-viet/{slug}` (đã kiểm tra).
- Feed sắp xếp mới nhất trước; ID tăng dần.
- Tổng số thông báo rất lớn (~22.799) → **bắt buộc baseline lần đầu**, không gửi toàn bộ.

### 3.5 Ghi chú dữ liệu

- `maMonHoc` trong TKB có thể sai (`CQUI`); dùng `maLop`/`tenMonHoc`.
- Tên môn có thể chứa non-breaking space (`\xa0`); normalize về space thường.
- Lịch thi có thể có nhiều item trùng; dedupe.
- API có thể trả 503 khi portal quá tải (đã gặp khi crawl nhanh). Luôn retry và không coi là "không có dữ liệu".
- Không gửi quá nhiều request song song; tuần tự, có delay nhỏ giữa các request.

---

## 4. Kiến trúc đề xuất

### 4.1 Thành phần

```
┌─────────────────────┐
│   Scheduler         │  APScheduler (Asia/Ho_Chi_Minh)
│   - announcements   │
│   - tkb             │
│   - exams           │
│   - digest 06:00    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐     ┌──────────────────┐
│   Portal Client     │────▶│  UIT Portal/SSO  │
│   - login/session   │     └──────────────────┘
│   - TKB API         │
│   - Exam API        │
│   - Announcement API│
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐     ┌──────────────────┐
│   Detector          │────▶│  SQLite State    │
│   - parse           │     │  - snapshots     │
│   - diff            │     │  - dedup keys    │
│   - filter          │     └──────────────────┘
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐     ┌──────────────────┐
│   Notifier          │────▶│  Telegram Bot    │
│   - format          │     └──────────────────┘
│   - retry           │
└─────────────────────┘
```

### 4.2 Cấu trúc thư mục đề xuất

```
E:\uit_checker\
├─ spec.md
├─ README.md
├─ .env.example
├─ .gitignore
├─ requirements.txt
├─ Dockerfile
├─ docker-compose.yml
├─ src/
│  ├─ main.py              # entrypoint, CLI
│  ├─ config.py            # đọc .env, validate
│  ├─ portal_client.py     # login Keycloak, session manager, TKB/exam/announcement APIs
│  ├─ courses_client.py    # login OAuth2 Moodle, sesskey, AJAX APIs
│  ├─ courses_checker.py   # baseline/poll notification + message Courses
│  ├─ courses_formatter.py # format Telegram cho Courses
│  ├─ lock.py              # single-instance lock
│  ├─ healthcheck.py       # Docker healthcheck
│  ├─ models.py            # dataclasses: Session, Exam, Announcement
│  ├─ state.py             # SQLite, migrations, dedup
│  ├─ detector.py          # diff TKB/exam, filter announcement
│  ├─ formatter.py         # format Telegram messages
│  ├─ notifier.py          # Telegram sender, retry
│  ├─ scheduler.py         # APScheduler jobs
│  └─ logging_setup.py
├─ tests/
│  ├─ fixtures/            # JSON mẫu từ API
│  ├─ test_auth.py
│  ├─ test_tkb.py
│  ├─ test_exam.py
│  ├─ test_announcement.py
│  ├─ test_detector.py
│  ├─ test_formatter.py
│  └─ test_courses.py
├─ data/                   # gitignored: state.db, heartbeat
└─ logs/                   # gitignored: app.log
```

### 4.3 Công nghệ

| Thành phần | Đề xuất |
|---|---|
| Ngôn ngữ | Python 3.12 |
| HTTP | `requests` + `urllib3 Retry` (hoặc `httpx`) |
| HTML parsing | `beautifulsoup4` + `lxml` |
| Scheduler | `APScheduler` 3.x |
| State | SQLite (stdlib `sqlite3`) |
| Env | `python-dotenv` |
| Timezone | `zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")` |
| Test | `pytest` + `responses`/`requests-mock` |
| Container | Docker + Docker Compose |

---

## 5. Định dạng tin nhắn Telegram

### 5.1 Digest 06:00 (có lịch)

```
📅 LỊCH HỌC HÔM NAY
Thứ Hai, 09/11/2026

🕐 07:30–09:00 · Tiết 1–3
📘 IT001.R124 – Nhập môn lập trình
📍 B2.12 · GV: Trần Gia Bảo

🕐 13:00–16:10 · Tiết 6–9
📘 MA006.R125 – Giải tích
📍 B4.16 · GV: TS Lê Huỳnh Mỹ Vân

📝 LỊCH THI HÔM NAY
• 09:30–11:30 · MA006 – Giải tích
  📍 B3.10 · Ca 2 · Tự luận

🔗 https://portal.uit.edu.vn/sinh-vien/tkb
```

### 5.2 Digest (chỉ có lịch thi)

```
📝 LỊCH THI HÔM NAY
Thứ Sáu, 06/11/2026

• 09:30–11:30 · MA006 – Giải tích
  📍 B3.10 · Ca 2 · Tự luận

🔗 https://portal.uit.edu.vn/sinh-vien/lich-thi
```

### 5.3 Thông báo mới (chung)

```
🔔 THÔNG BÁO MỚI

Thông báo nghỉ lớp Hệ thống tìm kiếm, phát hiện và ngăn ngừa xâm nhập (NT204.R11.ANTT.2) ngày 09/11/2026

🏷 Thông báo Nghỉ - Bù · Đại học chính quy
🏢 Khoa Mạng máy tính và Truyền thông
🕐 16:19, 07/11/2026

🔗 https://portal.uit.edu.vn/bai-viet/thong-bao-nghi-lop-...
```

### 5.4 Thông báo ảnh hưởng lớp của sinh viên

```
⚠️ THÔNG BÁO ẢNH HƯỞNG LỚP CỦA BẠN

Thông báo nghỉ lớp Nhập môn lập trình (IT001.R124) ngày 12/11/2026

🏷 Thông báo Nghỉ - Bù
🕐 15:37, 06/11/2026

🔗 https://portal.uit.edu.vn/bai-viet/...
```

### 5.5 Thay đổi TKB

```
⚠️ THAY ĐỔI THỜI KHÓA BIỂU
Thứ Tư, 11/11/2026

❌ HỦY
IT001.R124.1 – Nhập môn lập trình
Tiết 1–5 · B2.12

✅ THÊM
IT001.R124 – Nhập môn lập trình
Tiết 6–8 · B1.18 · GV: TS Mai Tiến Dũng

🔄 ĐỔI PHÒNG
MA006.R125 – Giải tích
Tiết 6–9 · B4.16 → B4.20
```

### 5.6 Thay đổi lịch thi

```
📝 LỊCH THI MỚI

MA006 – Giải tích
🕐 09:30–11:30 · Thứ Sáu, 06/11/2026
📍 B3.10 · Ca 2 · Tự luận

🔗 https://portal.uit.edu.vn/sinh-vien/lich-thi
```

### 5.7 Cảnh báo lỗi

```
⚠️ UIT CHECKER

Không đăng nhập được vào cổng sau 3 lần thử.
Vui lòng kiểm tra mật khẩu hoặc phiên SSO.

Thời gian: 07/11/2026 23:30
```

### 5.8 Startup

```
✅ UIT CHECKER ĐÃ KHỞI ĐỘNG

Tài khoản: 26522248
Theo dõi: TKB · Lịch thi · Thông báo
Digest: 06:00 hằng ngày (Asia/Ho_Chi_Minh)
Chế độ thông báo: relevant
```

### 5.9 Quy tắc format

- HTML parse mode; escape `&`, `<`, `>`.
- Tin tối đa 4096 ký tự; chia nhỏ nếu dài.
- Không dùng Markdown để tránh lỗi parse.
- `disable_web_page_preview=true` để tin gọn.
- Ngày giờ hiển thị `dd/mm/yyyy HH:MM` theo giờ Việt Nam.
- Tên môn normalize non-breaking space.
- Với TKB: hiển thị `Tiết X–Y` + giờ bắt đầu/kết thúc.
- Với lịch thi: hiển thị `start_time–end_time`, không dùng tiết.

---

## 6. Cấu hình

### 6.1 `.env.example`

```dotenv
# ===== UIT =====
UIT_USERNAME=26522248
UIT_PASSWORD=__CHANGE_ME__

# ===== Telegram =====
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=123456789
# TELEGRAM_MESSAGE_THREAD_ID=

# ===== Scheduler (giây) =====
ANNOUNCEMENT_POLL_SECONDS=120
TKB_POLL_SECONDS=900
EXAM_POLL_SECONDS=1800

# ===== Digest =====
DIGEST_CRON=0 6 * * *
DIGEST_SEND_EMPTY=true
DIGEST_CATCHUP_UNTIL=12:00

# ===== Announcements =====
ANNOUNCEMENT_MODE=relevant
ANNOUNCEMENT_PAGE_SIZE=50
ANNOUNCEMENT_MAX_PAGES=5

# ===== Auth =====
AUTH_RELOGIN_MARGIN_MINUTES=15
AUTH_MAX_FAILURES=3
AUTH_ALERT_COOLDOWN_MINUTES=60

# ===== TKB / Exam =====
TKB_POLL_NEXT_WEEK=true
TKB_CONFIRM_REMOVALS=true
EXAM_YEARS=auto
EXAM_SEMESTERS=all

# ===== Runtime =====
TZ=Asia/Ho_Chi_Minh
LOG_LEVEL=INFO
STATE_DB_PATH=./data/state.db
DRY_RUN=false
HEARTBEAT_ENABLED=false
```

### 6.2 Quy tắc secret

- `.env` không commit; thêm vào `.gitignore`.
- Không đưa mật khẩu/token vào `spec.md`, README, log, screenshot.
- Nếu repo được chia sẻ, dùng secret manager (1Password, Docker secrets, GitHub Actions secrets...).
- Mật khẩu có ký tự `!`: trong file `.env` không cần escape; nếu export trong bash, đặt trong dấu nháy đơn (`'...'`) để tránh history expansion.
- Nên đổi mật khẩu định kỳ; nếu nghi ngờ lộ, đổi ngay và cập nhật `.env`.

### 6.3 Giá trị mặc định

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `ANNOUNCEMENT_POLL_SECONDS` | 120 | Tối thiểu 60 |
| `TKB_POLL_SECONDS` | 900 | 15 phút |
| `EXAM_POLL_SECONDS` | 1800 | 30 phút |
| `DIGEST_CRON` | `0 6 * * *` | 06:00 hằng ngày |
| `DIGEST_SEND_EMPTY` | true | Gửi cả ngày không có lịch |
| `DIGEST_CATCHUP_UNTIL` | 12:00 | Gửi bù trước 12:00 |
| `ANNOUNCEMENT_MODE` | relevant | relevant / all |
| `TKB_CONFIRM_REMOVALS` | true | Xác nhận 2 lần |
| `EXAM_YEARS` | auto | Năm hiện tại (+ năm sau nếu tháng 11–12) |
| `EXAM_SEMESTERS` | all | 1, 2, 3 |
| `AUTH_MAX_FAILURES` | 3 | Trước khi cảnh báo/cooldown |
| `HEARTBEAT_ENABLED` | false | Tránh spam |

---

## 7. Triển khai 24/7

### 7.1 Docker (khuyến nghị)

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Ho_Chi_Minh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY .env.example ./.env.example

RUN mkdir -p /app/data /app/logs

CMD ["python", "-m", "src.main"]
```

`docker-compose.yml`:

```yaml
services:
  uit-checker:
    build: .
    container_name: uit-checker
    restart: unless-stopped
    env_file:
      - .env
    environment:
      TZ: Asia/Ho_Chi_Minh
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

Chạy:

```bash
docker compose up -d --build
docker compose logs -f
```

- `restart: unless-stopped` đảm bảo tự chạy lại sau reboot/crash.
- Volume giữ `state.db` và log.
- Không dùng `--reload` trong production.

### 7.2 Linux systemd

```ini
# /etc/systemd/system/uit-checker.service
[Unit]
Description=UIT Portal Checker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=uitchecker
WorkingDirectory=/opt/uit_checker
EnvironmentFile=/opt/uit_checker/.env
ExecStart=/opt/uit_checker/.venv/bin/python -m src.main
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uit-checker
sudo journalctl -u uit-checker -f
```

### 7.3 Windows (máy luôn bật)

- Cách 1: Docker Desktop + `docker compose up -d`.
- Cách 2: NSSM (Non-Sucking Service Manager):
  - Tạo service trỏ tới `python.exe -m src.main`.
  - Startup directory: `E:\uit_checker`.
  - Restart on failure.
- Cách 3: Task Scheduler:
  - Trigger: At startup.
  - Action: `powershell -ExecutionPolicy Bypass -File E:\uit_checker\run.ps1`.
  - Bật "Run whether user is logged on or not".
  - Bật "Restart on failure".
- Lưu ý: PC phải không sleep/hibernate; nếu không, nên dùng VPS Linux.

### 7.4 Health check

- File `data/heartbeat` cập nhật mỗi phút.
- Log dòng `HEARTBEAT` mỗi 5 phút (tùy chọn).
- Docker healthcheck (Phase 2):
  ```yaml
  healthcheck:
    test: ["CMD", "python", "-m", "src.healthcheck"]
    interval: 60s
    timeout: 10s
    retries: 3
  ```
- Khuyến nghị: dùng UptimeRobot/Better Stack ping một endpoint `/healthz` nếu triển khai Phase 2.

---

## 8. Xử lý lỗi và edge cases

| Tình huống | Hành vi |
|---|---|
| Portal trả 503/502/500 | Retry backoff; không cập nhật state; không gửi tin hủy |
| Timeout mạng | Retry; log; tiếp tục job sau |
| `ums_portal_session` hết hạn | Tự đăng nhập lại; retry request một lần |
| Sai mật khẩu | Cảnh báo sau 3 lần; cooldown 60 phút; không spam |
| SSO có OTP/captcha | Báo lỗi rõ; dừng auto-login; cần can thiệp |
| TKB tuần rỗng | Hợp lệ (thi giữa kỳ/lễ); vẫn gửi digest "không có lịch" nếu `DIGEST_SEND_EMPTY=true` |
| TKB API trả sai `startOfWeek` | Coi là lỗi; không dùng dữ liệu |
| TKB API lỗi nhưng có snapshot cũ | Digest dùng snapshot ≤ 24h + cảnh báo |
| Lịch thi API lỗi | Giữ dữ liệu cũ; không báo hủy |
| Lịch thi trùng | Dedupe theo composite key |
| `date` lịch thi `dd/mm/yyyy` | Parse thủ công; không dùng `Date.parse` |
| Thông báo cũ 22k | Baseline lần đầu; không gửi |
| `category=all` | Không dùng; gọi không category |
| Nhiều thông báo mới | Batch nếu > 3 |
| Telegram 429 | Chờ `retry_after` |
| Telegram token sai | Log lỗi; không crash |
| Restart giữa chừng | Dedup DB; không gửi lại |
| Digest 06:00 bị lỡ | Gửi bù trước 12:00 |
| Múi giờ server khác | Luôn dùng `Asia/Ho_Chi_Minh` |
| Đổi mùa học kỳ | Refresh metadata; baseline mới; không báo xóa hàng loạt |
| Ngày lễ | `ngayLe` nếu có; hiển thị trong digest |
| Buổi học đã qua | Không gửi thông báo thay đổi |
| Thông báo nghỉ lớp khác | Bỏ qua ở chế độ `relevant` |

---

## 9. Kiểm thử

### 9.1 Unit tests

- Parse login form (fixture HTML).
- Parse TKB response (fixture JSON) → sessions đúng.
- Parse exam response → date `dd/mm/yyyy` đúng.
- Parse announcement response → link đúng.
- Diff TKB: thêm/xóa/đổi phòng/đổi tiết.
- Diff exam: thêm/đổi/xóa.
- Filter announcement: relevant/all, match mã lớp.
- Dedup: cùng event key không gửi 2 lần.
- Formatter: HTML escape, chia nhỏ tin > 4096 ký tự.
- Digest: có lớp, chỉ thi, không có gì, lỗi API.

### 9.2 Integration tests

- Mock toàn bộ portal API bằng `responses`:
  - Login flow 302 → callback → 200.
  - 401 → re-login → retry.
  - 503 → không cập nhật state.
- Mock Telegram:
  - 200 OK.
  - 429 → retry đúng.
  - 500 → retry/backoff.
- SQLite: restart giữa chừng, không gửi trùng.

### 9.3 Manual test checklist

- [ ] `--check-auth` đăng nhập thành công.
- [ ] `--test-telegram` nhận tin.
- [ ] `--once` chạy một chu kỳ, log đúng.
- [ ] `--digest-now` gửi digest đúng.
- [ ] Tạo thông báo mới (hoặc sửa fixture) → nhận tin trong ≤ 4 phút.
- [ ] Sửa TKB trên portal (hoặc mock) → nhận tin thay đổi.
- [ ] Sửa lịch thi (hoặc mock) → nhận tin thay đổi.
- [ ] Restart tool → không gửi lại.
- [ ] Tắt mạng 5 phút → tool không crash, tự phục hồi.
- [ ] Kiểm tra log không chứa mật khẩu/token/cookie.

---

## 10. Bảo mật và tuân thủ

- Đây là tool cá nhân, không phải sản phẩm chính thức của UIT.
- Chỉ dùng tài khoản của chính sinh viên; không chia sẻ credential.
- Không scrape quá mức; poll tối thiểu 60 giây/lần cho announcements, 15 phút cho TKB.
- Không vượt captcha/2FA; không tấn công, không dò mật khẩu.
- Không log dữ liệu nhạy cảm:
  - Mật khẩu, token, cookie, header `Cookie`, `Authorization`.
  - Nội dung cá nhân không cần thiết.
- Mã hóa/giới hạn quyền file `.env` và `state.db` (chmod 600 trên Linux).
- Nếu dùng VPS, bật firewall, cập nhật hệ điều hành, không mở port nếu không cần.
- Nếu phát hiện bất thường (đăng nhập thất bại, lạ), đổi mật khẩu portal.

---

## 11. Kế hoạch triển khai

### Phase 0 — Chuẩn bị

- [ ] Xác nhận spec.
- [ ] Cung cấp `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- [ ] Chọn môi trường chạy 24/7 (VPS Linux / Docker / Windows luôn bật).
- [ ] Xác nhận chế độ thông báo (`relevant` / `all`).

### Phase 1 — MVP

- [ ] Login + session manager.
- [ ] TKB client + parser.
- [ ] Exam client + parser.
- [ ] Announcement client + baseline + filter.
- [ ] Telegram notifier + dedup.
- [ ] Digest 06:00 + catch-up.
- [ ] SQLite state.
- [ ] Docker/systemd.
- [ ] Logging.

### Phase 2 — Hardening

- [ ] Xác nhận 2 lần cho xóa/đổi.
- [ ] Batch thông báo.
- [ ] Cảnh báo lỗi auth/portal.
- [ ] Test đầy đủ.
- [ ] Health check endpoint.
- [ ] Tự động prune dữ liệu cũ.

### Phase 3 — Mở rộng (optional)

- [ ] Tự động ẩn buổi học bị hủy theo thông báo nghỉ/bù (parse nội dung bài viết).
- [ ] Hỗ trợ nhiều tài khoản.
- [ ] Web UI nhỏ để xem trạng thái/lịch sử.
- [ ] Đồng bộ Google Calendar.
- [ ] Thông báo qua Zalo/Email.
- [ ] Theo dõi thêm: lịch sinh hoạt, học phí, điểm.

---

## 12. Quyết định đã chốt và câu hỏi còn lại

**Đã chốt:**

1. Telegram bot token + chat ID: đã cấu hình trong `.env` (bot `@uit666bot`).
2. Phạm vi thông báo: `relevant`.
3. Gửi thông báo khi TKB/lịch thi thay đổi: có.
4. Ngày không có lịch: gửi tin "Hôm nay không có lịch".

**Còn lại (không chặn MVP):**

1. **Môi trường chạy 24/7**: VPS Linux, Docker trên máy cá nhân, hay Windows luôn bật? MVP sẽ kèm cả Docker, systemd và script Windows; chọn một khi triển khai thực tế.
2. **Có cần theo dõi thêm "Lịch sinh hoạt"** (`/sinh-vien/lich-sinh-hoat`) không? (Phase 3.)
3. **Có cần gửi digest vào cuối tuần/ngày lễ** không? (Mặc định vẫn chạy theo lịch; ngày không có lịch sẽ gửi tin "không có lịch".)
4. **Mật khẩu portal**: có muốn đổi mật khẩu sau khi cấu hình tool không? (Khuyến nghị nếu đã chia sẻ ở nơi khác.)

---

## 13. Phụ lục

### 13.1 Tóm tắt endpoint

| Mục đích | Method | Endpoint | Auth |
|---|---|---|---|
| Bắt đầu login | GET | `/api/auth/login?returnTo=%2Fsinh-vien%2Ftkb` | Không |
| SSO form | POST | `https://sso.uit.edu.vn/realms/UIT/login-actions/authenticate?...` | Không |
| Callback | GET | `/api/auth/callback/keycloak?code=...` | Cookie tạm |
| Đăng ký học phần | GET | `/api/sinh-vien/tkb/dang-ky` | Có |
| TKB tuần | GET | `/api/sinh-vien/tkb?hocKy=1&namHoc=2026&yearId=47&startDate=YYYY-MM-DD` | Có |
| Lịch thi | POST | `/api/sv/exam-schedule` | Có |
| Thông báo | GET | `/api/public/announcements?page=1&limit=50` | Không |
| Chi tiết thông báo | GET | `/bai-viet/{slug}` | Không |
| Trang TKB | GET | `/sinh-vien/tkb` | Có |
| Trang lịch thi | GET | `/sinh-vien/lich-thi` | Có |

### 13.2 Ví dụ gọi API bằng Python

```python
import requests

BASE = "https://portal.uit.edu.vn"
s = requests.Session()

# 1) Login flow
r = s.get(f"{BASE}/api/auth/login?returnTo=%2Fsinh-vien%2Ftkb", allow_redirects=True)
# ... parse form, POST username/password ...
# Sau khi login thành công:

# 2) TKB tuần
r = s.get(
    f"{BASE}/api/sinh-vien/tkb",
    params={
        "hocKy": 1,
        "namHoc": 2026,
        "yearId": 47,
        "startDate": "2026-11-09",
    },
    timeout=30,
)
tkb = r.json()

# 3) Lịch thi
r = s.post(
    f"{BASE}/api/sv/exam-schedule",
    json={
        "academic": [],
        "year_name": "2026",
        "examination": "midterm",
        "semester": "semester_1",
    },
    timeout=30,
)
exams = r.json()

# 4) Thông báo (public, có thể dùng session riêng)
r = requests.get(
    f"{BASE}/api/public/announcements",
    params={"page": 1, "limit": 50},
    timeout=30,
)
announcements = r.json()
```

### 13.3 Ví dụ event key

| Loại | Event key |
|---|---|
| Thông báo | `announcement:23174` |
| Lịch thi mới | `exam:new:2026:semester_1:midterm:MA006|MA006.R125|06/11/2026|2|B3.10` |
| Lịch thi đổi | `exam:changed:...:<hash>` |
| TKB thêm | `tkb:add:2026-11-09:IT001.R124:6-8` |
| TKB đổi | `tkb:change:2026-11-09:IT001.R124:6-8:<hash>` |
| TKB xóa | `tkb:remove:2026-11-09:IT001.R124:6-8` |
| Digest | `digest:2026-11-09` |
| Cảnh báo auth | `alert:auth:2026-11-07T23:30` |

### 13.4 Checklist trước khi chạy production

- [ ] `.env` đã điền đủ và không nằm trong git.
- [ ] Telegram test thành công.
- [ ] Login test thành công.
- [ ] `--once` chạy không lỗi.
- [ ] Digest test đúng nội dung.
- [ ] State DB ghi được vào volume.
- [ ] Log rotation hoạt động.
- [ ] Service tự restart sau reboot.
- [ ] Kiểm tra lại sau 24h: không gửi trùng, không bỏ lỡ.

---

## 14. Tình trạng triển khai (2026-11-07)

- MVP đã được code tại `E:\uit_checker`.
- Đang chạy nền trên Windows; có shortcut tự chạy khi đăng nhập:
  `C:\Users\<user>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\UIT Checker.cmd`.
- Có sẵn `Dockerfile`, `docker-compose.yml` và hướng dẫn systemd cho VPS Linux.
- Đã kiểm tra thực tế:
  - Đăng nhập portal thành công.
  - Baseline TKB/lịch thi/thông báo, không spam dữ liệu cũ.
  - Gửi Telegram test, startup, digest.
  - Chống chạy trùng bằng single-instance lock.
  - Poll TKB/lịch thi/thông báo theo scheduler.
  - Courses UIT: đăng nhập OAuth2 thành công; baseline 28 notification + 1 tin nhắn; poll định kỳ không lỗi.
- Lệnh kiểm tra nhanh:
  ```powershell
  cd E:\uit_checker
  python -m src.main --check-auth
  python -m src.main --check-courses
  python -m src.main --test-telegram
  python -m src.main --once
  python -m src.main --courses-once
  python -m src.main --digest-now
  ```
- Lưu ý: `install_task.ps1` cần quyền Administrator; shortcut trong Startup folder không cần quyền admin.

**Hết spec.**
