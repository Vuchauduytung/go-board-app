# Review sessions: a starting position (photo or empty board), the stones placed / removed on it and the
# coach conversation, so users can continue where they left off.
# One JSON file per session in SESSIONS_DIR/<user hash>/<id>.json, only ever read for that user. Each user keeps
# their 10 most recent sessions plus the ones they pinned (PINNED_LIMIT for normal users, no limit for admins).
# Every KataGo answer used in a session is also kept with it (<id>.kg/<key>.json) for repeated questions.

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import accounts

ROOT = Path(os.environ.get('SESSIONS_DIR', Path(os.environ.get('DATA_DIR', Path(__file__).parent / 'data')) / 'sessions'))
RECENT = 10
PINNED_LIMIT = int(os.environ.get('SESSIONS_PINNED_LIMIT', '20'))
CHAT_MAX = 100   # coach exchanges kept per session
ID_RE = re.compile(r'^[0-9a-f]{32}$')
_lock = threading.Lock()


class LimitReached(Exception):
    pass


def _dir(user):
    return ROOT / accounts.user_dir_name(user)


def _file(user, sid):
    if not ID_RE.match(sid or ''):
        return None
    return _dir(user) / f'{sid}.json'


def _read(f):
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def _write(f, s):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False))
    tmp.replace(f)


def _all(user):
    d = _dir(user)
    items = [s for s in (_read(f) for f in d.glob('*.json')) if s] if d.is_dir() else []
    return sorted(items, key=lambda s: s['updated_at'], reverse=True)


def summary(s):
    return {k: s.get(k) for k in ('id', 'title', 'pinned', 'created_at', 'updated_at', 'board_size', 'scan_id',
                                  'matrix', 'to_play')} \
        | {'moves': sum(1 for p in s.get('played', []) if p.get('color')), 'questions': len(s.get('chat', [])),
           'sgf': bool(s.get('game')), 'review': (s.get('review') or {}).get('status')}


def list_sessions(user):
    items = _all(user)
    return {'recent': [summary(s) for s in items[:RECENT]],
            'pinned': [summary(s) for s in items if s.get('pinned')],
            'pinned_limit': None if accounts.is_admin(user) else PINNED_LIMIT}


def _prune(user):
    """Forget unpinned sessions older than the 10 most recent ones."""
    for s in [s for s in _all(user) if not s.get('pinned')][RECENT:]:
        _remove(user, s['id'])


def _remove(user, sid):
    _file(user, sid).unlink(missing_ok=True)
    shutil.rmtree(_cache_dir(user, sid), ignore_errors=True)


def create(user, board):
    """board: board_size, base, scan_id, played, to_play, start_turn, matrix, title; game + review for an SGF."""
    now = time.time()
    s = {'id': uuid.uuid4().hex, 'created_at': now, 'updated_at': now, 'pinned': False, 'chat': [],
         'title': board.get('title') or time.strftime('Ván %d/%m %H:%M'), **{k: v for k, v in board.items() if k != 'title'}}
    with _lock:
        _write(_file(user, s['id']), s)
        _prune(user)
    return s


def get(user, sid):
    f = _file(user, sid)
    return _read(f) if f and f.is_file() else None


def _change(user, sid, fn):
    f = _file(user, sid)
    with _lock:
        s = _read(f) if f and f.is_file() else None
        if s is None:
            return None
        fn(s)
        _write(f, s)
    return s


def update(user, sid, fields):
    """Board state (played, to_play, start_turn, matrix) and title; the starting position never changes."""
    def fn(s):
        s.update({k: v for k, v in fields.items() if k in ('played', 'to_play', 'start_turn', 'matrix', 'title')})
        s['updated_at'] = time.time()
    return _change(user, sid, fn)


def set_pinned(user, sid, pinned):
    if pinned and not accounts.is_admin(user):
        if sum(1 for s in _all(user) if s.get('pinned') and s['id'] != sid) >= PINNED_LIMIT:
            raise LimitReached(f'Chỉ lưu được tối đa {PINNED_LIMIT} ván. Bỏ lưu bớt một ván cũ trước nhé.')
    return _change(user, sid, lambda s: s.update(pinned=bool(pinned)))


def delete(user, sid):
    f = _file(user, sid)
    if not f or not f.is_file():
        return False
    with _lock:
        _remove(user, sid)
    return True


def append_chat(user, sid, entry):
    def fn(s):
        s['chat'] = (s.get('chat', []) + [{**entry, 'at': time.time()}])[-CHAT_MAX:]
        s['updated_at'] = time.time()
    return _change(user, sid, fn)


def has_scan(user, scan_id):
    return any(s.get('scan_id') == scan_id for s in _all(user))


def set_review(user, sid, review):
    return _change(user, sid, lambda s: s.update(review=review, updated_at=time.time()))


def _cache_dir(user, sid):
    return _dir(user) / f'{sid}.kg'


def cache_get(user, sid, key):
    """KataGo answer kept with the session, or None."""
    f = _cache_dir(user, sid) / f'{key}.json' if _file(user, sid) else None
    return _read(f) if f and f.is_file() else None


def cache_put(user, sid, key, value):
    f = _file(user, sid)
    if f is None or not f.is_file():
        return
    d = _cache_dir(user, sid)
    d.mkdir(parents=True, exist_ok=True)
    _write(d / f'{key}.json', value)


def history(s, turns):
    """The last `turns` coach exchanges as chat messages for the LLM."""
    out = []
    for e in s.get('chat', [])[-turns:]:
        out += [{'role': 'user', 'content': e['q']}, {'role': 'assistant', 'content': e['a']}]
    return out
