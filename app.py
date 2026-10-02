# Go board scanner server
#   POST /api/recognize   image -> board matrix
#                         auto: Moku RT-DETR stones (kaya-go/moku-v4) + board corners chosen among
#                               Moku and image2sgf candidates by how well stones fit the grid
#                         ai:   image2sgf per-cell classifier (19x19 only)
#                         gbr:  classic OpenCV (skolchin/gbr)
#   POST /api/boards      save confirmed matrix
#   GET  /api/boards      list saved boards
#   POST /api/analyze     KataGo move suggestions for a matrix (katago service, see katago/)
#   POST /api/chat        ask the Go books assistant (RAG over books/, see rag/)
#   GET  /api/books/{id}/pages/{n}  page image cited by the assistant
#   GET  /                mobile web app
#
# Matrix: rows top-to-bottom as on the photo, 0 = empty, 1 = black, 2 = white

import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from img2sgf import get_board_model, get_stone_model, get_board_image, classifier_board
from img2sgf.tools import get_board_position
from moku import MokuDetector
from gr.board import GrBoard
from gr.grdef import GR_A, GR_B, GR_BOARD_SIZE

ROOT = Path(__file__).parent
MODELS = Path(os.environ.get('MODELS_DIR', ROOT / 'models'))
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
KATAGO_URL = os.environ.get('KATAGO_URL', 'http://katago:8080')
BOOK_PAGES = Path(os.environ.get('BOOK_PAGES_DIR', ROOT / 'books' / 'pages'))
MAX_SIDE = 1600

logging.basicConfig(level=logging.ERROR)  # gr logs a lot of warnings
log = logging.getLogger('app')

torch.set_grad_enabled(False)
board_model = get_board_model(str(MODELS / 'board.pth')).eval()
stone_model = get_stone_model(str(MODELS / 'stone.pth')).eval()
moku = MokuDetector(str(MODELS / 'moku-v4.onnx'))

# Load the chat embedding model during startup instead of on the first question (~20 s on Cloud Run)
try:
    from rag.core import _model as _embedding_model
    _embedding_model()
except Exception as ex:
    log.warning('embedding model not loaded at startup: %s', ex)
model_lock = threading.Lock()


def recognize_moku(pil_img, board_size):
    """Moku stone detection; image2sgf corner boxes are added as extra corner candidates."""
    extra = None
    try:
        with model_lock:
            boxes, _ = get_board_position(board_model, pil_img)
        extra = [((x0 + x1) / 2, (y0 + y1) / 2) for x0, y0, x1, y1 in boxes]
    except Exception as ex:
        log.info('image2sgf corners unavailable: %s', ex)
    matrix, _ = moku.detect(np.array(pil_img), board_size, extra_corners=extra)
    return matrix


def recognize_i2s(pil_img):
    """image2sgf: detects board corners, then classifies every point. 19x19 only."""
    with model_lock:
        img, _, scores = get_board_image(board_model, pil_img)
        if min(scores) < 0.7:
            img0, _, scores0 = get_board_image(board_model, img)
            if sum(scores0) > sum(scores):
                img = img0
        res = classifier_board(stone_model, img)  # res[r][c], r = 0 is the bottom row
    return (res.flip(0) >> 1).numpy().astype(int)


def recognize_gbr(pil_img):
    """gbr: classic OpenCV algorithm, any board size, works best on flat screenshots."""
    board = GrBoard()
    board.image = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    board.process()
    res = board.results
    if res is None or not res.get(GR_BOARD_SIZE):
        raise ValueError('Board not detected')
    size = int(res[GR_BOARD_SIZE])
    m = np.zeros((size, size), dtype=int)
    for stones, color in ((board.black_stones, 1), (board.white_stones, 2)):
        for st in stones:
            r, c = size - int(st[GR_B]), int(st[GR_A]) - 1
            if 0 <= r < size and 0 <= c < size:
                m[r, c] = color
    return m


app = FastAPI(title='Go board scanner')


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.post('/api/recognize')
def recognize(file: UploadFile = File(...), method: str = Form('auto'), board_size: int = Form(19)):
    data = file.file.read()
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert('RGB')
    except Exception:
        raise HTTPException(400, 'Không đọc được ảnh')
    img.thumbnail((MAX_SIDE, MAX_SIDE))

    t = time.time()
    matrix, used, errors = None, None, []
    if board_size not in (9, 13, 19):
        raise HTTPException(400, 'Kích thước bàn phải là 9, 13 hoặc 19')
    fallback = ['moku', 'ai', 'gbr'] if board_size == 19 else ['moku', 'gbr']
    order = {'auto': fallback, 'ai': ['ai'], 'gbr': ['gbr']}.get(method, fallback)
    run = {'moku': lambda: recognize_moku(img, board_size), 'ai': lambda: recognize_i2s(img),
           'gbr': lambda: recognize_gbr(img)}
    for m in order:
        try:
            matrix = run[m]()
            used = m
            break
        except Exception as ex:
            log.warning('%s failed: %s', m, ex)
            errors.append(f'{m}: {ex}')
    if matrix is None:
        raise HTTPException(422, 'Không nhận dạng được bàn cờ. ' + '; '.join(errors))

    scan_id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    d = DATA / scan_id
    d.mkdir(parents=True)
    img.save(d / 'image.jpg', quality=90)
    result = {
        'id': scan_id,
        'method': used,
        'board_size': int(matrix.shape[0]),
        'matrix': matrix.tolist(),
        'elapsed': round(time.time() - t, 2),
    }
    (d / 'recognized.json').write_text(json.dumps(result))
    return result


class BoardIn(BaseModel):
    id: str
    matrix: List[List[int]]
    note: Optional[str] = None
    to_play: Optional[str] = None


class AnalyzeIn(BaseModel):
    matrix: List[List[int]]
    to_play: str = 'B'
    komi: float = 7.5
    rules: str = 'chinese'
    max_visits: int = 400
    top: int = 5


@app.post('/api/boards')
def save_board(b: BoardIn):
    d = DATA / b.id
    if '/' in b.id or '..' in b.id or not d.is_dir():
        raise HTTPException(404, 'Không tìm thấy lượt chụp')
    n = len(b.matrix)
    if n not in (9, 13, 19) or any(len(r) != n or any(v not in (0, 1, 2) for v in r) for r in b.matrix):
        raise HTTPException(400, 'Ma trận không hợp lệ')
    rec = json.loads((d / 'recognized.json').read_text())
    fixed = sum(a != c for r1, r2 in zip(rec['matrix'], b.matrix) for a, c in zip(r1, r2)) \
        if len(rec['matrix']) == n else None
    out = {'id': b.id, 'board_size': n, 'matrix': b.matrix, 'note': b.note, 'to_play': b.to_play,
           'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'cells_fixed': fixed}
    (d / 'board.json').write_text(json.dumps(out))
    return out


_katago_token = {'value': None, 'exp': 0.0}


def _katago_headers():
    """On Cloud Run the KataGo service only accepts callers with an ID token for its URL
    (KATAGO_AUTH=gcp): get one for this service's identity from the metadata server."""
    h = {'Content-Type': 'application/json'}
    if os.environ.get('KATAGO_AUTH') != 'gcp':
        return h
    if time.time() > _katago_token['exp']:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity'
            f'?audience={KATAGO_URL}', headers={'Metadata-Flavor': 'Google'})
        with urllib.request.urlopen(req, timeout=5) as r:
            _katago_token['value'] = r.read().decode()
        _katago_token['exp'] = time.time() + 45 * 60   # tokens live 1 hour
    h['Authorization'] = 'Bearer ' + _katago_token['value']
    return h


@app.post('/api/analyze')
def analyze(a: AnalyzeIn):
    n = len(a.matrix)
    if n not in (9, 13, 19) or any(len(r) != n or any(v not in (0, 1, 2) for v in r) for r in a.matrix):
        raise HTTPException(400, 'Ma trận không hợp lệ')
    if a.to_play not in ('B', 'W'):
        raise HTTPException(400, 'to_play phải là B hoặc W')
    req = urllib.request.Request(KATAGO_URL + '/analyze', json.dumps(a.dict()).encode(), _katago_headers())
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        detail = json.loads(ex.read() or b'{}').get('error', str(ex))
        raise HTTPException(422 if ex.code == 400 else 502, f'KataGo: {detail}')
    except (urllib.error.URLError, TimeoutError) as ex:
        raise HTTPException(503, f'KataGo chưa sẵn sàng: {ex}')


@app.get('/api/boards')
def list_boards(limit: int = 20):
    items = []
    for f in sorted(DATA.glob('*/board.json'), reverse=True)[:limit]:
        b = json.loads(f.read_text())
        m = np.array(b['matrix'])
        items.append({k: b.get(k) for k in ('id', 'board_size', 'saved_at', 'note', 'cells_fixed', 'to_play')}
                     | {'black': int((m == 1).sum()), 'white': int((m == 2).sum())})
    return items


@app.get('/api/boards/{scan_id}')
def get_board(scan_id: str):
    f = DATA / scan_id / 'board.json'
    if '/' in scan_id or '..' in scan_id or not f.is_file():
        raise HTTPException(404, 'Không tìm thấy')
    return json.loads(f.read_text())


@app.get('/api/boards/{scan_id}/image')
def get_image(scan_id: str):
    f = DATA / scan_id / 'image.jpg'
    if '/' in scan_id or '..' in scan_id or not f.is_file():
        raise HTTPException(404, 'Không tìm thấy')
    return FileResponse(f)


class ChatIn(BaseModel):
    question: str


def _chat_user(iap_email, client_id):
    """IAP puts the signed-in Google account in a header; fall back to the browser's random id."""
    if iap_email:
        return iap_email.split(':')[-1]
    if client_id and client_id.isalnum() and len(client_id) <= 64:
        return client_id
    return 'anonymous'


@app.post('/api/chat')
def chat(c: ChatIn, x_goog_authenticated_user_email: Optional[str] = Header(None),
         x_client_id: Optional[str] = Header(None)):
    from rag.chat import QuotaExceeded, answer
    q = c.question.strip()
    if not q or len(q) > 1000:
        raise HTTPException(400, 'Câu hỏi trống hoặc dài quá 1000 ký tự')
    try:
        return answer(_chat_user(x_goog_authenticated_user_email, x_client_id), q)
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex))
    except Exception as ex:
        log.exception('chat failed')
        raise HTTPException(502, f'Trợ lý tạm thời lỗi: {type(ex).__name__}')


@app.post('/api/chat/reset')
def chat_reset(x_goog_authenticated_user_email: Optional[str] = Header(None), x_client_id: Optional[str] = Header(None)):
    from rag.chat import clear_history
    clear_history(_chat_user(x_goog_authenticated_user_email, x_client_id))
    return {'ok': True}


@app.get('/api/books/{book_id}/pages/{page}')
def book_page(book_id: str, page: int):
    from rag.catalog import BY_ID
    f = BOOK_PAGES / book_id / f'p{page}.jpg'
    if book_id not in BY_ID or page < 1 or not f.is_file():
        raise HTTPException(404, 'Không tìm thấy trang sách')
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=86400'})


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')
