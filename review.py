# Game review from an SGF file. KataGo searches every position of the main line; the moves that lose the most
# points are each side's mistakes, and every mistake gets KataGo's best line from that position (10 moves).
# The browser drives the review in short steps (POST /api/sessions/{id}/review/step), so it also works where the
# server gets no CPU between requests (Cloud Run); progress is kept in the session and survives a closed page.

import os
import time
from concurrent.futures import ThreadPoolExecutor

import coach

SCAN_VISITS = int(os.environ.get('REVIEW_VISITS', '100'))         # quick pass over every position
STEP_SECONDS = float(os.environ.get('REVIEW_STEP_SECONDS', '20'))  # work per request
PARALLEL = 4
MISTAKES = 10     # listed per side
CANDIDATES = 15   # per side, searched again more deeply before the 10 are picked
LINE = 10         # moves (both sides) in each mistake's best variation
LINE_VISITS = 400  # per search when the browser asks for more of a best variation
LINE_MAX = 300     # moves a best variation can be lengthened to
RULES = {'japanese': 'japanese', 'chinese': 'chinese', 'korean': 'korean', 'aga': 'aga', 'nz': 'new-zealand',
         'new zealand': 'new-zealand', 'tromp-taylor': 'tromp-taylor'}


class SgfError(ValueError):
    pass


def parse_sgf(data):
    """Main line of an SGF file -> {board_size, base, moves [["B", "Q16" | "pass"]], komi, rules, black, white,
    result, date}. Rejects other board sizes and moves onto occupied points."""
    from sgfmill import sgf
    try:
        game = sgf.Sgf_game.from_bytes(data)
    except ValueError as ex:
        raise SgfError(f'File SGF không hợp lệ: {ex}')
    n = game.get_size()
    if n not in (9, 13, 19):
        raise SgfError('Chỉ hỗ trợ bàn 9×9, 13×13 và 19×19')
    root = game.get_root()
    base = [[0] * n for _ in range(n)]
    black, white, _ = root.get_setup_stones()
    for color, points in ((1, black), (2, white)):
        for row, col in points:            # sgfmill: row 0 = bottom
            base[n - 1 - row][col] = color
    moves, board = [], base
    for node in game.get_main_sequence():
        colour, point = node.get_move()
        if colour is None:
            continue
        who = 'B' if colour == 'b' else 'W'
        if point is None:
            moves.append([who, 'pass'])
            continue
        r, c = n - 1 - point[0], point[1]
        if board[r][c]:
            raise SgfError(f'Nước {len(moves) + 1} ({coach.NAMES[who]} {coach.gtp(r, c, n)}) đi vào điểm đã có quân')
        board, _ = coach.play(board, r, c, 1 if who == 'B' else 2)
        moves.append([who, coach.gtp(r, c, n)])
    if not moves:
        raise SgfError('Ván cờ không có nước đi nào')

    def prop(name):
        try:
            return str(root.get(name)).strip() or None
        except KeyError:
            return None
    return {'board_size': n, 'base': base, 'moves': moves,
            'komi': float(game.get_komi()) if root.has_property('KM') else 7.5,
            'rules': RULES.get((prop('RU') or '').lower(), 'chinese'),
            'black': game.get_player_name('b'), 'white': game.get_player_name('w'),
            'result': prop('RE'), 'date': prop('DT')}


def position(game, i):
    """KataGo payload for the position before move i (0-based) of the game."""
    return {'matrix': game['base'], 'to_play': game['moves'][0][0], 'moves': game['moves'][:i],
            'komi': game['komi'], 'rules': game['rules']}


def game_from_moves(base, moves, komi, black, white, result=None, date=None):
    """A game record like parse_sgf's from moves played on `base` ([["B", "Q16" | "pass"]], colours as played).
    Raises SgfError for a move off the board or onto a stone."""
    n = len(base)
    if not moves:
        raise SgfError('Ván cờ không có nước đi nào')
    board = base
    for k, (who, mv) in enumerate(moves):
        if who not in ('B', 'W'):
            raise SgfError(f'Nước {k + 1}: màu không hợp lệ')
        if mv == 'pass':
            continue
        rc = coach.parse_point(mv, n)
        if rc is None or board[rc[0]][rc[1]]:
            raise SgfError(f'Nước {k + 1} ({mv}) không hợp lệ')
        board, _ = coach.play(board, rc[0], rc[1], 1 if who == 'B' else 2)
    return {'board_size': n, 'base': base, 'moves': [list(m) for m in moves], 'komi': float(komi), 'rules': 'chinese',
            'black': black, 'white': white, 'result': result, 'date': date}


def new_review(game):
    return {'status': 'running', 'phase': 'scan', 'total': len(game['moves']) + 1, 'done': 0, 'positions': {},
            'deep': {}, 'mistakes': {'B': [], 'W': []}, 'started_at': time.time()}


def _scan(katago, game, i, peek=None):
    """[winrate, lead, best move, best lead, lead of the move played or None] for the side to move at i. A full
    search of the position done before (a game against the AI searches every position) is used instead."""
    import bot
    known = peek and next((r for s in (coach.ROOT, bot.SEARCH) if (r := peek({**position(game, i), **s}))), None)
    res = known or katago({**position(game, i), 'max_visits': SCAN_VISITS, 'top': 40, 'ownership': False})
    best = res['moves'][0] if res['moves'] else None
    played = game['moves'][i][1] if i < len(game['moves']) else None
    mine = next((m for m in res['moves'] if m['move'] == played and m['visits'] >= 2), None)
    return [res['winrate'], res['score_lead'], best and best['move'], best and best['score_lead'],
            mine and mine['score_lead']]


def _loss(review, i, best_lead=None, played_lead=None):
    """Points move i gave away: best move's value minus the played move's (from the next position if needed)."""
    wr, lead, best, scan_best, scan_played = review['positions'][str(i)]
    best_lead = scan_best if best_lead is None else best_lead
    if played_lead is None:
        played_lead = scan_played if scan_played is not None else -review['positions'][str(i + 1)][1]
    return max(0.0, best_lead - played_lead) if best_lead is not None else 0.0


def _line(katago, game, i, pv):
    """KataGo's line from position i, lengthened with further searches up to LINE moves."""
    line, base = list(pv), position(game, i)
    for _ in range(3):
        if len(line) >= LINE:
            break
        last = base['moves'][-1][0] if base['moves'] else None
        who = coach.OTHER[last] if last else base['to_play']
        colored = [[who if k % 2 == 0 else coach.OTHER[who], mv] for k, mv in enumerate(line)]
        res = katago({**base, 'moves': base['moves'] + colored, 'max_visits': SCAN_VISITS * 2, 'top': 1,
                      'ownership': False})
        if not res['moves'] or not res['moves'][0]['pv']:
            break
        line += res['moves'][0]['pv']
    return line[:LINE]


def extend_line(katago, game, i, line, count=LINE):
    """The next `count` moves of the best variation `line` from the position before move i of the game."""
    return extend_from(katago, position(game, i), game['moves'][i][0], line, count)


def extend_from(katago, base, first, line, count=LINE):
    """The next `count` moves of the best variation `line` (first played by `first`) from the KataGo position `base`:
    KataGo's best line from where `line` ends, searched again from its end until long enough. Fewer (or none) when
    the game ends."""
    out = []
    for _ in range(4):
        moves = line + out
        if len(out) >= count or moves[-2:] == ['pass', 'pass']:
            break
        colored = [[first if k % 2 == 0 else coach.OTHER[first], mv] for k, mv in enumerate(moves)]
        res = katago({**base, 'moves': base.get('moves', []) + colored, 'max_visits': LINE_VISITS, 'top': 1,
                      'ownership': False})
        if not res['moves'] or not res['moves'][0]['pv']:
            break
        out += res['moves'][0]['pv']
    return out[:count]


def _deepen(katago, game, review, i):
    """A full search of the position before move i: exact loss and the best variation."""
    res = katago({**position(game, i), **coach.ROOT})
    if not res['moves']:
        return {'loss': _loss(review, i), 'best': None, 'line': []}
    best, played = res['moves'][0], game['moves'][i][1]
    mine = next((m for m in res['moves'] if m['move'] == played and m['visits'] >= 2), None)
    loss = _loss(review, i, best['score_lead'], mine and mine['score_lead'])
    return {'loss': loss, 'best': best['move'], 'best_lead': best['score_lead'],
            'line': _line(katago, game, i, best['pv'])}


def _candidates(game, review):
    out = {'B': [], 'W': []}
    for i, (who, _) in enumerate(game['moves']):
        loss = _loss(review, i)
        if loss > 0.5:
            out[who].append((loss, i))
    return {who: [i for _, i in sorted(c, reverse=True)[:CANDIDATES]] for who, c in out.items()}


def _finish(game, review):
    p = review['positions']
    for who in ('B', 'W'):
        found = []
        for key, d in review['deep'].items():
            i = int(key)
            if game['moves'][i][0] != who or d['loss'] <= 0.5:
                continue
            found.append({'number': i + 1, 'color': who, 'move': game['moves'][i][1], 'loss': round(d['loss'], 1),
                          'winrate_before': p[str(i)][0], 'winrate_after': round(1 - p[str(i + 1)][0], 4),
                          'best': d['best'], 'line': d['line']})
        review['mistakes'][who] = sorted(found, key=lambda m: -m['loss'])[:MISTAKES]
    review['opening'] = opening_review(game, review)
    review['opening_version'] = OPENING_VERSION
    review.update(status='done', phase='done', finished_at=time.time())


# ---- the opening against KataGo's joseki (joseki.py): each corner's moves (those in its quarter of the board, during
# the first OPENING_MOVES) are followed in KataGo's joseki tree, turned to its top-right corner (and across the
# corner's diagonal), until the game leaves the tree (often with a move KataGo finds fine) or a tenuki breaks the
# corner's sequence. A corner with a costly move (OPENING_LOSS or more) gets the joseki from where the game left the
# tree: the tree's best move there, then its main line until the joseki ends. ----
OPENING_MOVES = 40
OPENING_LOSS = 1.0
OPENING_VERSION = 2   # reviews with another version get their opening review again
CORNERS = (('trên-phải', False, False), ('trên-trái', False, True), ('dưới-phải', True, False), ('dưới-trái', True, True))
JOSEKI_LINE = 10


def _turn(rc, flip_r, flip_c, n=19):
    """A point of a corner to the top-right one (and back: each flip undoes itself)."""
    return (n - 1 - rc[0] if flip_r else rc[0], n - 1 - rc[1] if flip_c else rc[1])


def _quarter(rc, n=19):
    """The corner whose quarter holds the point (the middle lines go to the top and the right)."""
    top, right = rc[0] <= n // 2, rc[1] >= n // 2
    return next(i for i, (_, fr, fc) in enumerate(CORNERS) if fr != top and fc != right)


def opening_review(game, review, nodes=None):
    """Corners with a costly opening move -> [{number, color, move, loss (the costly move), corner, left (move number
    where the game left the joseki), left_move, left_color, left_loss, joseki: [[color, move]…] from there, ends,
    tenuki (the joseki was over there: play elsewhere), played_rating, known (joseki moves the corner followed)}]"""
    import joseki
    if game['board_size'] != 19 or any(v for row in game['base'] for v in row):
        return []
    if nodes is None:
        f = joseki.load()
        if f is None:
            return []
        import json
        nodes = json.loads(f.read_text())['nodes']
    moves, pos, out = game['moves'], review['positions'], []
    loss = lambda i: _loss(review, i) if str(i) in pos and str(i + 1) in pos else 0.0
    for ci, (name, fr, fc) in enumerate(CORNERS):
        turn = lambda p: joseki.gtp(*_turn(coach.parse_point(p, 19), fr, fc))
        corner = [(i, w, m) for i, (w, m) in enumerate(moves[:OPENING_MOVES])
                  if m != 'pass' and _quarter(coach.parse_point(m, 19)) == ci]
        costly = [(i, w, m) for i, w, m in corner if loss(i) >= OPENING_LOSS]
        if len(corner) < 2 or not costly:
            continue
        for diag in (False, True):   # the side of the diagonal the game's first corner move matches
            first = joseki.mirror(turn(corner[0][2])) if diag else turn(corner[0][2])
            if first in nodes:
                break
        else:
            continue
        seq, left = [first], None
        for (_, w0, _), (i, w, m) in zip(corner, corner[1:]):
            t = joseki.mirror(turn(m)) if diag else turn(m)
            if w != w0 and ' '.join(seq + [t]) in nodes and any(
                    k['move'] == t for k in nodes[' '.join(seq)].get('next', []) if not k.get('tenuki')):
                seq.append(t)
                continue
            left = (i, w, m, t, w == w0)
            break
        if left is None or left[4] and not nodes.get(' '.join(seq), {}).get('settled'):
            continue                 # never left the joseki, or a tenuki broke the corner before the joseki ended
        i, w, m, t, _ = left
        bad = next(((j, ww, mm) for j, ww, mm in costly if j >= i), None)
        if bad is None:
            continue                 # the costly moves came before the joseki was left: they are joseki moves
        node = nodes[' '.join(seq)]
        kids = [k for k in node.get('next', []) if not k.get('tenuki')]
        back = lambda mv: coach.gtp(*_turn(joseki.rc(joseki.mirror(mv) if diag else mv), fr, fc), 19)
        line, key = [], ' '.join(seq)
        for _ in range(JOSEKI_LINE):
            n = nodes.get(key)
            nxt = [k for k in (n or {}).get('next', []) if not k.get('tenuki')]
            if not n or not nxt or (n.get('settled') and line):
                break
            line.append(nxt[0]['move'])
            key += ' ' + nxt[0]['move']
        colors = [w if k % 2 == 0 else coach.OTHER[w] for k in range(len(line))]
        mine = next((k for k in kids if k['move'] == t), None)
        out.append({'number': bad[0] + 1, 'color': bad[1], 'move': bad[2], 'loss': round(loss(bad[0]), 1), 'corner': name,
                    'left': i + 1, 'left_move': m, 'left_color': w, 'left_loss': round(loss(i), 1),
                    'joseki': [[c, back(x)] for c, x in zip(colors, line)],
                    'ends': bool(nodes.get(key, {}).get('settled')), 'tenuki': bool(node.get('settled')) and not line,
                    'played_rating': mine and mine['rating'], 'known': len(seq)})
    return sorted(out, key=lambda o: o['number'])


def step(game, review, katago, seconds=STEP_SECONDS, peek=None):
    """Do up to `seconds` of work on the review (scan positions, then deepen the candidate mistakes).
    peek(payload): an answer already known for a query, or None (see _scan)."""
    end = time.time() + seconds
    with ThreadPoolExecutor(PARALLEL) as pool:
        while review['phase'] == 'scan' and time.time() < end:
            todo = [i for i in range(review['total']) if str(i) not in review['positions']][:PARALLEL]
            if not todo:
                review['phase'] = 'deep'
                review['candidates'] = _candidates(game, review)
                break
            for i, res in zip(todo, pool.map(lambda i: _scan(katago, game, i, peek), todo)):
                review['positions'][str(i)] = res
            review['done'] = len(review['positions'])
        while review['phase'] == 'deep' and time.time() < end:
            todo = [i for who in ('B', 'W') for i in review['candidates'][who] if str(i) not in review['deep']][:PARALLEL]
            if not todo:
                _finish(game, review)
                break
            for i, res in zip(todo, pool.map(lambda i: _deepen(katago, game, review, i), todo)):
                review['deep'][str(i)] = res
    return review


def progress(review):
    """What the browser needs: no per-position data."""
    if not review:
        return None
    deep_total = sum(len(v) for v in review.get('candidates', {}).values())
    return {k: review.get(k) for k in ('status', 'phase', 'total', 'done', 'mistakes', 'opening')} | \
        {'deep_done': len(review.get('deep', {})), 'deep_total': deep_total}
