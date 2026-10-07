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

## Báo cáo tài nguyên và chi phí

Mỗi sáng 8 giờ (giờ Việt Nam) bot gửi cho admin số liệu của ngày hôm trước và tháng này (`usage.py`, lệnh `/usage`
để xem ngay): giờ GPU Modal của KataGo (app tự đo thời gian mỗi lần gọi, cộng 2 phút GPU chờ trước khi tắt) và chi phí
quy ra đô la so với phần miễn phí hằng tháng của Modal, lượt gọi từng model AI và số lần hết lượt miễn phí (429), giọng
nói, giờ OCPU / RAM của VM Oracle so với mức Always Free, ổ đĩa, số người dùng. Chi phí cả tháng (theo dương lịch) được
dự kiến theo nhịp đã dùng; ngân sách mặc định là 0 (chỉ dùng gói miễn phí). Bot cảnh báo ngay (mỗi cảnh báo một lần
một ngày) khi đã hoặc sắp vượt ngân sách, khi Modal dự kiến dùng quá 80 % phần miễn phí, khi giờ GPU 7 ngày qua tăng
mạnh, khi nhiều lần hết lượt AI miễn phí hoặc ổ đĩa quá 80 %. Số liệu ở `/srv/go-scan/usage/<YYYY-MM>.json` (các tiến
trình khác như `python -m joseki build` ghi `<YYYY-MM>.<tên>.json`, được cộng vào nhưng không nhân lên khi dự kiến).

Giờ GPU do app tự đo chỉ là ước tính: thỉnh thoảng nhập số thật từ dashboard Modal (Usage & billing → Total Usage)
bằng lệnh bot `/modal 16.16`; báo cáo lấy số đó làm mốc rồi cộng phần ước tính sau đó. Workspace Modal đặt spend limit
$0: hết $30 credit là Modal dừng mọi workload, KataGo ngừng tới hết tháng — các cảnh báo báo trước ngày hết credit.
Dự kiến chỉ được tin (và mới cảnh báo) sau 2 ngày đếm.
Tham số: `USAGE_BUDGET_USD`, `MODAL_FREE_CREDIT_USD` (30), `MODAL_GPU_USD_PER_HOUR` (0,80), `ORACLE_FREE_OCPU_HOURS`
(1500), `ORACLE_FREE_GB_HOURS` (9000).

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
