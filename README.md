# UIT Portal Checker

Tool cá nhân theo dõi **thời khóa biểu**, **lịch thi** và **thông báo** trên [Cổng thông tin UIT](https://portal.uit.edu.vn), gửi thông báo qua **Telegram**.

## Tính năng

- Tự đăng nhập Cổng thông tin UIT qua Keycloak SSO và duy trì phiên.
- Kiểm tra TKB hằng ngày, phát hiện thêm/hủy/đổi phòng/đổi tiết/đổi giảng viên.
- Kiểm tra lịch thi giữa kỳ/cuối kỳ, phát hiện lịch mới hoặc thay đổi.
- Theo dõi feed thông báo công khai; mặc định chỉ gửi thông báo chung và thông báo nghỉ/bù đúng lớp của bạn (`ANNOUNCEMENT_MODE=relevant`).
- Theo dõi **Website môn học UIT** (`courses.uit.edu.vn`): chuông thông báo Moodle và tin nhắn riêng; gửi Telegram ngay khi có tin mới.
- Gửi digest lúc **06:00** hằng ngày (giờ Việt Nam), kể cả ngày không có lịch (theo cấu hình `DIGEST_SEND_EMPTY=true`).
- Chống gửi trùng sau restart; lưu trạng thái bằng SQLite.
- Chạy 24/7 bằng Docker, systemd hoặc Windows Task Scheduler.

## Cấu trúc

```
E:\uit_checker\
├─ spec.md               # spec tool portal
├─ spec_courses.md       # spec Courses UIT
├─ .env                  # secrets (không commit)
├─ .env.example          # mẫu cấu hình
├─ requirements.txt
├─ Dockerfile
├─ docker-compose.yml
├─ run.ps1
├─ start.cmd
├─ install_task.ps1
├─ src/
│  ├─ main.py
│  ├─ app.py
│  ├─ portal_client.py
│  ├─ courses_client.py
│  ├─ courses_checker.py
│  ├─ courses_formatter.py
│  ├─ parsers.py
│  ├─ detector.py
│  ├─ formatter.py
│  ├─ notifier.py
│  ├─ state.py
│  └─ config.py
├─ tests/
├─ data/                 # state.db, heartbeat (tự tạo)
└─ logs/                 # app.log (tự tạo)
```

## Cài đặt nhanh (Windows / Python)

Yêu cầu: Python 3.12+.

```powershell
cd E:\uit_checker
python -m pip install -r requirements.txt
```

Trong thư mục này `.env` đã được cấu hình sẵn cho tài khoản `26522248` và bot Telegram. **Không chạy `copy .env.example .env`** nếu không muốn ghi đè cấu hình thật.

Nếu triển khai ở máy khác, tạo `.env` từ mẫu rồi điền:

```powershell
copy .env.example .env
# Mở .env và điền UIT_USERNAME, UIT_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

### Kiểm tra

```powershell
python -m src.main --check-auth
python -m src.main --check-courses
python -m src.main --test-telegram
python -m src.main --once
python -m src.main --courses-once
python -m src.main --digest-now
```

### Chạy 24/7

```powershell
python -m src.main
```

hoặc:

```powershell
.\run.ps1
```

## Cấu hình quan trọng

Xem `.env.example`. Các biến chính:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `UIT_USERNAME` / `UIT_PASSWORD` | — | Tài khoản portal |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Bot và chat nhận tin |
| `ANNOUNCEMENT_MODE` | `relevant` | `relevant` hoặc `all` |
| `ANNOUNCEMENT_POLL_SECONDS` | `120` | Chu kỳ kiểm tra thông báo |
| `TKB_POLL_SECONDS` | `900` | Chu kỳ kiểm tra TKB |
| `EXAM_POLL_SECONDS` | `1800` | Chu kỳ kiểm tra lịch thi |
| `COURSES_ENABLED` | `true` | Bật/tắt checker Courses UIT |
| `COURSES_POLL_SECONDS` | `120` | Chu kỳ kiểm tra Courses |
| `COURSES_INCLUDE_MESSAGES` | `true` | Theo dõi cả tin nhắn riêng |
| `DIGEST_CRON` | `0 6 * * *` | Giờ gửi digest |
| `DIGEST_SEND_EMPTY` | `true` | Gửi tin khi không có lịch |
| `TZ` | `Asia/Ho_Chi_Minh` | Múi giờ |

## Docker

```bash
docker compose up -d --build
docker compose logs -f
```

- `restart: unless-stopped` tự chạy lại sau crash/reboot.
- Volume `./data` giữ `state.db`, `./logs` giữ log.
- Healthcheck dùng `src.healthcheck` (heartbeat trong 5 phút gần nhất).

## Linux systemd

Xem file mẫu trong `spec.md` mục 7.2, hoặc tạo service:

```ini
[Unit]
Description=UIT Portal Checker
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/uit_checker
EnvironmentFile=/opt/uit_checker/.env
ExecStart=/opt/uit_checker/.venv/bin/python -m src.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Windows: tự chạy khi đăng nhập

Cách nhanh (không cần quyền admin): tạo shortcut trong Startup folder:

```powershell
# File này đã được tạo sẵn:
# C:\Users\<user>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\UIT Checker.cmd
```

Hoặc chạy script có sẵn:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

`install_task.ps1` dùng Task Scheduler và cần quyền Administrator. Nếu không có quyền, dùng Startup folder hoặc Task Scheduler thủ công:

1. Task Scheduler → Create Task.
2. General: **Run whether user is logged on or not**.
3. Triggers: **At startup** (hoặc At log on).
4. Actions:
   - Program: `powershell.exe`
   - Arguments: `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File E:\uit_checker\run.ps1`
   - Start in: `E:\uit_checker`
5. Settings: **Restart on failure** nếu có.

Máy phải không sleep/hibernate. Nếu không, nên chạy trên VPS Linux.

## Log và trạng thái

- `logs/app.log` — log xoay vòng 14 ngày.
- `data/state.db` — baseline, snapshot TKB, lịch thi, notification dedup, Courses notifications/messages.
- `data/heartbeat` — cập nhật mỗi phút.

Không commit `.env`, `data/`, `logs/`.

## Gỡ lỗi

| Hiện tượng | Cách xử lý |
|---|---|
| Không đăng nhập được | Chạy `python -m src.main --check-auth`; kiểm tra mật khẩu/SSO |
| Không nhận Telegram | Chạy `--test-telegram`; kiểm tra token/chat ID; bot phải được `/start` |
| Portal 503 | Tool tự retry; kiểm tra log; không cần làm gì |
| Không có thông báo mới | Feed công khai có thể không có bài mới; kiểm tra `data/state.db` meta `announcement_max_id` |
| Courses không đăng nhập được | Chạy `python -m src.main --check-courses`; kiểm tra SSO/mật khẩu |
| Courses không có thông báo mới | Kiểm tra `courses_notification_max_id` trong `data/state.db`; tool không mark-read |
| Muốn gửi lại digest | `python -m src.main --digest-now` |

## Bảo mật

- Không commit `.env`; file này chứa mật khẩu portal và bot token.
- Không chia sẻ log/ảnh chụp có token.
- Chỉ dùng tài khoản của chính bạn; tôn trọng điều khoản của UIT.
- Nếu nghi ngờ lộ mật khẩu, đổi mật khẩu portal và cập nhật `.env`.
