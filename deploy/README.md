# Go Scan trên Google Cloud

| Thành phần | Tài nguyên |
|---|---|
| Web + nhận dạng + chat | Cloud Run `go-scan` (us-central1, 2 vCPU / 4 GiB, min 0 / max 1, IAP) |
| Gợi ý nước đi | Cloud Run `go-katago` (2 vCPU / 2 GiB, KataGo v1.16.4 Eigen AVX2 + mạng b15c192, 200 visits; chỉ `go-scan-run` được gọi) |
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
```

## Đăng nhập (IAP + OAuth client riêng)

Project nằm trong một organization, mà OAuth client do Google quản lý chỉ cho người dùng *trong* organization
đăng nhập; tài khoản Gmail cá nhân bị từ chối. Vì vậy IAP dùng OAuth client riêng **Go Scan IAP**
(Google Auth Platform: External, Testing). Cấu hình nằm ở `~/iap-oauth.yaml` (chmod 600, không commit), áp dụng bằng:

```bash
deploy/iap-oauth.sh
```

Người dùng mới phải được thêm ở **cả hai** chỗ: *Test users* của OAuth consent screen và quyền IAP bên dưới.

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
  Việt tốn 2 lượt gọi Gemini (dịch truy vấn + trả lời), dùng chung quota với cloud-bot.
- Artifact Registry chỉ miễn phí 0,5 GB: image app ~2,4 GB → xoá tag cũ sau mỗi lần deploy.
