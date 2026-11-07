# Spec — UIT Courses Notifier (Moodle)

- **Trạng thái:** MVP đã triển khai (2026-11-08)
- **Ngày:** 2026-11-08
- **Trang đích:** `https://courses.uit.edu.vn/?redirect=0`
- **Tài khoản:** dùng chung tài khoản portal `26522248`
- **Kênh nhận:** Telegram (dùng chung bot `@uit666bot` và chat ID đã cấu hình trong `.env`)
- **Múi giờ:** `Asia/Ho_Chi_Minh`

> Ghi chú: yêu cầu gốc ghi "báo về ot"; spec này hiểu là **Telegram** — cùng kênh với tool portal hiện tại. Nếu "ot" là kênh khác, cần xác nhận lại.

---

## 1. Tổng quan

### 1.1 Mục tiêu

Mở rộng tool hiện tại để theo dõi thêm **Website môn học UIT** (`courses.uit.edu.vn`), cụ thể:

1. Đăng nhập bằng **UIT SSO (OAuth2/Keycloak)**.
2. Kiểm tra liên tục **chuông thông báo Moodle**.
3. Kiểm tra **tin nhắn riêng** trong Moodle.
4. Gửi Telegram **ngay khi có thông báo/tin nhắn mới**.
5. Không gửi lại thông báo cũ, không đánh dấu đã đọc thay người dùng.

### 1.2 Phạm vi MVP

| Nguồn | Có trong MVP? | Ghi chú |
|---|---|---|
| Moodle notifications (chuông) | Có | `message_popup_get_popup_notifications` |
| Tin nhắn riêng | Có | `core_message_get_conversations` + `core_message_get_conversation_messages` |
| Bài đăng forum/diễn đàn | Một phần | Có nếu Moodle sinh notification (`mod_forum`) |
| Site news / "Tin tức chung" | Không | Có thể thêm Phase 2 nếu cần |
| Deadline/lịch | Một phần | Moodle đã sinh notification `assign_due_soon`, `assign_overdue` |
| Điểm, bài nộp, tài liệu | Không | Ngoài phạm vi |

### 1.3 Ngoài phạm vi

- Không tự động nộp bài, làm quiz, tải tài liệu.
- Không gửi tin nhắn, không đánh dấu đã đọc.
- Không đăng ký forum, không thay đổi cấu hình Moodle.
- Không hỗ trợ nhiều tài khoản trong MVP.
- Không xử lý captcha/2FA (nếu UIT bật sau này).

---

## 2. Quyết định thiết kế

1. **Kênh:** Telegram, dùng lại `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
2. **Tần suất:** poll mỗi **2 phút** (cấu hình `COURSES_POLL_SECONDS`, tối thiểu 60 giây).
3. **Baseline:** lần chạy đầu ghi nhận toàn bộ notification/message hiện có, **không gửi** để tránh spam (hiện tài khoản có 28 notification chưa đọc).
4. **Không mark-read:** chỉ đọc dữ liệu; không gọi API đánh dấu đã đọc.
5. **Tin nhắn:** gửi thông báo cho tin nhắn mới; bỏ qua tin do chính mình gửi.
6. **Không chặn tool portal:** nếu Courses lỗi, tool portal vẫn chạy bình thường.

---

## 3. Khảo sát nguồn dữ liệu

### 3.1 Đăng nhập OAuth2 qua UIT SSO

Luồng đã kiểm tra thực tế:

1. `GET https://courses.uit.edu.vn/?redirect=0`
   → chuyển tới `https://courses.uit.edu.vn/login/index.php`.
2. Trang login có:
   - `M.cfg.sesskey` (ví dụ `BxahUEbZnI`).
   - Link **UIT SSO**:
     `https://courses.uit.edu.vn/auth/oauth2/login.php?id=1&sesskey=<sesskey>&wantsurl=...`
3. `GET` link OAuth2 → chuyển tới Keycloak:
   `https://sso.uit.edu.vn/realms/UIT/protocol/openid-connect/auth?client_id=moodle&...`
4. Trang SSO có form `#kc-form-login`:
   - POST `username`, `password`, `login`.
5. Thành công → quay về `https://courses.uit.edu.vn/?redirect=0` (dashboard), set cookie:
   - `MoodleSession` (cookie phiên Moodle, không có expiry cố định).
   - Cookie SSO `KEYCLOAK_SESSION` (~24 giờ).
6. Dashboard có `M.cfg`:
   ```json
   {
     "wwwroot": "https://courses.uit.edu.vn",
     "sesskey": "47m43FeZYn",
     "sessiontimeout": "28800",
     "userId": 24513,
     "usertimezone": "Asia/Ho_Chi_Minh"
   }
   ```

**Thông tin phiên:**

| Mục | Giá trị |
|---|---|
| Cookie phiên Moodle | `MoodleSession` |
| Thời gian timeout Moodle | 28800 giây (~8 giờ) |
| SSO session | ~24 giờ |
| `sesskey` | Lấy từ `M.cfg.sesskey`, đổi theo phiên |
| `userId` | Lấy từ `M.cfg.userId` |

**Re-login:**

- Nếu dashboard trả về trang login hoặc AJAX trả `servicerequireslogin`/`invalidsesskey`:
  1. GET lại `/?redirect=0`.
  2. Parse `sesskey` + link UIT SSO.
  3. Follow OAuth2; nếu SSO session còn hiệu lực, có thể vào thẳng; nếu không, POST lại username/password.
  4. Cập nhật `sesskey`, `userId`, deadline phiên.
- Chủ động re-login trước khi hết 8 giờ (margin 15 phút), hoặc khi lỗi.

### 3.2 API thông báo (chuông Moodle)

**Endpoint:**

```http
POST https://courses.uit.edu.vn/lib/ajax/service.php?sesskey=<sesskey>&info=message_popup_get_popup_notifications
Content-Type: application/json
```

**Body:**

```json
[
  {
    "index": 0,
    "methodname": "message_popup_get_popup_notifications",
    "args": {
      "useridto": 24513,
      "limit": 50,
      "offset": 0,
      "newestfirst": true
    }
  }
]
```

**Response (rút gọn):**

```json
[
  {
    "error": false,
    "data": {
      "notifications": [
        {
          "id": 762675,
          "useridfrom": -10,
          "useridto": 24513,
          "subject": "Bạn đã nộp bài làm cho BT4 - Viết bài tiểu luận về \"Lộ trình nghề nghiệp\" của bản thân.",
          "shortenedsubject": "Bạn đã nộp bài làm cho BT4...",
          "text": "<p>Bạn đã nộp bài làm...</p>",
          "fullmessage": "EC005.R11 -> Bài tập -> BT4...",
          "fullmessagehtml": "<p>...</p>",
          "smallmessage": "Bạn đã nộp bài làm cho BT4...",
          "contexturl": "https://courses.uit.edu.vn/mod/assign/view.php?id=14746",
          "contexturlname": "BT4 - Viết bài tiểu luận...",
          "timecreated": 1793864013,
          "timecreatedpretty": "cách đây 2 ngày 9 giờ",
          "timeread": null,
          "read": false,
          "deleted": false,
          "iconurl": "...",
          "component": "mod_assign",
          "eventtype": "assign_notification",
          "customdata": "{\"cmid\":\"14746\",...}"
        }
      ],
      "unreadcount": 28
    }
  }
]
```

**Ghi chú quan trọng:**

- API trả **tất cả notification gần đây**, không chỉ chưa đọc; `read` có thể `false`/`true`.
- Gọi API **không** đánh dấu đã đọc; `unreadcount` vẫn giữ nguyên sau khi gọi (đã kiểm tra).
- Trang `/message/output/popup/notifications.php` render HTML ban đầu có thể rỗng; **dùng AJAX** là nguồn chính.
- Notification hiện tại gồm:
  - `mod_assign`: nộp bài, quá hạn, sắp đến hạn.
  - `mod_forum`: bài đăng diễn đàn (ví dụ `MA003.R125: Buổi học 29/10/26`).
  - `moodle`: đăng nhập mới (`newlogin`).
- `contexturl` có thể `null` (ví dụ newlogin) → dùng link trang thông báo.

### 3.3 API tin nhắn riêng

**Danh sách hội thoại:**

```http
POST /lib/ajax/service.php?sesskey=<sesskey>&info=core_message_get_conversations
```

Body:

```json
[
  {
    "index": 0,
    "methodname": "core_message_get_conversations",
    "args": {
      "userid": 24513,
      "limitfrom": 0,
      "limitnum": 50
    }
  }
]
```

Response (rút gọn):

```json
[
  {
    "error": false,
    "data": {
      "conversations": [
        {
          "id": 4448,
          "type": 1,
          "membercount": 2,
          "isread": true,
          "unreadcount": null,
          "members": [
            {
              "id": 6928,
              "fullname": "Dương Ngọc Hảo",
              "profileurl": "https://courses.uit.edu.vn/user/profile.php?id=6928"
            }
          ],
          "messages": [
            {
              "id": 1651,
              "useridfrom": 6928,
              "text": "<p>Chào các em,<br>T5 ngày 24/9 lớp mình học bình thường nhé</p>",
              "timecreated": 1790073886
            }
          ]
        }
      ]
    }
  }
]
```

**Lấy tin nhắn của một hội thoại (khi cần):**

```json
[
  {
    "index": 0,
    "methodname": "core_message_get_conversation_messages",
    "args": {
      "currentuserid": 24513,
      "convid": 4448,
      "limitfrom": 0,
      "limitnum": 10,
      "newest": true
    }
  }
]
```

- `unreadcount` trong `core_message_get_conversations` có thể `null`; không dựa vào đó.
- Có thể dùng `core_message_get_unread_conversation_counts` để lấy tổng unread, nhưng MVP theo dõi **message ID**.
- Không gọi `core_message_mark_message_read` / `message_popup_mark_notification_as_read`.

### 3.4 Lỗi phiên

Khi session hết hạn, AJAX trả HTTP 200 với:

```json
[
  {
    "error": true,
    "exception": {
      "message": "Dịch vụ web không tồn tại. (Phiên đăng nhập đã hết hạn hoặc đã đăng xuất).",
      "errorcode": "servicerequireslogin"
    }
  }
]
```

hoặc `errorcode: "invalidsesskey"`.

→ Phải re-login và retry một lần.

### 3.5 Link

| Mục | URL |
|---|---|
| Trang thông báo | `https://courses.uit.edu.vn/message/output/popup/notifications.php` |
| Hội thoại | `https://courses.uit.edu.vn/message/index.php?conversationid={id}` |
| Ngữ cảnh notification | `contexturl` (nếu có) |

---

## 4. Quy tắc phát hiện

### 4.1 Notification

- Lần đầu:
  - Fetch tối đa 50 notification mới nhất.
  - Lưu tất cả vào DB, đánh dấu `processed_at`, set `courses_notification_max_id`.
  - Không gửi Telegram.
- Các lần sau:
  - Fetch 50 notification mới nhất.
  - Item mới nếu `id` chưa có `processed_at`.
  - Lưu item, gửi Telegram, đánh dấu processed nếu gửi thành công.
  - Nếu gửi lỗi: giữ `processed_at = NULL` để retry lần sau; notification row ở bảng `notifications` giữ trạng thái `failed`.
- Nếu >50 item mới: page `offset` thêm (tối đa 5 trang), tránh bỏ sót.
- Không lọc theo `read`; notification mới có thể đã bị đọc bởi thao tác khác.

### 4.2 Tin nhắn

- Lần đầu:
  - Fetch danh sách hội thoại.
  - Với mỗi hội thoại có message, fetch 10 message mới nhất; lưu tất cả, đánh dấu processed; set `courses_message_max_id`.
  - Không gửi.
- Các lần sau:
  - Fetch conversations.
  - Với mỗi conversation:
    - Nếu message mới nhất có ID > `courses_message_max_id` hoặc chưa processed:
      - Fetch 10 message mới nhất của conversation.
      - Item mới nếu `id` chưa processed.
      - Bỏ qua message do chính user gửi (`useridfrom == userId`), nhưng vẫn đánh dấu processed.
      - Gửi Telegram cho message mới; đánh dấu processed nếu gửi thành công.
  - Cập nhật `courses_message_max_id`.
- Không gửi tin nhắn tự ghi chú (self conversation, type 3, không có message).
- Tên người gửi: lấy từ `members` theo `useridfrom`; nếu không tìm thấy, dùng "Ai đó".

### 4.3 Chống trùng

- Bảng `notifications` hiện có dùng event key:
  - `courses:noti:{id}`
  - `courses:msg:{id}`
- `processed_at` trong bảng courses tương tự announcements.
- Restart không gửi lại.
- Nếu gửi Telegram thành công nhưng crash trước khi mark processed: lần sau notification row đã `sent`, app sẽ mark processed mà không gửi lại.

---

## 5. Định dạng Telegram

### 5.1 Thông báo mới

```
🔔 THÔNG BÁO MỚI — COURSES UIT

Bạn đã nộp bài làm cho BT4 - Viết bài tiểu luận về "Lộ trình nghề nghiệp" của bản thân.

📚 EC005.R11 · Bài tập
🕐 20:13, 05/11/2026

🔗 https://courses.uit.edu.vn/mod/assign/view.php?id=14746
```

- Nếu `contexturl` null:
  `🔗 https://courses.uit.edu.vn/message/output/popup/notifications.php`
- Nếu `component`/`eventtype`:
  - `mod_assign` → "Bài tập"
  - `mod_forum` → "Diễn đàn"
  - `moodle` → "Hệ thống"
  - `mod_quiz` → "Bài kiểm tra"
  - Khác → component.

### 5.2 Tin nhắn mới

```
💬 TIN NHẮN MỚI — COURSES UIT

Từ: Dương Ngọc Hảo
Chào các em,
T5 ngày 24/9 lớp mình học bình thường nhé

🕐 10:24, 22/09/2026

🔗 https://courses.uit.edu.vn/message/index.php?conversationid=4448
```

- Text lấy từ `text` (HTML), strip tag, truncate 800 ký tự.
- Nếu là nhóm: thêm tên hội thoại (nếu có).

### 5.3 Cảnh báo lỗi

```
⚠️ COURSES UIT CHECKER

Không đăng nhập được Website môn học sau 3 lần thử.
Lỗi: ...

Thời gian: ...
```

---

## 6. Kiến trúc tích hợp

### 6.1 Thành phần mới

```
src/
├─ courses_client.py      # login OAuth2, sesskey, AJAX calls
├─ courses_checker.py     # baseline, poll, diff, notify
├─ courses_formatter.py   # format Telegram (có thể gộp vào formatter.py)
└─ state.py               # thêm bảng courses_notifications, courses_messages
```

### 6.2 Luồng

```
Scheduler
  └─ job_courses (mỗi 2 phút)
       ├─ CoursesClient.ensure_session()
       ├─ get_notifications()
       ├─ get_conversations()
       ├─ so sánh với SQLite
       ├─ TelegramNotifier
       └─ lưu processed/max_id
```

- Dùng chung `TelegramNotifier` và `State` của tool hiện tại.
- `CoursesClient` có `requests.Session` riêng; không dùng chung session portal (khác cookie).
- Có lock riêng `courses` để tránh chạy chồng.
- Nếu `COURSES_ENABLED=false`, bỏ qua hoàn toàn.

### 6.3 Scheduler

| Job | Tần suất | Ghi chú |
|---|---|---|
| `courses` | 120 giây | Poll notification + message |
| `courses_auth` | Khi cần | Re-login trong `ensure_session` |
| `courses_health` | 60 giây | Heartbeat chung |

- Nếu lỗi liên tiếp ≥ 6 lần: gửi cảnh báo Telegram (cooldown 60 phút).
- Nếu login sai ≥ 3 lần: cảnh báo, giãn thời gian thử.

---

## 7. Cấu hình

Thêm vào `.env` / `.env.example`:

```dotenv
# ===== Courses UIT =====
COURSES_ENABLED=true
COURSES_BASE_URL=https://courses.uit.edu.vn
COURSES_POLL_SECONDS=120
COURSES_NOTIFICATION_LIMIT=50
COURSES_INCLUDE_MESSAGES=true
COURSES_SESSION_REFRESH_MARGIN_MINUTES=15
```

- Dùng chung `UIT_USERNAME`, `UIT_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- `COURSES_POLL_SECONDS` tối thiểu 60.
- `COURSES_NOTIFICATION_LIMIT` tối đa 100.
- `COURSES_INCLUDE_MESSAGES=false` nếu chỉ muốn chuông thông báo.

---

## 8. Xử lý lỗi

| Tình huống | Hành vi |
|---|---|
| Moodle session hết hạn | Re-login OAuth2, retry 1 lần |
| `servicerequireslogin` / `invalidsesskey` | Re-login, retry |
| SSO form xuất hiện | POST username/password |
| Sai mật khẩu 3 lần | Cảnh báo Telegram, cooldown 60 phút, không spam |
| Courses 503/timeout | Retry backoff; giữ baseline; không gửi tin sai |
| AJAX trả HTML | Coi là mất phiên, re-login |
| Notification API lỗi | Không cập nhật max/processed; retry sau |
| Message API lỗi | Giữ dữ liệu cũ; retry sau |
| Telegram 429 | Chờ `retry_after` |
| Restart | Dedup bằng SQLite; không gửi lại |
| Notification bị xóa trên Moodle | Không ảnh hưởng; DB giữ lịch sử |
| User đọc notification trên web | API vẫn trả; tool không mark-read, không ảnh hưởng |
| Nhiều notification mới | Gửi từng tin; nếu >5 có thể gộp (tùy chọn) |

---

## 9. Acceptance criteria

- [ ] Đăng nhập Courses bằng UIT SSO thành công.
- [ ] Baseline 28 notification hiện tại + tin nhắn cũ, **không gửi** Telegram.
- [ ] Có notification mới → Telegram trong ≤ 2 chu kỳ poll (≤ 4 phút).
- [ ] Có tin nhắn mới → Telegram trong ≤ 4 phút.
- [ ] Không gửi lại sau restart.
- [ ] Không đánh dấu đã đọc notification/message.
- [ ] Courses lỗi không làm dừng tool portal.
- [ ] `--check-courses` báo đăng nhập OK.
- [ ] `--courses-once` chạy một lượt, log đúng.
- [ ] 9+ unit tests hiện tại vẫn pass; thêm test cho parser/dedup.

---

## 10. Kiểm thử

### 10.1 Unit tests

- Parse notification response (sample JSON) → đúng id/subject/contexturl/time.
- Parse conversations/messages → đúng sender, text, time.
- Format Telegram notification/message → đúng HTML escape.
- Dedup: cùng notification ID không gửi 2 lần.
- Baseline: lần đầu mark processed, không gửi.
- Self message: không gửi.
- Session error: giả lập `servicerequireslogin` → re-login.

### 10.2 Manual tests

```powershell
cd E:\uit_checker
python -m src.main --check-courses
python -m src.main --courses-once
python -m src.main --once
```

- Kiểm tra Telegram nhận tin.
- Kiểm tra `unreadcount` trước/sau khi poll không đổi.
- Restart, đảm bảo không gửi lại.

### 10.3 Fixtures

Lưu mẫu JSON vào `tests/fixtures/`:
- `courses_notifications.json`
- `courses_conversations.json`
- `courses_messages.json`

---

## 11. Câu hỏi mở

1. **"ot"** là Telegram đúng không? (Spec đang giả định Telegram.)
2. Có muốn nhận **tất cả notification**, kể cả `newlogin` (cảnh báo đăng nhập mới) không? Mặc định: có.
3. Có muốn nhận **tin nhắn riêng** không? Mặc định: có.
4. Có cần theo dõi **site news / forum** ngay cả khi Moodle không sinh notification không? Mặc định: không (Phase 2).
5. Có cần gửi **digest** cho Courses (ví dụ 06:00 tóm tắt thông báo mới) không? Mặc định: không; chỉ gửi ngay khi có tin.

---

## 12. Phụ lục — endpoint tóm tắt

| Mục đích | Method | Endpoint |
|---|---|---|
| Trang login | GET | `/login/index.php` |
| OAuth2 start | GET | `/auth/oauth2/login.php?id=1&sesskey=...` |
| Dashboard | GET | `/?redirect=0` |
| Notifications | POST | `/lib/ajax/service.php?sesskey=...&info=message_popup_get_popup_notifications` |
| Conversations | POST | `/lib/ajax/service.php?sesskey=...&info=core_message_get_conversations` |
| Conversation messages | POST | `/lib/ajax/service.php?sesskey=...&info=core_message_get_conversation_messages` |
| Unread counts | POST | `/lib/ajax/service.php?sesskey=...&info=core_message_get_unread_conversation_counts` |
| Trang thông báo | GET | `/message/output/popup/notifications.php` |
| Trang tin nhắn | GET | `/message/index.php` |

---

## 13. Tình trạng triển khai (2026-11-08)

- Đã code:
  - `src/courses_client.py` — login OAuth2, sesskey, AJAX Moodle.
  - `src/courses_checker.py` — baseline, poll notification/message, dedup, gửi Telegram.
  - `src/courses_formatter.py` — format tin nhắn.
  - `src/state.py` — bảng `courses_notifications`, `courses_messages`.
- Đã kiểm tra thực tế:
  - Đăng nhập Courses bằng UIT SSO thành công (`userId=24513`).
  - Baseline: **28 notification** + **1 tin nhắn**, không gửi Telegram.
  - Poll định kỳ 2 phút; `courses_last_poll_success` cập nhật, không lỗi.
  - Không gọi API mark-read; `unreadcount` không bị thay đổi bởi tool.
  - Tool portal + TKB/lịch thi vẫn chạy bình thường.
- Lệnh kiểm tra:
  ```powershell
  cd E:\uit_checker
  python -m src.main --check-courses
  python -m src.main --courses-once
  python -m src.main --once
  ```
- Lưu ý: 28 notification cũ được coi là baseline, không gửi để tránh spam; từ nay chỉ gửi notification/tin nhắn mới.

**Hết spec Courses.**
