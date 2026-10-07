# Go Scan trên Oracle Cloud — kiến trúc

https://lhm-go.duckdns.org — VM Ampere A1 (2 OCPU / 12 GB, Ubuntu 22.04 aarch64) ở `ap-singapore-1`, chạy bằng
`deploy/oracle/compose.yaml` với profile `duckdns`. Cách dựng: `deploy/oracle/README.md`. Đây là bản triển khai duy
nhất: bản Cloud Run cũ trên Google Cloud đã gỡ ngày 6/10/2026 (dữ liệu chuyển sang VM, xem `deploy/README.md`).

```mermaid
flowchart LR
    user(["Trình duyệt<br/>tài khoản Google<br/>(mic + giọng đọc tiếng Việt)"])
    admin(["Admin<br/>Telegram"])
    tg["Telegram Bot API"]
    google["Google OAuth<br/>client 'Go Scan Oracle'<br/>(Test users)"]
    le["Let's Encrypt"]
    duck["DuckDNS<br/>lhm-go.duckdns.org → 138.2.89.60"]

    subgraph oci["Oracle Cloud · ap-singapore-1 · VCN vnc-vcdtung"]
        sl{{"Security List + iptables<br/>TCP 22 · 80 · 443"}}
        subgraph vm["VM A1.Flex · 2 OCPU / 12 GB · Docker Compose 'go-scan'"]
            caddy["caddy<br/>:80 / :443, HTTPS"]
            oauth["oauth2-proxy :4180<br/>chỉ email trong emails.txt"]
            subgraph appc["app :8000 (FastAPI)"]
                api["Web tĩnh + /api/*<br/>recognize · analyze · chat · stt<br/>sessions (cây nước đi) · sgf · feedback"]
                bot["telegram_bot.py<br/>/telegram/webhook<br/>quản lý + trả lời góp ý"]
                recog["Nhận dạng ảnh<br/>Moku RT-DETR ONNX + image2sgf"]
                emb["multilingual-e5-small<br/>embedding trên CPU"]
                llm["rag/llm.py<br/>chuỗi LLM dự phòng"]
            end
            valkey[("valkey<br/>lịch sử chat, hạn mức,<br/>cache KataGo")]
            kcpu["katago (CPU, b15)<br/>~18 lượt/s<br/>đánh với AI + dự phòng"]
            data[("/srv/go-scan<br/>scans/ · sessions/ · feedback/")]
            pages[("books/pages<br/>ảnh trang sách, read-only")]
        end
    end

    subgraph ext["Dịch vụ ngoài"]
        katago["Modal · KataGo<br/>GPU L4, scale về 0<br/>Modal-Key/Secret"]
        qdrant[("Qdrant Cloud<br/>go_books_e5")]
        subgraph chain["Chuỗi LLM: thử lần lượt"]
            direction TB
            gemini["1 · Gemini 3.5 flash / flash-lite"]
            groq["2 · Groq gpt-oss-120b / 20b"]
            orouter["3 · OpenRouter gemma-4-31b → nemotron-3-ultra (free)"]
            mistral["4 · Mistral medium / small → ministral-14b"]
            gemini --> groq --> orouter --> mistral
        end
        whisper["Giọng nói → chữ (dự phòng)<br/>Groq Whisper → Gemini"]
    end

    user -- "DNS" --> duck
    user -- "HTTPS" --> sl --> caddy
    caddy -- "/telegram/webhook<br/>(secret token)" --> bot
    caddy -- "mọi đường dẫn khác" --> oauth
    oauth -. "đăng nhập" .-> google
    oauth -- "X-Forwarded-Email" --> api
    caddy -. "chứng chỉ (HTTP-01)" .-> le

    api --> recog
    api --> data
    api --> pages
    api --> valkey
    api -- "gợi ý, review, trợ lý" --> katago
    api -- "đánh với AI; khi Modal lỗi" --> kcpu
    api --> emb -- "tìm kiếm vector" --> qdrant
    api --> llm --> gemini
    api -- "/api/stt" --> whisper
    admin <--> tg
    tg -- "webhook" --> caddy
    bot -- "báo góp ý mới, trả lời lệnh" --> tg
    bot --> data
    bot --> llm
```

## Luồng chính

| Bước | Đi qua |
|---|---|
| Vào trang | DuckDNS → Security List/iptables (443) → Caddy (HTTPS) → oauth2-proxy (Google, `emails.txt`) → app |
| Chụp bàn cờ | `/api/recognize`: Moku RT-DETR + image2sgf → ma trận bàn cờ, ảnh lưu ở `/srv/go-scan/scans` |
| Gợi ý nước đi | `/api/analyze` → KataGo trên Modal (khởi động nguội ~20–30 s), kết quả cache trong Valkey; Modal lỗi hoặc hết credit → KataGo CPU trên VM (200 lượt), thử lại Modal sau 5 phút |
| Đánh với AI | `/api/play` → luôn KataGo CPU trên VM (100 lượt tìm, ~5,5 s một nước), không tốn credit Modal |
| Hỏi trợ lý / sách | `/api/chat` → e5 embedding → Qdrant → chuỗi LLM (model đầu tiên trả lời được thì dùng) → trích dẫn ảnh trang sách |
| Ván / review | `/api/sessions`, `/api/sgf` → `/srv/go-scan/sessions` (theo email); mỗi ván lưu cả **cây nước đi** (mọi nhánh đã thử, nhánh đang sáng) |
| Hỏi bằng giọng nói | Trình duyệt tự nhận giọng (Chrome, Safari, `vi-VN`); không có thì ghi âm → `/api/stt` → Groq Whisper (dự phòng Gemini). Câu trả lời đọc bằng giọng tiếng Việt của máy (`speechSynthesis`) |
| Góp ý | `/api/feedback` → `/srv/go-scan/feedback`; luồng nền: AI viết câu trả lời công khai (chuỗi `feedback`, câu mẫu khi AI lỗi) → bot báo admin qua Telegram |
| Bot admin | Telegram → Caddy (`/telegram/webhook`, không qua đăng nhập) → app kiểm tra secret + chat id admin → lệnh `/reviews`, `/summary`, `/reply`, `/draft`, `/autoreply`… |
| Báo cáo tài nguyên | app đếm giờ GPU Modal, lượt AI, giọng nói (`usage.py` → `/srv/go-scan/usage`) → 8 giờ sáng bot gửi admin số liệu, chi phí dự kiến tháng, cảnh báo vượt ngân sách (0) |

## Vận hành

- SSH: `ssh -i ~/.ssh/oracle-vm.key ubuntu@138.2.89.60`; app không qua đăng nhập: `ssh -L 8765:localhost:8765 …`.
- Cập nhật: rsync code từ máy dev (không ghi đè `deploy/oracle/.env`, `emails.txt`), rồi
  `docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env up -d --build`.
- Bí mật chỉ nằm trong `deploy/oracle/.env` trên VM: Google OAuth, cookie, Gemini, Qdrant, Groq, OpenRouter, Mistral,
  Modal token, Telegram (token, webhook secret, chat id admin). Bản sao dự phòng của Modal / Telegram token còn trong
  Secret Manager của project Google Cloud (project đó vẫn giữ OAuth client "Go Scan Oracle").
- oauth2-proxy chờ app tối đa 240 s (`OAUTH2_PROXY_UPSTREAM_TIMEOUT`): câu hỏi về thế cờ khi GPU Modal khởi động nguội
  lâu hơn mặc định 30 s, trước đây bị cắt thành lỗi 502.
- Đổi webhook bot (khi đổi tên miền): `docker compose … exec app python -m telegram_bot set-webhook https://<DOMAIN>/telegram/webhook`.
- Dữ liệu người dùng chỉ có ở `/srv/go-scan` trên VM (file do container ghi, chủ sở hữu root): nên bật backup policy
  cho boot volume.

## Đã gỡ

| Ở đâu | Đã xoá |
|---|---|
| Google Cloud `project-1a0ed8d8-…` | Cloud Run `go-scan`, `go-scan-bot`; Artifact Registry `go-scan`; bucket `…-go-scan`; service account `go-scan-run`, `go-katago-run` |
| Repo | `deploy/gcp.sh`, `deploy/iap-oauth.sh`, `infrastructure.puml` |
