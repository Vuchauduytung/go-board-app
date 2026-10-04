# Reviews of the app: a star rating and a comment, public to every user. An anonymous review shows no name
# publicly but still keeps its author, for admins (spam, follow-up). Admins read and moderate them from the
# Telegram bot (telegram_bot.py). One JSON file per review in FEEDBACK_DIR/<id>.json, its photos (up to 3,
# re-encoded so no EXIF such as GPS position is ever published) in FEEDBACK_DIR/images/<id>-<n>.jpg.

import io
import os
import threading
import time
import uuid
from pathlib import Path

import sessions

ROOT = Path(os.environ.get('FEEDBACK_DIR', sessions.ROOT.parent / 'feedback'))
TEXT_MAX = 2000
IMAGES_MAX = 3
IMAGE_BYTES_MAX = 10_000_000   # per uploaded file, before re-encoding
IMAGE_SIDE = 1600
DAILY_PER_USER = int(os.environ.get('FEEDBACK_DAILY_PER_USER', '5'))
_lock = threading.Lock()


class TooMany(Exception):
    pass


class BadImage(ValueError):
    pass


def encode_image(data):
    """An uploaded photo as a JPEG of at most IMAGE_SIDE pixels, rotated upright, without any metadata."""
    from PIL import Image, ImageOps
    if len(data) > IMAGE_BYTES_MAX:
        raise BadImage(f'Ảnh lớn quá {IMAGE_BYTES_MAX // 1_000_000} MB')
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > 60_000_000:
            raise BadImage('Ảnh lớn quá')
        img = ImageOps.exif_transpose(img).convert('RGB')
    except BadImage:
        raise
    except Exception:
        raise BadImage('File không phải ảnh hoặc ảnh bị hỏng')
    img.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=85, optimize=True)   # a new file: no EXIF, GPS or other metadata carried over
    return out.getvalue()


def image_path(rid, k):
    return ROOT / 'images' / f'{rid}-{k}.jpg' if sessions.ID_RE.match(rid or '') and 0 <= k < IMAGES_MAX else None


def image_paths(r):
    return [image_path(r['id'], k) for k in range(r.get('images', 0))]


def _file(rid):
    return ROOT / f'{rid}.json' if sessions.ID_RE.match(rid or '') else None


def _all():
    items = [r for r in (sessions._read(f) for f in ROOT.glob('*.json')) if r] if ROOT.is_dir() else []
    return sorted(items, key=lambda r: r['created_at'], reverse=True)


def display_name(user):
    """Public name: the e-mail's local part; browsers without an account are guests."""
    return user.split('@')[0] if '@' in user else 'Khách'


def add(user, rating, text, anonymous, images=()):
    """images: JPEGs from encode_image."""
    since = time.time() - 86400
    with _lock:
        if sum(1 for r in _all() if r['user'] == user and r['created_at'] > since) >= DAILY_PER_USER:
            raise TooMany(f'Mỗi ngày gửi tối đa {DAILY_PER_USER} góp ý, cảm ơn bạn!')
        r = {'id': uuid.uuid4().hex, 'user': user, 'anonymous': bool(anonymous), 'rating': rating,
             'text': text, 'created_at': time.time(), 'hidden': False, 'images': len(images[:IMAGES_MAX])}
        for f, data in zip(image_paths(r), images):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        sessions._write(_file(r['id']), r)
    return r


def public(r, viewer=None):
    """A review as every user sees it: no author when anonymous, never the e-mail."""
    return {'id': r['id'], 'rating': r['rating'], 'text': r['text'], 'created_at': r['created_at'],
            'anonymous': r['anonymous'], 'images': r.get('images', 0),
            'author': 'Ẩn danh' if r['anonymous'] else display_name(r['user']), 'mine': r['user'] == viewer}


def list_public(viewer):
    shown = [r for r in _all() if not r.get('hidden')]
    rated = [r['rating'] for r in shown if r.get('rating')]
    return {'reviews': [public(r, viewer) for r in shown],
            'count': len(shown), 'average': round(sum(rated) / len(rated), 2) if rated else None}


def get(rid):
    f = _file(rid)
    return sessions._read(f) if f and f.is_file() else None


def delete(rid, user=None):
    """Remove a review: its author's, or any one when `user` is None (an admin)."""
    r = get(rid)
    if not r or (user is not None and r['user'] != user):
        return False
    with _lock:
        _file(rid).unlink(missing_ok=True)
        for f in image_paths(r):
            f.unlink(missing_ok=True)
    return True


def set_hidden(rid, hidden):
    with _lock:
        r = get(rid)
        if not r:
            return None
        r['hidden'] = bool(hidden)
        sessions._write(_file(rid), r)
    return r


def find(prefix):
    """A review by the first characters of its id (as the bot shows them)."""
    prefix = (prefix or '').strip().lower()
    hits = [r for r in _all() if r['id'].startswith(prefix)] if len(prefix) >= 4 else []
    return hits[0] if len(hits) == 1 else None


def all_reviews(since=None):
    """Every review, newest first, with its author: for admins only."""
    return [r for r in _all() if since is None or r['created_at'] >= since]


def stats(items):
    rated = [r['rating'] for r in items if r.get('rating')]
    return {'count': len(items), 'hidden': sum(1 for r in items if r.get('hidden')),
            'anonymous': sum(1 for r in items if r['anonymous']),
            'average': round(sum(rated) / len(rated), 2) if rated else None,
            'stars': {k: rated.count(k) for k in range(5, 0, -1)}}
