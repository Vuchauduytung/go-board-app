# Go Scan trên Google Cloud

| Thành phần | Tài nguyên |
|---|---|
| Web + nhận dạng + chat | Cloud Run `go-scan` (us-central1, 2 vCPU / 4 GiB, min 0 / max 1, IAP) |
| Gợi ý nước đi | KataGo trên GPU Modal L4 (`katago/modal_katago.py`, tắt khi không dùng; token proxy trong secret `go-scan-modal-token`). Không có secret đó thì dùng Cloud Run `go-katago` (CPU, chậm) |
| Ảnh đã chụp, bàn cờ, ảnh trang sách | Bucket `<project>-go-scan` gắn vào `/data` (`/data/scans`, `/data/books/pages`) |
| Ảnh Docker | Artifact Registry `us-central1/go-scan` |
| Sách (vector) | Qdrant Cloud của cloud-bot, collection `go_books_e5` |
| Lịch sử chat, giới hạn lượt | Upstash Valkey của cloud-bot, key `go-scan:*` |
| LLM | Gemini Developer API (free tier, chung key với cloud-bot) |

Bí mật dùng lại của cloud-bot: `cloud-bot-gemini-api-key`, `cloud-bot-qdrant-api-key`, `cloud-bot-valkey-url`,
`cloud-bot-valkey-token` (service account `go-scan-run` chỉ được đọc 4 secret này).

## Cập nhật

```bash
deploy/gcp.sh          # build + deploy cả hai
deploy/gcp.sh app      # chỉ app
deploy/gcp.sh cleanup  # chỉ xoá revision / image cũ
```

## Đăng nhập (IAP + OAuth client riêng)

Project nằm trong một organization, mà OAuth client do Google quản lý chỉ cho người dùng *trong* organization
đăng nhập; tài khoản Gmail cá nhân bị từ chối. Vì vậy IAP dùng OAuth client riêng **Go Scan IAP**
(Google Auth Platform: External, Testing). Cấu hình nằm ở `~/iap-oauth.yaml` (chmod 600, không commit), áp dụng bằng:

```bash
deploy/iap-oauth.sh
```

Người dùng mới phải được thêm ở **cả hai** chỗ: *Test users* của OAuth consent screen và quyền IAP bên dưới.

## Admin

Admin không bị giới hạn token Gemini / lượt KataGo và số ván ghim. Ghi email vào `deploy/admins.txt` (mỗi dòng một
email, không commit) rồi chạy `deploy/gcp.sh app`. Danh tính lấy từ header của IAP (`TRUST_IAP_HEADER=1`, chỉ an toàn
khi dịch vụ nằm sau IAP).

## Thêm người được dùng

```bash
gcloud iap web add-iam-policy-binding --resource-type=cloud-run --service=go-scan --region=us-central1 \
  --member=user:EMAIL --role=roles/iap.httpsResourceAccessor
```

## Thêm / cập nhật sách

1. Đặt file vào `books/src/Go Books/…`, khai báo trong `rag/catalog.py` (sách scan: chạy `scripts/ocr-books.sh` trước).
2. `python -m rag.run_with_cloudbot_env rag.ingest_books --book <id>` (nhúng vector, đẩy lên Qdrant, xuất ảnh trang).
3. `gcloud storage cp -r books/pages/<id> gs://<project>-go-scan/books/pages/`

## Giới hạn để nằm trong free tier

- Cloud Run: `max-instances=1`, scale về 0 khi không dùng; free tier (180k vCPU-giây, 360k GiB-giây/tháng)
  tính **chung cả billing account** với cloud-bot.
- Chat: 300 câu/ngày cho cả app, 5 câu/phút mỗi người (`CHAT_DAILY_LIMIT`, `CHAT_PER_MINUTE`); mỗi câu tiếng
  Việt tốn 2 lượt gọi Gemini (dịch truy vấn + trả lời), dùng chung quota với cloud-bot. Admin bỏ qua các giới hạn này.
- Mỗi người dùng thường (khoảng 10 người): 200.000 token AI/ngày (~25 câu hỏi về thế cờ, mỗi câu ~8.000 token vì
  kèm 60 biến KataGo) và 150.000 lượt tìm KataGo/ngày (~3 lần review ván); kết quả lấy từ cache không bị tính.
- KataGo: giải thích một nước tốn 4 lượt tìm kiếm (thế cờ, lựa chọn kém thay cho nó, thế cờ sau nó và các cách đáp
  kém); hỏi lại hoặc đi đúng biến đã tính thì lấy từ cache. Cần deploy lại `go-katago`
  (`deploy/gcp.sh katago`) để có tham số `avoid` / `wide_root_noise`; bản cũ vẫn chạy nhưng ít biến kém hơn.
- Artifact Registry chỉ miễn phí 0,5 GB (tính chung billing account): `deploy/gcp.sh` tự xoá revision và image cũ sau
  mỗi lần deploy (chạy riêng: `deploy/gcp.sh cleanup`). Ba image đang chạy (app ~1 GB, KataGo ~0,17 GB, cloud-bot
  ~0,43 GB) vẫn vượt ~1,1 GB, khoảng 0,11 USD/tháng.
