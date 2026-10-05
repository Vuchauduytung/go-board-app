# Các nhà cung cấp AI dự phòng

App gọi AI theo chuỗi (`rag/llm.py`): model đầu tiên trả lời được thì dùng; model bị quá tải, hết lượt miễn phí hoặc
lỗi thì chuyển sang model sau; nhà cung cấp chưa có key thì bỏ qua. Mặc định:

| Việc | Chuỗi (`LLM_CHAIN_*`) |
|---|---|
| Giải thích thế cờ (`BOARD`) | gemini-3.5-flash → gemini-3.5-flash-lite → Groq gpt-oss-120b → OpenRouter gemma-4-31b → nemotron-3-ultra (free) → Mistral medium → ministral-14b |
| Hỏi sách (`BOOKS`) | gemini-3.5-flash-lite → gemini-3.5-flash → Groq gpt-oss-120b → OpenRouter gemma-4-31b → nemotron-3-ultra (free) → Mistral small → ministral-14b |
| Dịch câu hỏi để tìm sách (`TRANSLATE`) | gemini-3.5-flash-lite → Groq gpt-oss-20b → Mistral small → ministral-14b |

Thử ngày 2026-10-04 với câu hỏi cờ vây tiếng Việt: Groq gpt-oss-120b nhanh nhất (~1 s) nhưng giải thích chung chung hơn;
OpenRouter `qwen/qwen3.8-27b:free` giải thích đúng ý nhất nhưng đến 2026-10-06 đã hết bản miễn phí, thay bằng
`google/gemma-4-31b-it:free` (hay bị giới hạn chung, báo 429 ngay) rồi `nvidia/nemotron-3-ultra-550b-a55b:free` (tiếng
Việt tốt nhưng chậm, 15–30 s; bản `nemotron-3-super` trả lời lẫn tiếng nước ngoài). Mistral giới hạn lượt theo từng
model: khi `mistral-medium`/`mistral-small` báo 429 thì `ministral-14b-latest` thường vẫn trả lời được. **GitHub
Models đã ngừng hoạt động từ 2026-07-30** nên không còn trong chuỗi.

Đổi chuỗi bằng biến môi trường, ví dụ `LLM_CHAIN_BOARD=gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b`. Tên model
của các nhà cung cấp thay đổi theo thời gian: sau khi thêm key, chạy kiểm tra ở cuối trang.

Lưu ý: ở gói miễn phí, các nhà cung cấp thường được dùng nội dung câu hỏi để cải thiện model (Gemini free tier,
Mistral Experiment, nhiều model `:free` trên OpenRouter). Đừng hỏi thông tin nhạy cảm.

## Tạo key

| Nhà cung cấp | Cách lấy key | Biến môi trường | Secret trên GCP |
|---|---|---|---|
| **Groq** | https://console.groq.com → đăng nhập → **API Keys** → Create API Key | `GROQ_API_KEY` | `go-scan-groq-api-key` |
| **Mistral** | https://console.mistral.ai → **Admin → Billing / Plans** → kích hoạt gói **Experiment** (miễn phí, cần xác minh số điện thoại) → **API Keys** → Create new key. Key tạo trước khi kích hoạt gói có hạn mức 0 request/phút (lỗi 429). | `MISTRAL_API_KEY` | `go-scan-mistral-api-key` |
| **OpenRouter** | https://openrouter.ai → **Keys** → Create Key (50 lượt/ngày với model `:free`; nạp 10 USD một lần để có 1.000 lượt/ngày) | `OPENROUTER_API_KEY` | `go-scan-openrouter-api-key` |

Không bắt buộc có đủ cả ba; có cái nào thì chuỗi dùng cái đó.

## Dùng ở máy bạn (docker compose)

Thêm vào `.env` ở thư mục gốc repo (không commit):

```bash
GROQ_API_KEY=gsk_...
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=sk-or-...
```

rồi `docker compose up -d`. Trên Oracle: điền vào `deploy/oracle/.env`.

## Dùng trên Cloud Run

Mỗi key là một secret, chỉ service account của app được đọc; `deploy/gcp.sh` tự gắn secret nào đang có:

```bash
P=$(gcloud config get-value project)
read -rsp 'Groq key: ' KEY && printf %s "$KEY" | gcloud secrets create go-scan-groq-api-key --data-file=- && unset KEY
gcloud secrets add-iam-policy-binding go-scan-groq-api-key \
  --member="serviceAccount:go-scan-run@$P.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
# tương tự: go-scan-mistral-api-key, go-scan-openrouter-api-key
deploy/gcp.sh app
```

## Kiểm tra

```bash
python -m rag.run_with_cloudbot_env rag.probe_llm
```

Mỗi model trong các chuỗi được hỏi một câu ngắn bằng tiếng Việt, trả về JSON; dòng `FAIL` cho biết model nào sai
tên, hết lượt hoặc chưa có quyền. Xem nhà cung cấp nào đã trả lời một câu hỏi: trường `model` trong kết quả `/api/chat`.
