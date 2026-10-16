# Exercises from the users' mistakes, in one bank every user can browse and solve. A finished game review gives
# the biggest mistakes (at least MIN_LOSS points) of the player(s) it was about: the position before each one is a
# problem for the side that went wrong, its answers KataGo's best move and the moves about as good (from the
# review's full search: no more KataGo), its solution KataGo's best line. Each is sorted by the shape around the
# answer: life and death, connecting / cutting, tesuji, opening or direction of play.
# One JSON file per exercise in EXERCISES_DIR/<id>.json; the id comes from the position and its answer, so the same
# problem found in two games is kept once. Who solved what is kept with it, by user hash (no e-mail addresses).

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import accounts
import coach

ROOT = Path(os.environ.get('EXERCISES_DIR', Path(os.environ.get('DATA_DIR', Path(__file__).parent / 'data')) / 'exercises'))
MIN_LOSS = 2.0      # points a mistake must lose to become an exercise
PER_GAME = 5        # exercises from one game at most
CLOSE = 0.5         # moves within this many points of the best one are right answers too
OPENING = 40        # moves of the opening
KINDS = {'song-chet': 'Sống chết', 'noi-cat': 'Nối / cắt', 'tesuji': 'Tesuji', 'khai-cuoc': 'Khai cuộc',
         'dai-cuc': 'Hướng đi / đại cục'}
_lock = threading.Lock()


def _near(n, r, c, d):
    return [(y, x) for y in range(max(0, r - d), min(n, r + d + 1)) for x in range(max(0, c - d), min(n, c + d + 1))]


def _groups(board, points, color):
    """The distinct groups of `color` with a stone on one of the points: [(stones, liberties)]."""
    out, seen = [], set()
    for y, x in points:
        if board[y][x] == color and (y, x) not in seen:
            stones, libs = coach.group(board, y, x)
            seen |= stones
            out.append((stones, libs))
    return out


def classify(board, who, move, number):
    """The kind of problem whose answer is `move` for `who` ("B"/"W") on `board`, at game move `number`."""
    n, rc = len(board), coach.parse_point(move, len(board))
    if rc is None or board[rc[0]][rc[1]]:
        return 'dai-cuc'
    r, c = rc
    me = 1 if who == 'B' else 2
    after, captured = coach.play(board, r, c, me)
    sides = [(y, x) for y, x in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)) if 0 <= y < n and 0 <= x < n]
    if captured or any(len(libs) == 1 and len(stones) >= 2 for stones, libs in _groups(after, sides, 3 - me)):
        return 'tesuji'                                   # captures, or ataris a group
    mine, theirs = _groups(board, sides, me), _groups(board, sides, 3 - me)
    if any(len(libs) <= 2 and len(stones) >= 3 for stones, libs in mine + theirs):
        return 'song-chet'                                # saves, or kills, a group short of liberties
    if len(mine) >= 2 or len(theirs) >= 2:
        return 'noi-cat'                                  # joins two groups, or cuts two apart
    around = _near(n, r, c, 2)
    if any(len(stones) >= 4 and len(libs) <= 4 for col in (1, 2) for stones, libs in _groups(board, around, col)):
        return 'song-chet'                                # a weak group close by
    return 'khai-cuoc' if number <= OPENING else 'dai-cuc'


def from_review(game, review, sides, by):
    """Exercises from a finished review: the biggest mistakes of `sides`, each the position before it."""
    found = []
    for who in sides:
        for m in review.get('mistakes', {}).get(who, []):
            if m['loss'] >= MIN_LOSS and m.get('best') and m['best'] != 'pass':
                found.append(m)
    out = []
    for m in sorted(found, key=lambda m: -m['loss'])[:PER_GAME]:
        i, who = m['number'] - 1, m['color']
        board, _, ko = coach.final_position(game['base'], game['moves'][0][0], game['moves'][:i])
        deep = review.get('deep', {}).get(str(i), {})
        answers = [m['best']] + [mv for mv in deep.get('good', []) if mv != m['best'] and mv != 'pass']
        key = json.dumps([board, who, m['best']])
        out.append({'id': hashlib.sha1(key.encode()).hexdigest()[:16], 'kind': classify(board, who, m['best'], m['number']),
                    'board_size': len(board), 'matrix': board, 'to_play': who, 'ko': ko and coach.gtp(*ko, len(board)),
                    'last': game['moves'][i - 1][1] if i else None, 'komi': game['komi'],
                    'answers': answers, 'line': m.get('line') or [m['best']], 'played': m['move'], 'loss': m['loss'],
                    'number': m['number'], 'source': {'black': game.get('black'), 'white': game.get('white'),
                                                      'date': game.get('date')},
                    'by': by, 'created_at': time.time(), 'solved': {}})
    return out


def _file(eid):
    return ROOT / f'{eid}.json' if eid and eid.isalnum() and len(eid) <= 32 else None


def _read(f):
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError, AttributeError):
        return None


def _write(f, e):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f'.{f.stem}.{uuid.uuid4().hex[:8]}.tmp')
    tmp.write_text(json.dumps(e, ensure_ascii=False))
    os.replace(tmp, f)


def add(items, owner):
    """Keep new exercises (one already in the bank stays as it is); their ids, in order."""
    with _lock:
        for e in items:
            f = _file(e['id'])
            if not f.exists():
                _write(f, {**e, 'owner': accounts.user_dir_name(owner)})
    return [e['id'] for e in items]


def _mine(e, user):
    return e.get('solved', {}).get(accounts.user_dir_name(user))


def summary(e, user):
    """What the bank's list shows: no answers."""
    s = e.get('solved', {})
    return {'id': e['id'], 'kind': e['kind'], 'kind_name': KINDS.get(e['kind'], e['kind']), 'to_play': e['to_play'],
            'board_size': e['board_size'], 'number': e['number'], 'loss': e['loss'], 'by': e['by'],
            'created_at': e['created_at'], 'tried': len(s), 'solvers': sum(1 for v in s.values() if v.get('ok')),
            'mine': _mine(e, user), 'own': e.get('owner') == accounts.user_dir_name(user)}


def problem(e, user):
    """An exercise to solve: the position, not its answers (those come with an attempt)."""
    return {**summary(e, user), 'matrix': e['matrix'], 'komi': e['komi'], 'ko': e.get('ko'), 'last': e.get('last'),
            'source': e.get('source')}


def listing(user, ids=None):
    if ids is not None:
        items = [e for e in (_read(_file(i)) for i in ids) if e]
    else:
        items = [e for e in (_read(f) for f in ROOT.glob('*.json')) if e] if ROOT.is_dir() else []
    items.sort(key=lambda e: -e['created_at'])
    return [summary(e, user) for e in items]


def get(eid):
    return _read(_file(eid))


def attempt(eid, user, move):
    """The user's answer (a point, or None to see the solution): right or not, and the solution. The first answer
    counts for the bank's statistics; a later right answer marks the exercise solved, not solved first time."""
    with _lock:
        f = _file(eid)
        e = _read(f)
        if not e:
            return None
        ok = move in e['answers']
        me = accounts.user_dir_name(user)
        rec = e.setdefault('solved', {}).get(me)
        if rec is None:
            rec = e['solved'][me] = {'ok': ok, 'first': ok, 'tries': 0, 'at': time.time()}
        rec['tries'] += 1
        rec['ok'] = rec['ok'] or ok
        _write(f, e)
    return {'ok': ok, 'answers': e['answers'], 'line': e['line'], 'played': e['played'], 'loss': e['loss'],
            'mine': rec}


def delete(eid, user):
    with _lock:
        f = _file(eid)
        e = _read(f)
        if not e or not (accounts.is_admin(user) or e.get('owner') == accounts.user_dir_name(user)):
            return False
        f.unlink()
    return True
