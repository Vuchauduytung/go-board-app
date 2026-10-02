# Go Scan

Mobile web app: chụp ảnh bàn cờ vây → ma trận NxN (0 trống, 1 đen, 2 trắng; hàng 0 = cạnh trên ảnh) → sửa tay nếu cần → gửi lên server.

- Nhận dạng: [Moku](https://huggingface.co/kaya-go/moku-v4) RT-DETR (mặc định, 9/13/19, AGPL-3.0), [noword/image2sgf](https://github.com/noword/image2sgf) (19x19), [skolchin/gbr](https://github.com/skolchin/gbr) (OpenCV).
- Gợi ý nước đi: [KataGo](https://github.com/lightvector/KataGo) v1.18.2 (CUDA) + mạng `kata1-b18c384nbt`, chạy ở container `katago` (cần GPU NVIDIA), ~2.7 s/lần với 400 lượt tìm kiếm trên GTX 1650.
- Hỏi đáp: RAG trên 11 sách cờ vây (Qdrant + multilingual-E5 + Gemini), xem `rag/`.
- Lưu trữ: mỗi lượt chụp nằm trong `DATA_DIR/<id>/` gồm `image.jpg`, `recognized.json`, `board.json` (sau khi gửi).
- Triển khai Google Cloud (Cloud Run + IAP): xem [deploy/README.md](deploy/README.md) và [infrastructure.puml](infrastructure.puml).

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
| POST | `/api/analyze` | JSON `{matrix, to_play: "B"/"W", komi?=7.5, rules?="chinese", max_visits?=400, top?=5}` → `{winrate, score_lead, moves: [{move, row, col, winrate, score_lead, visits, pv}]}` (tỉ lệ thắng/điểm tính cho bên đang tới lượt) |
| GET | `/api/boards` | danh sách đã gửi |
| GET | `/api/boards/{id}` | ma trận đã lưu |
| GET | `/api/boards/{id}/image` | ảnh gốc |
| POST | `/api/chat` | JSON `{question}` → `{answer, sources: [{n, title, page, image}], remaining_today}` |
| POST | `/api/chat/reset` | xoá lịch sử hội thoại |
| GET | `/api/books/{id}/pages/{n}` | ảnh trang sách được trích dẫn |
