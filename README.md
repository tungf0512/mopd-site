# Đài Quan Sát MOPD — website có đăng nhập & lưu tiến độ

Gói này gồm website khoá học và một server nhỏ (Flask + SQLite) cho phép:
- đăng ký, đăng nhập, đăng xuất (mật khẩu được băm một chiều bằng scrypt, không lưu dạng gốc);
- tự động lưu **bài đã hoàn thành, bài đang học, điểm quiz** vào tài khoản — mở trên máy nào cũng học tiếp được;
- khi đăng nhập, tiến độ đang học dở (chưa đăng nhập) được **gộp** vào tài khoản chứ không bị mất.

```
mopd-site/
├── server.py              # server: trang web + API tài khoản + API tiến độ
├── requirements.txt
├── static/index.html      # website khoá học
├── deploy/mopd.service    # chạy nền bằng systemd
├── deploy/cloudflared-config.yml
└── data/                  # tự tạo khi chạy: mopd.db (CSDL) và secret_key
```

## 1. Chạy thử trên máy (khoảng 5 phút)

```bash
cd mopd-site
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Mở http://127.0.0.1:8000, bấm **Đăng ký** ở cuối thanh bên trái (hoặc biểu tượng 👤 trên điện thoại).

## 2. Quản lý tài khoản

```bash
python server.py create-user ten_cua_ban     # tạo tài khoản (hỏi mật khẩu)
python server.py reset-password ten_cua_ban  # đổi mật khẩu
python server.py list-users                  # xem danh sách
```

Nếu chỉ mình bạn dùng, hãy tạo tài khoản bằng lệnh trên rồi **tắt đăng ký công khai** bằng biến môi trường `MOPD_ALLOW_REGISTER=0` (file `deploy/mopd.service` đã đặt sẵn).

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `MOPD_ALLOW_REGISTER` | `1` | `0` để tắt đăng ký trên web |
| `MOPD_COOKIE_SECURE` | `0` | `1` khi chạy sau HTTPS (tunnel) — cookie chỉ gửi qua HTTPS |
| `MOPD_BEHIND_CLOUDFLARE` | `0` | `1` khi chạy sau Cloudflare, để chặn đoán mật khẩu theo đúng IP người dùng |
| `MOPD_DATA_DIR` | `./data` | nơi lưu CSDL và secret key |
| `MOPD_HOST`, `MOPD_PORT` | `127.0.0.1`, `8000` | địa chỉ lắng nghe khi chạy `python server.py` |

## 3. Chạy lâu dài trên server (gunicorn + systemd)

```bash
sudo mkdir -p /opt/mopd-site && sudo chown $USER /opt/mopd-site
cp -r mopd-site/* /opt/mopd-site/ && cd /opt/mopd-site
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py create-user ten_cua_ban
# sửa YOUR_USER trong deploy/mopd.service, rồi:
sudo cp deploy/mopd.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mopd
curl http://127.0.0.1:8000/healthz          # phải trả về {"ok":true}
```

Lưu ý: `mopd.service` đặt `MOPD_COOKIE_SECURE=1`, nên đăng nhập chỉ giữ được khi truy cập qua **HTTPS** (bước 4). Muốn thử trực tiếp qua `http://127.0.0.1`, tạm đổi thành `0`.

## 4. Đưa lên internet và có tên miền

Server chỉ lắng nghe ở `127.0.0.1`, **không cần mở cổng** trên máy. Cloudflare Tunnel tạo một kết nối ra ngoài và cấp đường dẫn HTTPS.

### Cách A — đường dẫn tạm thời trong 1 phút (để thử)

Cài `cloudflared` theo hướng dẫn chính thức của Cloudflare, rồi chạy:

```bash
cloudflared tunnel --url http://localhost:8000
```

Lệnh in ra một địa chỉ dạng `https://<chuoi-ngau-nhien>.trycloudflare.com` — đó là **domain của bạn** trong lúc lệnh còn chạy. Không cần tài khoản Cloudflare, nhưng địa chỉ đổi mỗi lần chạy và chỉ dành cho thử nghiệm.

### Cách B — tên miền cố định (khuyến nghị)

1. Mua một tên miền (Cloudflare Registrar, Namecheap, hoặc nhà cung cấp Việt Nam) và thêm vào Cloudflare (đổi nameserver theo hướng dẫn).
2. Tạo tunnel và gắn tên miền:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create mopd
   cloudflared tunnel route dns mopd learn.ten-mien-cua-ban.com
   ```
3. Sửa `deploy/cloudflared-config.yml` (UUID của tunnel, đường dẫn file credentials, tên miền), chép vào `/etc/cloudflared/config.yml`, rồi:
   ```bash
   sudo cloudflared service install
   ```
4. Website chạy tại `https://learn.ten-mien-cua-ban.com`.

Các lệnh của `cloudflared` có thể thay đổi theo phiên bản — nếu có khác biệt, làm theo tài liệu Cloudflare Tunnel mới nhất.

### Cách C — không chạy trên máy GPU

Dùng một VPS Linux nhỏ và làm y hệt bước 3–4. Nếu dùng nền tảng PaaS, cần một **ổ đĩa lưu trữ bền** cho thư mục `data/` (SQLite).

## 5. Vận hành & bảo mật

- **Sao lưu tiến độ:** `sqlite3 data/mopd.db ".backup data/backup.db"` (định kỳ, ví dụ bằng cron).
- **Cập nhật nội dung khoá học:** chỉ cần thay `static/index.html` — không cần khởi động lại server.
- **Giữ bí mật `data/secret_key`:** lộ file này thì người khác có thể giả mạo phiên đăng nhập. Không đưa thư mục `data/` lên Git.
- **Chặn đoán mật khẩu:** 10 lần sai trong 5 phút mỗi IP (bộ đếm nằm trong bộ nhớ của từng worker gunicorn).
- **Muốn thêm một lớp khoá nữa:** có thể bật Cloudflare Access (Zero Trust) để chỉ email của bạn mới vào được trang.
- Mở file `index.html` trực tiếp (không qua server) vẫn xem được toàn bộ khoá học, nhưng không đăng nhập và không lưu tiến độ.

## 6. API (tham khảo)

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/api/me` | người dùng hiện tại và việc có cho đăng ký không |
| POST | `/api/register`, `/api/login`, `/api/logout` | tài khoản (JSON: `username`, `password`) |
| GET / POST | `/api/progress` | đọc / lưu `{completed, current, quiz}` |
| GET | `/healthz` | kiểm tra server còn sống |

Mọi yêu cầu POST phải có header `X-Requested-With: mopd` (chống CSRF); website tự gửi header này.

## 7. GitHub Pages (bản tĩnh)

Workflow `.github/workflows/pages.yml` xuất riêng thư mục `static/` lên GitHub Pages mỗi khi có commit vào nhánh `main`. Trên Pages, khoá học và tiến độ khách được lưu trong `localStorage` của trình duyệt; đăng nhập và đồng bộ tiến độ qua SQLite vẫn cần chạy `server.py` trên một máy chủ riêng.
