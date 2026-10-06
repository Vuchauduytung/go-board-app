# Go board scanner server
#   POST /api/recognize   image -> board matrix
#                         auto: Moku RT-DETR stones (kaya-go/moku-v4) + board corners chosen among
#                               Moku and image2sgf candidates by how well stones fit the grid
#                         ai:   image2sgf per-cell classifier (19x19 only)
#                         gbr:  classic OpenCV (skolchin/gbr)
#   POST /api/boards      save confirmed matrix
#   GET  /api/boards      list saved boards
#   POST /api/analyze     KataGo move suggestions for a matrix (katago service, see katago/)
#   POST /api/chat        ask the Go books assistant (RAG over books/, see rag/); with a board: the coach
#   GET  /api/me          signed-in user, role and today's usage
#   /api/sessions         the user's review sessions: list, create, load, save, pin, delete
#   /api/feedback         reviews of the app: public list, post (optionally anonymous), delete one's own;
#                         admins reply publicly (new reviews get an LLM reply first, see feedback.py)
#   POST /api/stt         voice question -> text, for browsers without speech recognition
#   POST /telegram/webhook  admin bot (telegram_bot.py), when the proxy lets Telegram reach it (Oracle VM)
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
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

import accounts
import coach
import kgcache
import review
import feedback
import sessions
import telegram_bot
from accounts import QuotaExceeded
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
app.include_router(telegram_bot.router)


def current_user(x_goog_authenticated_user_email: Optional[str] = Header(None),
                 cf_access_authenticated_user_email: Optional[str] = Header(None),
                 x_forwarded_email: Optional[str] = Header(None),
                 x_client_id: Optional[str] = Header(None)):
    return accounts.identity(x_goog_authenticated_user_email, x_client_id, cf_access_authenticated_user_email,
                             x_forwarded_email)


def _scan_dir(user, scan_id):
    """A photo's directory, only for the user who took it (older photos without an owner: for whoever has a
    session on it)."""
    d = DATA / scan_id
    if '/' in scan_id or '..' in scan_id or not (d / 'recognized.json').is_file():
        raise HTTPException(404, 'Không tìm thấy lượt chụp')
    owner = json.loads((d / 'recognized.json').read_text()).get('owner')
    if owner == user or owner is None and sessions.has_scan(user, scan_id):
        return d
    raise HTTPException(404, 'Không tìm thấy lượt chụp')


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.post('/api/recognize')
def recognize(file: UploadFile = File(...), method: str = Form('auto'), board_size: int = Form(19),
              user: str = Depends(current_user)):
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
    (d / 'recognized.json').write_text(json.dumps({**result, 'owner': user}))
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
    session_id: Optional[str] = None   # the KataGo answer is also indexed in this session


@app.post('/api/boards')
def save_board(b: BoardIn, user: str = Depends(current_user)):
    d = _scan_dir(user, b.id)
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
    (KATAGO_AUTH=gcp): get one for this service's identity from the metadata server.
    KataGo on Modal (KATAGO_AUTH=modal) takes a proxy token: KATAGO_MODAL_TOKEN=wk-...:ws-..."""
    h = {'Content-Type': 'application/json'}
    if os.environ.get('KATAGO_AUTH') == 'modal':
        key, _, secret = os.environ['KATAGO_MODAL_TOKEN'].replace(':', '.', 1).partition('.')
        return h | {'Modal-Key': key, 'Modal-Secret': secret}
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


class KataGoError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status


def _katago(payload):
    """POST a query to the KataGo service; raises KataGoError with an HTTP status for the client."""
    req = urllib.request.Request(KATAGO_URL + '/analyze', json.dumps(payload).encode(), _katago_headers())
    try:
        # a Modal GPU that scaled to zero may need a minute or more to start
        with urllib.request.urlopen(req, timeout=150 if os.environ.get('KATAGO_AUTH') == 'modal' else 90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        detail = json.loads(ex.read() or b'{}').get('error', str(ex))
        raise KataGoError(422 if ex.code == 400 else 502, f'KataGo: {detail}')
    except (urllib.error.URLError, TimeoutError) as ex:
        raise KataGoError(503, f'KataGo chưa sẵn sàng: {ex}')


def _check_board(matrix, to_play):
    n = len(matrix)
    if n not in (9, 13, 19) or any(len(r) != n or any(v not in (0, 1, 2) for v in r) for r in matrix):
        raise HTTPException(400, 'Ma trận không hợp lệ')
    if to_play not in ('B', 'W'):
        raise HTTPException(400, 'to_play phải là B hoặc W')


def _katago_for(user, sid=None):
    """KataGo for a user's request: answered from the session's cache or the shared one, otherwise searched off
    their daily budget (in visits). With a session id, every answer is indexed in that session."""
    def search(payload):
        accounts.ensure(user, 'katago')
        accounts.add(user, 'katago', int(payload.get('max_visits', 400)))
        return _katago(payload)
    return lambda payload: kgcache.cached(search, payload, (user, sid) if sid else None)


def _own_session(user, sid):
    s = sessions.get(user, sid) if sid else None
    if sid and s is None:
        raise HTTPException(404, 'Không tìm thấy ván này')
    return s


_warmed = {'at': 0.0}


@app.post('/api/katago/warmup')
def katago_warmup():
    """Start a scaled-to-zero KataGo GPU (Modal) while the user sets up the board, so the first question
    does not wait for the cold start. At most once a minute; the answer is not waited for."""
    if os.environ.get('KATAGO_AUTH') != 'modal' or time.time() - _warmed['at'] < 60:
        return {'started': False}
    _warmed['at'] = time.time()

    def ping():
        req = urllib.request.Request(KATAGO_URL + '/health', headers=_katago_headers())
        try:
            urllib.request.urlopen(req, timeout=150).read()
        except Exception as ex:
            log.warning('KataGo warm-up failed: %s', ex)
    threading.Thread(target=ping, daemon=True).start()
    return {'started': True}


@app.post('/api/analyze')
def analyze(a: AnalyzeIn, user: str = Depends(current_user)):
    _check_board(a.matrix, a.to_play)
    _own_session(user, a.session_id)
    payload = {k: v for k, v in a.dict().items() if k != 'session_id'}
    try:
        # searched like the coach's main analysis, so both share the cached result
        res = _katago_for(user, a.session_id)({**payload, **coach.ROOT})
    except KataGoError as ex:
        raise HTTPException(ex.status, str(ex))
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex))
    return {**res, 'moves': res['moves'][:a.top], 'ownership': None}


@app.get('/api/boards')
def list_boards(limit: int = 20, user: str = Depends(current_user)):
    """The user's own saved photos."""
    items = []
    for f in sorted(DATA.glob('*/board.json'), reverse=True):
        if len(items) >= limit:
            break
        rec = f.parent / 'recognized.json'
        if not rec.is_file() or json.loads(rec.read_text()).get('owner') != user:
            continue
        b = json.loads(f.read_text())
        m = np.array(b['matrix'])
        items.append({k: b.get(k) for k in ('id', 'board_size', 'saved_at', 'note', 'cells_fixed', 'to_play')}
                     | {'black': int((m == 1).sum()), 'white': int((m == 2).sum())})
    return items


@app.get('/api/boards/{scan_id}')
def get_board(scan_id: str, user: str = Depends(current_user)):
    f = _scan_dir(user, scan_id) / 'board.json'
    if not f.is_file():
        raise HTTPException(404, 'Không tìm thấy')
    return json.loads(f.read_text())


@app.get('/api/boards/{scan_id}/image')
def get_image(scan_id: str, user: str = Depends(current_user)):
    f = _scan_dir(user, scan_id) / 'image.jpg'
    if not f.is_file():
        raise HTTPException(404, 'Không tìm thấy')
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=86400'})


class BoardIn2(BaseModel):
    matrix: List[List[int]]                      # position on screen
    to_play: str = 'B'
    base: Optional[List[List[int]]] = None       # photographed position, before the moves tried on it
    base_to_play: Optional[str] = None
    moves: List[List[str]] = []                  # [["B", "Q16"], ...] tried since `base`
    komi: float = 7.5


class Placed(BaseModel):
    r: int
    c: int
    color: int                 # 1 black, 2 white, 0 = stone removed
    was: Optional[int] = None  # colour of a removed stone


class ChatIn(BaseModel):
    question: str
    board: Optional[BoardIn2] = None   # set when asking about the position on screen
    session_id: Optional[str] = None   # review session the board conversation belongs to
    played: List[Placed] = []          # the board's stones placed / removed: where the answer's variations start


@app.post('/api/chat')
def chat(c: ChatIn, user: str = Depends(current_user)):
    from rag.chat import HISTORY_TURNS, answer
    q = c.question.strip()
    if not q or len(q) > 1000:
        raise HTTPException(400, 'Câu hỏi trống hoặc dài quá 1000 ký tự')
    session = _own_session(user, c.session_id)
    board_context, marks = None, []
    if c.board:
        b = c.board
        _check_board(b.matrix, b.to_play)
        if b.base is not None:
            _check_board(b.base, b.base_to_play or 'B')
        played = [p.dict(exclude_none=True) for p in c.played]
        _check_session(len(b.matrix), {'played': played})
        try:
            board_context, marks = coach.build_context(b.matrix, b.to_play, q, _katago_for(user, c.session_id),
                                                       base=b.base, base_to_play=b.base_to_play,
                                                       played=b.moves[:600], komi=b.komi)
        except QuotaExceeded as ex:
            raise HTTPException(429, str(ex))
        except KataGoError as ex:
            log.warning('board context without KataGo: %s', ex)
            board_context = ('THẾ CỜ HIỆN TẠI:\n' + coach.ascii_board(c.board.matrix) +
                             f'\nTới lượt: {coach.NAMES[c.board.to_play]}. (KataGo tạm thời không phân tích được, '
                             'hãy nói rõ là nhận xét chưa được máy kiểm chứng.)')
    history = sessions.history(session, HISTORY_TURNS) if session and c.board else None
    first_var = sessions.next_variation(session)
    if board_context:
        board_context += (f'\n\nCác biến đã nêu trước đây giữ tên cũ (Var1 … Var{first_var - 1}); biến mới trong câu '
                          f'trả lời này đặt tên từ Var{first_var}.' if first_var > 1 else '')
    try:
        res = answer(user, q, board_context, history)
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex))
    except Exception as ex:
        log.exception('chat failed')
        raise HTTPException(502, f'Trợ lý tạm thời lỗi: {type(ex).__name__}')
    actions, variations = [], []
    if c.board:
        n = len(c.board.matrix)
        actions = coach.clean_actions(res['actions'], n)
        variations, res['answer'] = coach.clean_variations(res['variations'], n, played, first_var, res['answer'])
    if session and c.board:
        sessions.append_chat(user, session['id'], {'q': q, 'a': res['answer'], 'sources': res['sources'],
                                                   'marks': marks, 'actions': actions}, variations)
    return {**res, 'actions': actions, 'variations': variations, 'marks': marks, 'me': accounts.usage(user)}


@app.post('/api/chat/reset')
def chat_reset(board: bool = False, user: str = Depends(current_user)):
    from rag.chat import clear_history
    clear_history(f'{user}:board' if board else user)
    return {'ok': True}


@app.get('/api/feedback')
def list_feedback(user: str = Depends(current_user)):
    return feedback.list_public(user)


@app.post('/api/feedback')
def post_feedback(rating: Optional[int] = Form(None), text: str = Form(''), anonymous: bool = Form(False),
                  images: List[UploadFile] = File([]), user: str = Depends(current_user)):
    """A review: 1–5 stars and/or a comment, up to 3 photos (multipart form)."""
    text = text.strip()
    if rating is not None and not 1 <= rating <= 5:
        raise HTTPException(400, 'Số sao từ 1 đến 5')
    if not text and rating is None and not images:
        raise HTTPException(400, 'Hãy chấm sao, viết vài dòng hoặc gửi ảnh góp ý')
    if len(text) > feedback.TEXT_MAX:
        raise HTTPException(400, f'Góp ý dài tối đa {feedback.TEXT_MAX} ký tự')
    if len(images) > feedback.IMAGES_MAX:
        raise HTTPException(400, f'Tối đa {feedback.IMAGES_MAX} ảnh mỗi góp ý')
    try:
        jpegs = [feedback.encode_image(f.file.read(feedback.IMAGE_BYTES_MAX + 1)) for f in images]
        r = feedback.add(user, rating, text, anonymous, jpegs)
    except feedback.BadImage as ex:
        raise HTTPException(400, str(ex))
    except feedback.TooMany as ex:
        raise HTTPException(429, str(ex))
    telegram_bot.on_new_review(r)   # auto reply + admins' Telegram, in the background
    return feedback.public(r, user)


class ReplyIn(BaseModel):
    text: str


def _admin_review(rid, user):
    if not accounts.is_admin(user):
        raise HTTPException(403, 'Chỉ admin mới trả lời góp ý')
    r = feedback.get(rid)
    if not r:
        raise HTTPException(404, 'Không tìm thấy góp ý')
    return r


@app.post('/api/feedback/{rid}/reply')
def reply_feedback(rid: str, body: ReplyIn, user: str = Depends(current_user)):
    _admin_review(rid, user)
    if len(body.text.strip()) > feedback.REPLY_MAX:
        raise HTTPException(400, f'Câu trả lời dài tối đa {feedback.REPLY_MAX} ký tự')
    return feedback.public(feedback.set_reply(rid, body.text, user), user)


@app.delete('/api/feedback/{rid}/reply')
def unreply_feedback(rid: str, user: str = Depends(current_user)):
    _admin_review(rid, user)
    return feedback.public(feedback.set_reply(rid, '', user), user)


@app.post('/api/feedback/{rid}/reply/draft')
def draft_feedback_reply(rid: str, user: str = Depends(current_user)):
    """A reply written by the LLM for the admin to edit; not published."""
    return {'text': feedback.draft_reply(_admin_review(rid, user))}


STT_BYTES_MAX = 3_000_000   # ~1 minute of compressed audio
STT_TOKENS = 500            # counted against the daily AI budget per transcription


@app.post('/api/stt')
def speech_to_text(file: UploadFile = File(...), user: str = Depends(current_user)):
    from rag import llm
    try:
        accounts.ensure(user, 'llm')
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex))
    data = file.file.read(STT_BYTES_MAX + 1)
    if len(data) > STT_BYTES_MAX:
        raise HTTPException(400, 'Đoạn ghi âm dài quá, hỏi ngắn hơn nhé')
    if len(data) < 1000:
        raise HTTPException(400, 'Không nghe thấy gì, thử nói lại nhé')
    mime = (file.content_type or 'audio/webm').split(';')[0].strip()
    try:
        text = llm.transcribe(data, mime)
    except llm.Unavailable as ex:
        raise HTTPException(503, str(ex))
    accounts.add(user, 'llm', STT_TOKENS)
    return {'text': text}


@app.get('/api/feedback/{rid}/images/{k}')
def feedback_image(rid: str, k: int, user: str = Depends(current_user)):
    r = feedback.get(rid)
    f = feedback.image_path(rid, k)
    if not r or k >= r.get('images', 0) or (r.get('hidden') and not accounts.is_admin(user)) or not f.is_file():
        raise HTTPException(404, 'Không có ảnh này')
    return FileResponse(f, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})


@app.delete('/api/feedback/{rid}')
def delete_feedback(rid: str, user: str = Depends(current_user)):
    if not feedback.delete(rid, None if accounts.is_admin(user) else user):
        raise HTTPException(404, 'Không tìm thấy góp ý của bạn')
    return {'ok': True}


@app.get('/api/me')
def me(user: str = Depends(current_user)):
    return {**accounts.usage(user), 'pinned_limit': None if accounts.is_admin(user) else sessions.PINNED_LIMIT}


class TreeNode(BaseModel):
    p: int                     # parent: index of an earlier node, -1 = the starting position
    r: int
    c: int
    color: int                 # as in Placed
    was: Optional[int] = None
    s: int = -1                # child on the highlighted line (index of a later node), -1 = none


class MoveTree(BaseModel):
    s: int = -1                # the starting position's child on the highlighted line
    nodes: List[TreeNode] = []


class SessionIn(BaseModel):
    base: List[List[int]]              # starting position: the photo's matrix or an empty board
    scan_id: Optional[str] = None
    played: List[Placed] = []
    to_play: str = 'B'
    start_turn: str = 'B'
    matrix: Optional[List[List[int]]] = None   # position on screen (list thumbnails)
    title: Optional[str] = None
    tree: Optional[MoveTree] = None    # every line tried from the starting position; `played` is the one shown


class SessionUpdate(BaseModel):
    played: Optional[List[Placed]] = None
    tree: Optional[MoveTree] = None
    to_play: Optional[str] = None
    start_turn: Optional[str] = None
    matrix: Optional[List[List[int]]] = None
    title: Optional[str] = None


class PinIn(BaseModel):
    pinned: bool


def _check_session(n, fields):
    for p in fields.get('played') or []:
        if not (0 <= p['r'] < n and 0 <= p['c'] < n and p['color'] in (0, 1, 2)):
            raise HTTPException(400, 'Nước đi không hợp lệ')
    if len(fields.get('played') or []) > 2000:
        raise HTTPException(400, 'Quá nhiều nước đi')
    if fields.get('tree') is not None:
        t = fields['tree']
        nodes = t['nodes']
        if len(nodes) > sessions.TREE_MAX:
            raise HTTPException(400, f'Cây nước đi quá lớn (tối đa {sessions.TREE_MAX} nước)')
        if not -1 <= t['s'] < len(nodes) or (t['s'] >= 0 and nodes[t['s']]['p'] != -1):
            raise HTTPException(400, 'Cây nước đi không hợp lệ')
        for i, x in enumerate(nodes):
            if not (-1 <= x['p'] < i and 0 <= x['r'] < n and 0 <= x['c'] < n and x['color'] in (0, 1, 2)
                    and (x['s'] == -1 or (i < x['s'] < len(nodes) and nodes[x['s']]['p'] == i))):
                raise HTTPException(400, 'Cây nước đi không hợp lệ')
    for k in ('to_play', 'start_turn'):
        if fields.get(k) is not None and fields[k] not in ('B', 'W'):
            raise HTTPException(400, f'{k} phải là B hoặc W')
    if fields.get('matrix') is not None:
        _check_board(fields['matrix'], 'B')
        if len(fields['matrix']) != n:
            raise HTTPException(400, 'Kích thước bàn không khớp')
    if fields.get('title') is not None:
        fields['title'] = fields['title'].strip()[:80] or None


@app.get('/api/sessions')
def list_sessions(user: str = Depends(current_user)):
    return sessions.list_sessions(user)


@app.post('/api/sessions')
def create_session(s: SessionIn, user: str = Depends(current_user)):
    _check_board(s.base, s.to_play)
    if s.scan_id is not None and ('/' in s.scan_id or '..' in s.scan_id or not (DATA / s.scan_id).is_dir()):
        raise HTTPException(404, 'Không tìm thấy lượt chụp')
    fields = s.dict()
    _check_session(len(s.base), fields)
    return sessions.create(user, {**fields, 'board_size': len(s.base)})


@app.get('/api/sessions/{sid}')
def get_session(sid: str, user: str = Depends(current_user)):
    s = sessions.get(user, sid)
    if s is None:
        raise HTTPException(404, 'Không tìm thấy ván này')
    return s


@app.put('/api/sessions/{sid}')
def save_session(sid: str, u: SessionUpdate, user: str = Depends(current_user)):
    s = sessions.get(user, sid)
    if s is None:
        raise HTTPException(404, 'Không tìm thấy ván này')
    fields = {k: v for k, v in u.dict().items() if v is not None}
    _check_session(s['board_size'], fields)
    s = sessions.update(user, sid, fields)
    return sessions.summary(s)


@app.post('/api/sessions/{sid}/pin')
def pin_session(sid: str, p: PinIn, user: str = Depends(current_user)):
    try:
        s = sessions.set_pinned(user, sid, p.pinned)
    except sessions.LimitReached as ex:
        raise HTTPException(409, str(ex))
    if s is None:
        raise HTTPException(404, 'Không tìm thấy ván này')
    return sessions.summary(s)


@app.delete('/api/sessions/{sid}')
def delete_session(sid: str, user: str = Depends(current_user)):
    if not sessions.delete(user, sid):
        raise HTTPException(404, 'Không tìm thấy ván này')
    return {'ok': True}


@app.post('/api/sgf')
def import_sgf(file: UploadFile = File(...), user: str = Depends(current_user)):
    """A game record becomes a review session: the starting position, the moves (also kept unchanged as `game`
    so variations tried on the board never lose the game) and an empty review, run with .../review/step."""
    data = file.file.read(500_000)
    try:
        game = review.parse_sgf(data)
    except review.SgfError as ex:
        raise HTTPException(400, str(ex))
    n, moves = game['board_size'], game['moves']
    played = [{'r': rc[0], 'c': rc[1], 'color': 1 if who == 'B' else 2}
              for who, mv in moves if (rc := coach.parse_point(mv, n))]
    matrix, to_play, _ = coach.final_position(game['base'], moves[0][0], moves)
    names = ' vs '.join(x for x in (game['black'], game['white']) if x) or 'Ván SGF'
    title = f'{names}{" · " + game["date"] if game["date"] else ""}'[:80]
    s = sessions.create(user, {'board_size': n, 'base': game['base'], 'scan_id': None, 'played': played,
                               'to_play': to_play, 'start_turn': moves[0][0], 'matrix': matrix, 'title': title,
                               'game': game, 'review': review.new_review(game)})
    return sessions.summary(s)


@app.post('/api/sessions/{sid}/review/step')
def review_step(sid: str, user: str = Depends(current_user)):
    """Some more of the game review (~20 s of KataGo work); call again until status is "done"."""
    s = _own_session(user, sid)
    if not s.get('game'):
        raise HTTPException(400, 'Ván này không có kỷ lục SGF để review')
    r = s.get('review') or review.new_review(s['game'])
    if r['status'] != 'done':
        try:
            r = review.step(s['game'], r, _katago_for(user, sid))
        except QuotaExceeded as ex:
            raise HTTPException(429, str(ex))
        except KataGoError as ex:
            raise HTTPException(ex.status, str(ex))
        finally:
            sessions.set_review(user, sid, r)   # keep what was done, also when stopped by an error
    return review.progress(r)


class LineIn(BaseModel):
    number: int          # the mistake: game move number, from 1
    line: List[str]      # its best variation known so far ("Q16" | "pass")
    count: int = 10


@app.post('/api/sessions/{sid}/review/line')
def review_line(sid: str, body: LineIn, user: str = Depends(current_user)):
    """The next moves of a mistake's best variation, as the user steps through it; kept with the mistake."""
    s = _own_session(user, sid)
    game = s.get('game')
    if not game or not 1 <= body.number <= len(game['moves']):
        raise HTTPException(400, 'Không có nước này trong ván')
    n = game['board_size']
    if len(body.line) >= review.LINE_MAX or any(mv != 'pass' and coach.parse_point(mv, n) is None for mv in body.line):
        raise HTTPException(400, 'Biến không hợp lệ')
    try:
        more = review.extend_line(_katago_for(user, sid), game, body.number - 1, body.line, max(1, min(body.count, 20)))
    except QuotaExceeded as ex:
        raise HTTPException(429, str(ex))
    except KataGoError as ex:
        raise HTTPException(ex.status, str(ex))
    if more:
        sessions.set_mistake_line(user, sid, body.number, body.line + more)
    return {'moves': more}


@app.get('/api/books/{book_id}/pages/{page}')
def book_page(book_id: str, page: int):
    from rag.catalog import BY_ID
    f = BOOK_PAGES / book_id / f'p{page}.jpg'
    if book_id not in BY_ID or page < 1 or not f.is_file():
        raise HTTPException(404, 'Không tìm thấy trang sách')
    return FileResponse(f, headers={'Cache-Control': 'private, max-age=86400'})


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')
