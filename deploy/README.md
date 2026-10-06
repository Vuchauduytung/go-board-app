# Go Scan: triển khai

Go Scan chạy trên **một VM Oracle Cloud Always Free** (https://lhm-go.duckdns.org): kiến trúc ở
[oracle/ARCHITECTURE.md](oracle/ARCHITECTURE.md), dựng từ đầu theo [oracle/README.md](oracle/README.md). KataGo chạy trên
GPU Modal (`katago/modal_katago.py`). Chạy thử ở máy nhà / LAN: `docker compose up -d --build` ở thư mục gốc (README).

## Đã gỡ khỏi Google Cloud (6/10/2026)

Bản Cloud Run cũ trong project `project-1a0ed8d8-d43b-4071-aec` đã xoá: service `go-scan` và `go-scan-bot`
(us-central1), Artifact Registry `go-scan`, bucket `<project>-go-scan` (sessions, góp ý, ảnh chụp, ảnh trang sách đã
chuyển hết sang `/srv/go-scan` và `books/pages` trên VM), service account `go-scan-run` / `go-katago-run`, cùng
`deploy/gcp.sh`, `deploy/iap-oauth.sh`, `infrastructure.puml` (còn trong lịch sử git). Webhook Telegram đã trỏ sang VM.

Project đó vẫn giữ, vì VM còn dùng hoặc làm bản dự phòng:

| Còn lại | Để làm gì |
|---|---|
| OAuth client **Go Scan Oracle** + màn hình đồng ý (Testing, Test users) | đăng nhập Google qua oauth2-proxy trên VM |
| Secret `go-scan-modal-token`, `go-scan-telegram-token`, `go-scan-telegram-secret` | bản sao dự phòng; VM đọc từ `deploy/oracle/.env` |
| Mọi thứ `cloud-bot-*` (Cloud Run `cloud-bot-telegram`, secret Gemini / Qdrant / Valkey) | app cloud-bot, không thuộc Go Scan |

## Góp ý của người dùng và bot admin Telegram

Tab **⭐ Đánh giá** của app nhận góp ý (sao + lời nhận xét) và hiện công khai cho mọi người. Góp ý ẩn danh không hiện
tên công khai, nhưng vẫn lưu email người viết để admin đọc. Mỗi góp ý là một file `feedback/<id>.json` trong
`/srv/go-scan` trên VM, cạnh `sessions/`.

Admin đọc và quản lý góp ý qua một bot Telegram riêng, bot chỉ trả lời chat id của admin. Các lệnh: `/reviews`,
`/stats`, `/summary` (AI tổng hợp), `/hide` · `/show` · `/delete <mã>`, hoặc nhắn câu hỏi tự do. Có góp ý mới thì bot
tự báo. Mỗi góp ý mới được **tự trả lời công khai** (AI viết: cảm ơn, ghi nhận, xin lỗi và xoa dịu khi người dùng phàn nàn;
không hứa thời hạn; AI lỗi thì dùng câu mẫu theo số sao), rồi bot báo cho admin kèm câu đã trả lời. Admin trả lời tay
trên web (nút ↩ Trả lời, có ✨ AI viết nháp) hoặc qua bot: `/draft <mã>`, `/reply <mã> <nội dung>`, `/unreply <mã>`;
bật / tắt tự trả lời: `/autoreply on|off` (lưu ở `feedback/settings.json`; mặc định theo `FEEDBACK_AUTO_REPLY`, bật).

Cài bot: @BotFather → /newbot lấy token; điền `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` (`openssl rand -hex 32`),
`TELEGRAM_ADMIN_IDS` trong `deploy/oracle/.env` (chưa biết chat id: nhắn /start cho bot, bot trả lời id), khởi động
lại app rồi đăng ký webhook và menu lệnh:

```bash
docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env exec app \
  python -m telegram_bot set-webhook https://lhm-go.duckdns.org/telegram/webhook
```

Caddy cho riêng đường dẫn `/telegram/webhook` đi thẳng vào app, không qua oauth2-proxy; app chỉ nhận request có đúng
secret đã đăng ký với Telegram và chỉ trả lời chat id của admin. Webhook là `https://<DOMAIN>/telegram/webhook`. Nếu dùng Cloudflare Access thì thêm
policy **Bypass** cho đường dẫn đó.

## Thêm / cập nhật sách

1. Đặt file vào `books/src/Go Books/…`, khai báo trong `rag/catalog.py` (sách scan: chạy `scripts/ocr-books.sh` trước).
2. `python -m rag.run_with_cloudbot_env rag.ingest_books --book <id>` (nhúng vector, đẩy lên Qdrant, xuất ảnh trang).
3. Chép ảnh trang lên VM: `rsync -a -e "ssh -i ~/.ssh/oracle-vm.key" books/pages/<id> ubuntu@138.2.89.60:~/go-board-app/books/pages/`

## Hạn mức người dùng

- Mỗi người dùng thường: 200.000 token AI/ngày (~25 câu hỏi về thế cờ, mỗi câu ~8.000 token vì kèm 60 biến KataGo;
  mỗi câu hỏi bằng giọng nói chuyển qua server tính thêm 500 token) và 150.000 lượt tìm KataGo/ngày (~3 lần review
  ván); kết quả lấy từ cache không bị tính. Admin (`ADMIN_EMAILS`) không giới hạn.
- KataGo: giải thích một nước tốn 4 lượt tìm kiếm (thế cờ, lựa chọn kém thay cho nó, thế cờ sau nó và các cách đáp
  kém); hỏi lại hoặc đi đúng biến đã tính thì lấy từ cache.
- Modal: GPU L4 tính tiền theo giây khi chạy, tự tắt khi không dùng (dùng credit miễn phí hằng tháng của Modal).
