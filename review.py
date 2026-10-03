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


def new_review(game):
    return {'status': 'running', 'phase': 'scan', 'total': len(game['moves']) + 1, 'done': 0, 'positions': {},
            'deep': {}, 'mistakes': {'B': [], 'W': []}, 'started_at': time.time()}


def _scan(katago, game, i):
    """[winrate, lead, best move, best lead, lead of the move played or None] for the side to move at i."""
    res = katago({**position(game, i), 'max_visits': SCAN_VISITS, 'top': 40, 'ownership': False})
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
    review.update(status='done', phase='done', finished_at=time.time())


def step(game, review, katago, seconds=STEP_SECONDS):
    """Do up to `seconds` of work on the review (scan positions, then deepen the candidate mistakes)."""
    end = time.time() + seconds
    with ThreadPoolExecutor(PARALLEL) as pool:
        while review['phase'] == 'scan' and time.time() < end:
            todo = [i for i in range(review['total']) if str(i) not in review['positions']][:PARALLEL]
            if not todo:
                review['phase'] = 'deep'
                review['candidates'] = _candidates(game, review)
                break
            for i, res in zip(todo, pool.map(lambda i: _scan(katago, game, i), todo)):
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
    return {k: review.get(k) for k in ('status', 'phase', 'total', 'done', 'mistakes')} | \
        {'deep_done': len(review.get('deep', {})), 'deep_total': deep_total}
