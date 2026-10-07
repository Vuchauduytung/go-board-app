# Go Scan

Mobile web app: chụp ảnh bàn cờ vây → ma trận NxN (0 trống, 1 đen, 2 trắng; hàng 0 = cạnh trên ảnh) → sửa tay nếu cần → gửi lên server.

- Nhận dạng: [Moku](https://huggingface.co/kaya-go/moku-v4) RT-DETR (mặc định, 9/13/19, AGPL-3.0), [noword/image2sgf](https://github.com/noword/image2sgf) (19x19), [skolchin/gbr](https://github.com/skolchin/gbr) (OpenCV).
- Gợi ý nước đi: [KataGo](https://github.com/lightvector/KataGo) v1.18.2 (CUDA) + mạng `kata1-b18c384nbt`, chạy ở container `katago` (cần GPU NVIDIA), ~2.7 s/lần với 400 lượt tìm kiếm trên GTX 1650.
- Hỏi đáp: RAG trên 11 sách cờ vây (Qdrant + multilingual-E5 + Gemini), xem `rag/`.
- Bàn cờ luôn hiển thị: nhập ván từ nước đầu tiên trên bàn trống, hoặc chụp ảnh một thế cờ.
- Trợ lý về thế cờ (`coach.py`): khi giải thích một nước, KataGo tính 20 lựa chọn tốt nhất / 20 lựa chọn kém nhất
  thay cho nước đó và 10 biến tốt nhất / 10 biến kém nhất sau nước đó;
  kết quả KataGo được cache theo thế cờ, trong session (lâu dài, chỉ người đó) và trong Valkey (7 ngày, `kgcache.py`). Khi người dùng yêu cầu ("áp dụng chuỗi đó",
  "xoá quân D4", "quay lại nước 5"…), trợ lý trả kèm thao tác và trang web thực hiện lên bàn (có nút hoàn tác).
- Ván cờ (`sessions.py`): mỗi người dùng giữ 10 ván gần nhất + tối đa 20 ván ghim ⭐ (admin không giới hạn), gồm
  thế cờ, các nước đã đặt và cuộc trò chuyện, trong `SESSIONS_DIR` (mặc định `DATA_DIR/sessions`).
- Tài khoản (`accounts.py`): tài khoản Google qua oauth2-proxy (VM Oracle); `ADMIN_EMAILS` không bị giới hạn, người dùng thường có hạn mức
  mỗi ngày `USER_DAILY_GEMINI_TOKENS` (200.000 token) và `USER_DAILY_KATAGO` (300 lượt KataGo không lấy từ cache).
- Lưu trữ: mỗi lượt chụp nằm trong `DATA_DIR/<id>/` gồm `image.jpg`, `recognized.json`, `board.json` (sau khi gửi).
- Triển khai: một VM Oracle Cloud Always Free (ARM 2 OCPU / 12 GB, DuckDNS + Caddy + đăng nhập Google), kiến trúc ở
  [deploy/oracle/ARCHITECTURE.md](deploy/oracle/ARCHITECTURE.md), cách dựng ở [deploy/oracle/README.md](deploy/oracle/README.md),
  vận hành (bot Telegram, thêm sách) ở [deploy/README.md](deploy/README.md). Bản Cloud Run cũ đã gỡ (6/10/2026).
- Cây nước đi: mọi nhánh đã thử được giữ (lùi rồi đi khác là mở nhánh mới), nhánh đang xem sáng lên, chạm một nước
  trên cây để nhảy tới; nút ⏮ ◀◀ ◀ ▶ ▶▶ ⏭ (về gốc, ±1, ±10, tới cuối nhánh) như OGS. Lưu cùng ván (`tree`).
  Trên bàn chỉ đánh số 10 nước gần nhất (số thật trong ván, nước cuối có vòng cam). Công tắc "Khi bấm ▶":
  🌳 Theo nhánh (đi theo nhánh đang sáng) hoặc 🤖 Theo KataGo (đi nước tốt nhất; 10 nước tiếp theo hiện mờ, viền xanh,
  tính trước theo đợt qua `POST /api/line`). Chạm một ứng viên của 💡 Gợi ý hoặc mở một lỗi của review là tự sang 🤖.
- Hỏi bằng giọng nói (nhận giọng trong trình duyệt, dự phòng `/api/stt`), nghe trả lời bằng giọng tiếng Việt do server
  tạo (`tts.py`: Microsoft HoaiMy qua edge-tts, dự phòng Google; giọng của máy chỉ khi server lỗi).
- Đánh với AI (🤖 Đánh với AI): cấp 5k–5d. Mỗi lượt KataGo tìm ~40 ứng viên, AI rút một nước sao cho trung bình mất
  đúng số điểm của cấp đó (`bot.py`, bảng `LEVELS` = điểm mất trung bình mỗi nước, điểm mất tối đa một nước; chỉnh ở
  đó hoặc qua biến `BOT_LEVELS`; nước KataGo chỉ xem qua 1–2 lượt bị tính là mất thêm `UNSURE / √lượt`). Thanh ván
  đấu hiện điểm mất trung bình mỗi nước của AI và của người chơi để hiệu chỉnh; bỏ lượt, đi lại, xin thua. Hết ván
  (hai lần bỏ lượt hoặc "Đếm điểm") là **đếm đất** (`score.py`, `POST /api/score`): KataGo đánh dấu quân chết và đất,
  chạm một nhóm để đổi chết ↔ sống, tính theo luật Trung Quốc (quân sống + đất, Trắng cộng komi). Ván được lưu như mọi ván khác (`play` trong session, `POST /api/play`). Trong lúc
  đánh, mọi thế cờ được KataGo tìm với đúng thông số của review (lượt AI khi chọn nước, lượt người chơi chạy ngầm
  với `think: true`, không hiện ra), nên hết ván bấm **📋 Review ván này** (`POST /api/sessions/{id}/play/review`)
  là review gần như ngay, lấy từ cache của ván.
- Định thức (tab 📘, `joseki.py`): cây định thức góc trên-phải do chính KataGo dựng (bàn trống, komi 7.5, mỗi thế
  cờ 500 lượt tìm trên cả bàn; nước ở góc là nhánh, nước tốt nhất ở chỗ khác là tenuki; giữ nước tốt nhất ở góc và
  các nước kém nó ≤ 1,5 điểm, tối đa 4, xếp loại tốt nhất / tốt / chơi được; hết định thức khi tenuki hơn hẳn).
  Dựng một lần trên máy chủ: `docker compose … exec app python -m joseki build` (ghi `/data/joseki/tree.json`, bản đã
  dựng được kèm trong `josekidb/`). Xem từng nước, đi theo nhánh chính, hỏi trợ lý, mở trên bàn chính.
- Tài liệu định thức trên web (`rag/web_sources.py`, `python -m rag.ingest_web`): các bài phân tích định thức bằng
  KataGo, Wikipedia, DeepMind, diễn đàn OGS… được index cùng sách; trợ lý trích dẫn kèm link gốc.
- Góp ý (tab ⭐): AI tự trả lời công khai, admin trả lời tay trên web hoặc qua bot Telegram (`telegram_bot.py`).
- Review ván từ file SGF: KataGo xem mọi thế cờ của ván, liệt kê 10 lỗi mất nhiều điểm nhất của mỗi bên, mỗi lỗi kèm biến
  tốt nhất 10 nước (`review.py`; chạy theo từng đợt ~20 s do trang web gọi). Chạm số trên biến (hoặc ▶) để đi tiếp
  theo biến: cửa sổ 10 nước trượt theo, biến được KataGo tính thêm từng đợt 10 nước khi chỉ còn ≤ 15 nước đã tính
  phía trước (`POST /api/sessions/{id}/review/line`) và lưu cùng lỗi đó.
- AI: chuỗi model miễn phí có dự phòng (Gemini → Groq → OpenRouter → Mistral), xem
  [deploy/LLM_PROVIDERS.md](deploy/LLM_PROVIDERS.md).
- KataGo trên GPU Modal (L4, tắt khi không dùng, ~1.700 lượt/s so với ~21 lượt/s trên CPU):
  `modal deploy katago/modal_katago.py`, rồi đặt `KATAGO_URL`, `KATAGO_AUTH=modal`, `KATAGO_MODAL_TOKEN` cho app.

## Chạy

```bash
scripts/fetch-assets.sh              # mô hình nhận dạng + KataGo (không lưu trong git, ~470 MB)
docker volume create go-board-data   # lần đầu
docker compose up -d --build
```

Sách không lưu trong git (có bản quyền): giải nén vào `books/src/`, chạy `scripts/ocr-books.sh` cho sách scan rồi
`python -m rag.ingest_books` (xem [deploy/README.md](deploy/README.md)).

Hai dịch vụ: `app` (nhận dạng + web, cổng 8765) và `katago` (phân tích, chỉ trong mạng nội bộ compose).
KataGo được build từ `katago/` (binary AppImage + mạng neural + `analysis.cfg`). Trên GPU không có tensor core
(GTX 16xx) phải tắt `cudaUseFP16`/`cudaUseNHWC`, nếu không chậm ~7 lần.

Mở `http://<IP máy chủ>:8765` trên điện thoại (cùng Wi-Fi).

## API

| Method | Path | |
|---|---|---|
| POST | `/api/recognize` | form `file` (ảnh), `board_size` = 9/13/19, `method` = `auto`/`ai`/`gbr` → `{id, method, board_size, matrix}` |
| POST | `/api/boards` | JSON `{id, matrix, note?, to_play?}` → lưu ma trận đã xác nhận |
| POST | `/api/analyze` | JSON `{matrix, to_play: "B"/"W", komi?=7.5, rules?="chinese", top?=5}` (400 lượt, dùng chung cache với trợ lý) → `{winrate, score_lead, moves: [{move, row, col, winrate, score_lead, visits, pv}]}` (tỉ lệ thắng/điểm tính cho bên đang tới lượt) |
| GET | `/api/boards` | danh sách đã gửi |
| GET | `/api/boards/{id}` | ma trận đã lưu |
| GET | `/api/boards/{id}/image` | ảnh gốc |
| POST | `/api/chat` | JSON `{question, board?, session_id?}` → `{answer, sources: [{n, title, page, image}], marks, actions, me}`; `actions` = thao tác lên bàn khi người dùng yêu cầu |
| POST | `/api/sgf` | form `file` (SGF) → tạo ván để review (`game` = kỷ lục ván, không đổi khi thử biến) |
| POST | `/api/sessions/{id}/review/step` | làm thêm ~20 s review; gọi lại tới khi `status` = `done` → `mistakes: {B: [...], W: [...]}` |
| GET | `/api/me` | người dùng, quyền admin và lượt đã dùng hôm nay |
| GET / POST | `/api/sessions` | danh sách `{recent (10), pinned, pinned_limit}` / tạo ván `{base, scan_id?, played, to_play, start_turn, matrix}` |
| GET / PUT / DELETE | `/api/sessions/{id}` | mở / lưu `{played, tree, to_play, start_turn, matrix, title}` / xoá ván; `tree` = `{s, nodes: [{p, r, c, color, was?, s}]}` (cha, nước, con trên nhánh đang sáng) |
| POST | `/api/sessions/{id}/pin` | `{pinned}`; 409 khi đã đủ số ván ghim |
| POST | `/api/chat/reset` | xoá lịch sử hội thoại |
| GET | `/api/books/{id}/pages/{n}` | ảnh trang sách được trích dẫn |
| GET | `/api/joseki` | cây định thức của KataGo `{starts, nodes: {"Q16 R17 …": {winrate, score_lead, tenuki, settled, next: [{move, loss, rating}]}}}` |
| POST | `/api/stt` | form `file` (ghi âm ≤ 3 MB) → `{text}` (giọng nói tiếng Việt → chữ) |
| POST | `/api/tts` | JSON `{text}` (≤ 600 ký tự) → MP3 đọc tiếng Việt |
| GET / POST | `/api/feedback` | góp ý công khai (kèm `reply`) / gửi góp ý (form `rating`, `text`, `anonymous`, `images`) |
| POST / DELETE | `/api/feedback/{id}/reply` | admin: đăng `{text}` / gỡ câu trả lời; `POST …/reply/draft` → nháp do AI viết |
