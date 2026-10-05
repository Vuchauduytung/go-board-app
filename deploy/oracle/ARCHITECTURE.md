# Go Scan trên Oracle Cloud — kiến trúc

https://lhm-go.duckdns.org — VM Ampere A1 (2 OCPU / 12 GB, Ubuntu 22.04 aarch64) ở `ap-singapore-1`, chạy bằng
`deploy/oracle/compose.yaml` với profile `duckdns`. Cách dựng: `deploy/oracle/README.md`.

```mermaid
flowchart LR
    user(["Trình duyệt<br/>tài khoản Google"])
    google["Google OAuth<br/>client 'Go Scan Oracle'<br/>(Test users)"]
    le["Let's Encrypt"]
    duck["DuckDNS<br/>lhm-go.duckdns.org → 138.2.89.60"]

    subgraph oci["Oracle Cloud · ap-singapore-1 · VCN vnc-vcdtung"]
        sl{{"Security List + iptables<br/>TCP 22 · 80 · 443"}}
        subgraph vm["VM A1.Flex · 2 OCPU / 12 GB · Docker Compose 'go-scan'"]
            caddy["caddy<br/>:80 / :443, HTTPS"]
            oauth["oauth2-proxy :4180<br/>chỉ email trong emails.txt"]
            subgraph appc["app :8000 (FastAPI)"]
                api["Web tĩnh + /api/*<br/>recognize · analyze · chat<br/>sessions · sgf · feedback"]
                recog["Nhận dạng ảnh<br/>Moku RT-DETR ONNX + image2sgf"]
                emb["multilingual-e5-small<br/>embedding trên CPU"]
                llm["rag/llm.py<br/>chuỗi LLM dự phòng"]
            end
            valkey[("valkey<br/>lịch sử chat, hạn mức,<br/>cache KataGo")]
            data[("/srv/go-scan<br/>scans/ · sessions/")]
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
    end

    user -- "DNS" --> duck
    user -- "HTTPS" --> sl --> caddy
    caddy -- "/telegram/webhook" --> api
    caddy -- "mọi đường dẫn khác" --> oauth
    oauth -. "đăng nhập" .-> google
    oauth -- "X-Forwarded-Email" --> api
    caddy -. "chứng chỉ (HTTP-01)" .-> le

    api --> recog
    api --> data
    api --> pages
    api --> valkey
    api -- "gợi ý nước đi" --> katago
    api --> emb -- "tìm kiếm vector" --> qdrant
    api --> llm --> gemini
```

## Luồng chính

| Bước | Đi qua |
|---|---|
| Vào trang | DuckDNS → Security List/iptables (443) → Caddy (HTTPS) → oauth2-proxy (Google, `emails.txt`) → app |
| Chụp bàn cờ | `/api/recognize`: Moku RT-DETR + image2sgf → ma trận bàn cờ, ảnh lưu ở `/srv/go-scan/scans` |
| Gợi ý nước đi | `/api/analyze` → KataGo trên Modal (khởi động nguội ~20–30 s), kết quả cache trong Valkey |
| Hỏi trợ lý / sách | `/api/chat` → e5 embedding → Qdrant → chuỗi LLM (model đầu tiên trả lời được thì dùng) → trích dẫn ảnh trang sách |
| Ván / review | `/api/sessions`, `/api/sgf` → `/srv/go-scan/sessions` (theo email) |

## Vận hành

- SSH: `ssh -i ~/.ssh/oracle-vm.key ubuntu@138.2.89.60`; app không qua đăng nhập: `ssh -L 8765:localhost:8765 …`.
- Cập nhật: rsync code từ máy dev (không ghi đè `deploy/oracle/.env`, `emails.txt`), rồi
  `docker compose -f deploy/oracle/compose.yaml --env-file deploy/oracle/.env up -d --build`.
- Bí mật chỉ nằm trong `deploy/oracle/.env` trên VM: Google OAuth, cookie, Gemini, Qdrant, Groq, OpenRouter, Mistral,
  Modal token.
