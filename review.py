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
GOOD = 0.5         # other moves within this many points of the best one, kept as right answers (exercises)
# What the review is for: learning to develop in the opening, more than fights. A mistake ranks by the points it lost
# times the weight of its kind (exercises.classify, by the shape around the best move): a group dying for 20 points
# still shows, while fights of a few points give way to the opening's development mistakes. The exercises weigh the
# other way (exercises.FOCUS); FIGHTS more of each side's biggest fights are searched too, for them.
FOCUS = {'khai-cuoc': 2.0, 'dai-cuc': 1.0, 'noi-cat': 0.7, 'tesuji': 0.5, 'song-chet': 0.5}
FIGHTS = 5
# Taught at the player's level, with KataGo's human SL net on the VM's CPU (katago_server.py): of the moves about as
# good as the best one (TEACH points), the one players of the level would most likely play, and one close to the move
# played (NEAR lines: the same purpose) when there is one; KataGo's own best move stays next to it. A mistake that
# players STRONGER ranks above play at most half as often is one of the level's typical mistakes: weighed TYPICAL.
# The level is the one the player chose, else each side's guessed from the points it lost per move (bot.LEVELS).
TEACH = 1.0
NEAR = 3
STRONGER = 4
TYPICAL = 1.5
RANKS = [f'{k}k' for k in range(20, 0, -1)] + [f'{d}d' for d in range(1, 10)]
HUMAN_VISITS = 10   # the human policy needs no search
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


def guess_level(game, review, who):
    """A side's level from the points it lost per move (each capped at 10), next to the AI levels' (bot.LEVELS):
    past 5k, a kyu more for every 0.3 point more, down to 15k."""
    import bot
    p = review['positions']
    losses = [min(10.0, _loss(review, i)) for i, (w, _) in enumerate(game['moves'])
              if w == who and str(i) in p and str(i + 1) in p]
    if not losses:
        return '5k'
    avg = sum(losses) / len(losses)
    table = sorted((t, lv) for lv, (t, _) in bot.LEVELS.items())
    if avg > table[-1][0]:
        return f'{min(15, int(table[-1][1][:-1]) + round((avg - table[-1][0]) / 0.3))}k'
    return min(table, key=lambda x: abs(x[0] - avg))[1]


def _human(katago, game, i, level):
    """{move: probability} players of `level` play in the position before move i, or None (no human net)."""
    try:
        res = katago({**position(game, i), 'max_visits': HUMAN_VISITS, 'top': 1, 'ownership': False, 'engine': 'cpu',
                      'human_profile': f'rank_{level}'})
    except Exception as ex:   # the review goes on without it
        import logging
        logging.getLogger('review').warning('human policy failed: %s', ex)
        return None
    return dict(res['human_policy']) if res.get('human_policy') else None


def _teach(katago, game, i, res, level):
    """The move to teach at `level` and whether the move played is one of the level's typical mistakes."""
    who, played = game['moves'][i]
    n = game['board_size']
    hp = _human(katago, game, i, level)
    if not hp:
        return None
    best, top = res['moves'][0], res['moves'][0]['visits']
    cands = [m for m in res['moves'] if m['move'] != 'pass' and m['score_lead'] >= best['score_lead'] - TEACH
             and m['visits'] >= max(10, top * 0.1)] or [best]
    pr = coach.parse_point(played, n)
    dist = lambda m: (lambda rc: max(abs(rc[0] - pr[0]), abs(rc[1] - pr[1])) if rc and pr else 99)(coach.parse_point(m['move'], n))
    near = [m for m in cands if dist(m) <= NEAR]
    teach = max(near or cands, key=lambda m: (hp.get(m['move'], 0), m['score_lead']))
    stronger = RANKS[min(len(RANKS) - 1, RANKS.index(level) + STRONGER)]
    hs = _human(katago, game, i, stronger) or {}
    mine = hp.get(played, 0)
    return {'teach': teach, 'intent': bool(near) and teach in near, 'level': level,
            'typical': mine >= 0.02 and hs.get(played, 0) <= mine / 2, 'played_prior': round(mine, 3)}


def _board(game, i):
    p = position(game, i)
    return coach.final_position(p['matrix'], p['to_play'], p['moves'])[0]


def _deepen(katago, game, review, i):
    """A full search of the position before move i: exact loss, the best variation and the kind of mistake."""
    import exercises
    res = katago({**position(game, i), **coach.ROOT})
    if not res['moves']:
        return {'loss': _loss(review, i), 'best': None, 'line': []}
    best, played = res['moves'][0], game['moves'][i][1]
    mine = next((m for m in res['moves'] if m['move'] == played and m['visits'] >= 2), None)
    loss = _loss(review, i, best['score_lead'], mine and mine['score_lead'])
    good = [m['move'] for m in res['moves'][1:] if m['score_lead'] >= best['score_lead'] - GOOD
            and m['visits'] >= max(10, best['visits'] * 0.1)][:3]
    out = {'loss': loss, 'best': best['move'], 'best_lead': best['score_lead'], 'good': good,
           'line': _line(katago, game, i, best['pv']),
           'kind': exercises.classify(_board(game, i), game['moves'][i][0], best['move'], i + 1)}
    level = review.get('levels', {}).get(game['moves'][i][0])
    t = level and _teach(katago, game, i, res, level)
    if t:
        m = t['teach']
        out.update(teach=m['move'], teach_loss=round(max(0.0, best['score_lead'] - m['score_lead']), 1),
                   teach_line=out['line'] if m is best else _line(katago, game, i, m['pv']), intent=t['intent'],
                   typical=t['typical'], level=level)
    return out


def _candidates(game, review):
    """Each side's moves to search again: the CANDIDATES first by weighted loss, and its FIGHTS biggest fights."""
    import exercises
    out = {'B': [], 'W': []}
    board, nxt = game['base'], game['moves'][0][0]
    for i, (who, mv) in enumerate(game['moves']):
        loss, best = _loss(review, i), review['positions'][str(i)][2]
        if loss > 0.5:
            kind = exercises.classify(board, who, best, i + 1) if best else 'dai-cuc'
            out[who].append((loss * FOCUS[kind], loss if kind in ('song-chet', 'tesuji') else 0, i))
        board, nxt, _ = coach.final_position(board, nxt, [[who, mv]])
    pick = {}
    for who, c in out.items():
        first = [i for _, _, i in sorted(c, reverse=True)[:CANDIDATES]]
        fights = [i for _, f, i in sorted(c, key=lambda x: -x[1])[:FIGHTS] if f > 0 and i not in first]
        pick[who] = first + fights
    return pick


def _finish(game, review):
    p = review['positions']
    for who in ('B', 'W'):
        found = []
        for key, d in review['deep'].items():
            i = int(key)
            if game['moves'][i][0] != who or d['loss'] <= 0.5:
                continue
            # shown: the move taught at the player's level and its line; KataGo's best next to it when different
            found.append({'number': i + 1, 'color': who, 'move': game['moves'][i][1], 'loss': round(d['loss'], 1),
                          'winrate_before': p[str(i)][0], 'winrate_after': round(1 - p[str(i + 1)][0], 4),
                          'best': d.get('teach') or d['best'], 'line': d.get('teach_line') or d['line'],
                          'kata_best': d['best'], 'teach_loss': d.get('teach_loss'), 'intent': d.get('intent'),
                          'typical': d.get('typical'), 'level': d.get('level'), 'kind': d.get('kind', 'dai-cuc')})
        rank = lambda m: m['loss'] * FOCUS[m['kind']] * (TYPICAL if m['typical'] else 1)
        kept = {}   # the same move to play, missed move after move: one mistake, its repeats listed with it
        for m in sorted(found, key=lambda m: -rank(m)):
            if m['best'] in kept:
                kept[m['best']].setdefault('repeats', []).append(m['number'])
            elif len(kept) < MISTAKES:
                kept[m['best']] = m
        for m in kept.values():
            m.get('repeats', []).sort()
        review['mistakes'][who] = list(kept.values())
    review.update(status='done', phase='done', finished_at=time.time())


# ---- the joseki review, apart from the game's review and on the VM's CPU KataGo: each corner's moves (those in its
# quarter of the board, during the first OPENING_MOVES) are followed in KataGo's joseki tree (joseki.py), turned to its
# top-right corner (and across the corner's diagonal), until the game leaves the tree or a tenuki breaks the corner's
# sequence before the joseki ended. From where it left, the corner's moves are searched (SCAN_VISITS each) for their
# loss; the review lists every corner that left the joseki: the move that left it, the first costly move after it
# (OPENING_LOSS or more) if any, and the joseki to play from there (the tree's best move, then its main line).
# The player's joseki is kept when it left the tree for a move losing less than KEEP_LOSS: then only a costly move
# after it is corrected, where it was played. Otherwise, of the tree's branches about as good as its best (EQUAL), the
# joseki proposed is the one played out to its end with the fewest tenukis, then one this player was not shown yet
# (`seen`, from their other games: knowing more joseki), then the one players of their level would most likely play
# (the human SL net). ----
OPENING_MOVES = 40
OPENING_LOSS = 1.0
CORNER_MOVES = 8      # moves of a corner searched from where it left the joseki
CORNERS = (('trên-phải', False, False), ('trên-trái', False, True), ('dưới-phải', True, False), ('dưới-trái', True, True))
JOSEKI_LINE = 30     # the joseki to play is followed through the deepened tree up to this many moves…
JOSEKI_TENUKI = 1    # …with at most this many tenukis in it (the corner's local moves are always preferred)
KEEP_LOSS = 1.5
EQUAL = 0.5


def _turn(rc, flip_r, flip_c, n=19):
    """A point of a corner to the top-right one (and back: each flip undoes itself)."""
    return (n - 1 - rc[0] if flip_r else rc[0], n - 1 - rc[1] if flip_c else rc[1])


def _quarter(rc, n=19):
    """The corner whose quarter holds the point (the middle lines go to the top and the right)."""
    top, right = rc[0] <= n // 2, rc[1] >= n // 2
    return next(i for i, (_, fr, fc) in enumerate(CORNERS) if fr != top and fc != right)


def _tree_nodes():
    import json
    import joseki
    f = joseki.load()
    return json.loads(f.read_text())['nodes'] if f else None


def corner_walks(game, nodes):
    """Each corner that left the joseki: {name, fr, fc, diag, seq (tree moves followed), left: (index, colour, move,
    tree move), after: [indices of the corner's moves from there]}."""
    import joseki
    if game['board_size'] != 19 or any(v for row in game['base'] for v in row) or not nodes:
        return []
    moves, out = game['moves'], []
    for ci, (name, fr, fc) in enumerate(CORNERS):
        turn = lambda p: joseki.gtp(*_turn(coach.parse_point(p, 19), fr, fc))
        corner = [(i, w, m) for i, (w, m) in enumerate(moves[:OPENING_MOVES])
                  if m != 'pass' and _quarter(coach.parse_point(m, 19)) == ci]
        if len(corner) < 2:
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
            if w == w0 and not nodes.get(' '.join(seq), {}).get('settled'):
                break                # a tenuki broke the corner before the joseki ended: nothing to compare
            left = (i, w, m, t)
            break
        if left:
            out.append({'name': name, 'fr': fr, 'fc': fc, 'diag': diag, 'seq': seq, 'left': left, 'first': corner[0][1],
                        'after': [i for i, _, _ in corner if i >= left[0]][:CORNER_MOVES]})
    return out


def joseki_needs(game, nodes):
    """Positions (indices before a move) the joseki review searches."""
    need = set()
    for c in corner_walks(game, nodes):
        for i in c['after']:
            need |= {i, i + 1}
    return sorted(i for i in need if i <= len(game['moves']))


def _follow(nodes, key):
    """The tree's main line from `key` -> (moves, None for a tenuki, number of tenukis, whether the corner ends)."""
    line = []
    while len(line) < JOSEKI_LINE:
        nxt = (nodes.get(key) or {}).get('next', [])
        here = [k for k in nxt if not k.get('tenuki')]
        if not here and (not nxt or line.count(None) >= JOSEKI_TENUKI):
            break
        k = (here or nxt)[0]
        line.append(None if k.get('tenuki') else k['move'])
        key += ' ' + k['move']
    ends = bool((nodes.get(key) or {}).get('settled'))   # a branch not grown yet is no end
    while line and line[-1] is None:   # a line never ends on a tenuki
        line.pop()
    return line, line.count(None), ends


def _choose(nodes, key, prior, seen):
    """The joseki to propose from `key` -> (line, ends, the tree key of its first move), or None."""
    kids = [k for k in (nodes.get(key) or {}).get('next', []) if not k.get('tenuki')]
    if not kids:
        return None
    low = min(k.get('delta', 0) for k in kids)
    opts = []
    for k in kids:
        if k.get('delta', 0) <= low + EQUAL:
            nk = f"{key} {k['move']}"
            line, tenukis, ends = _follow(nodes, nk)
            opts.append(((ends, -tenukis, nk not in seen, prior(k['move']), -k.get('delta', 0)), [k['move']] + line, ends, nk))
    _, line, ends, nk = max(opts, key=lambda o: o[0])
    return line, ends, nk


def joseki_results(game, positions, nodes, human=None, seen=frozenset()):
    """The joseki review from the corners' searched positions -> [{corner, left, left_color, left_move, left_loss,
    number, color, move, loss (the first costly move from there, or None), kept (the player's joseki is kept), fix
    (the move to play instead of that costly one, when kept), joseki: [[colour, move]…] (proposed), ends, tenuki,
    played_rating, known, pick (the tree key of the joseki proposed), explain ({first, before, moves}: the corner in
    the tree's top-right orientation, for the explanation)}], in move order. human: {str(i): {move: p}} of the
    player's level before each corner's move that left the tree; seen: tree keys already proposed to this player."""
    import joseki
    review = {'positions': positions}
    loss = lambda i: _loss(review, i) if str(i) in positions and str(i + 1) in positions else None
    out = []
    for c in corner_walks(game, nodes):
        i, w, m, t = c['left']
        node = nodes[' '.join(c['seq'])]
        kids = [k for k in node.get('next', []) if not k.get('tenuki')]
        if kids and kids[0]['move'] == t:
            continue
        back = lambda mv: coach.gtp(*_turn(joseki.rc(joseki.mirror(mv) if c['diag'] else mv), c['fr'], c['fc']), 19)
        turn = lambda mv: (lambda x: joseki.mirror(x) if c['diag'] else x)(joseki.gtp(*_turn(coach.parse_point(mv, 19), c['fr'], c['fc'])))
        seq_colors = [c['first'] if k % 2 == 0 else coach.OTHER[c['first']] for k in range(len(c['seq']))]
        bad = next((j for j in c['after'] if (loss(j) or 0) >= OPENING_LOSS), None)
        mine = next((k for k in kids if k['move'] == t), None)
        kept = loss(i) is not None and loss(i) < KEEP_LOSS
        item = {'corner': c['name'], 'left': i + 1, 'left_color': w, 'left_move': m,
                'left_loss': None if loss(i) is None else round(loss(i), 1),
                'number': None if bad is None else bad + 1, 'color': None if bad is None else game['moves'][bad][0],
                'move': None if bad is None else game['moves'][bad][1],
                'loss': None if bad is None else round(loss(bad), 1),
                'kept': kept, 'fix': None, 'joseki': [], 'ends': False, 'tenuki': False, 'pick': None,
                'played_rating': mine and mine['rating'], 'known': len(c['seq'])}
        before = [[col, mv] for col, mv in zip(seq_colors, c['seq'])]
        if kept:   # the player's own joseki: its moves, and only the costly one corrected
            if bad is not None:
                item['fix'] = positions[str(bad)][2]
            item['explain'] = {'first': c['first'], 'before': before,
                               'moves': [[game['moves'][j][0], turn(game['moves'][j][1])] for j in c['after']]}
        else:
            prior = (lambda mv: (human or {}).get(str(i), {}).get(back(mv), 0))
            got = None if node.get('settled') else _choose(nodes, ' '.join(c['seq']), prior, seen)
            line, ends, pick = got or ([], True, None)
            colors = [w if k % 2 == 0 else coach.OTHER[w] for k in range(len(line))]
            item.update(joseki=[[col, back(x) if x else 'tenuki'] for col, x in zip(colors, line)], ends=ends,
                        tenuki=not line, pick=pick,
                        explain={'first': c['first'], 'before': before,
                                 'moves': [[col, x or 'tenuki'] for col, x in zip(colors, line)]})
        out.append(item)
    return sorted(out, key=lambda o: o['left'])


def new_joseki_review(game, nodes=None):
    nodes = nodes if nodes is not None else _tree_nodes()
    return {'status': 'running', 'need': joseki_needs(game, nodes), 'positions': {}, 'results': None}


def joseki_step(game, jr, katago, seconds=STEP_SECONDS, nodes=None, levels=None, seen=frozenset()):
    """Up to `seconds` of searches for the joseki review (katago: the CPU's), then its results. levels: {colour:
    level} of the player(s), for the human SL net; seen: the tree keys of joseki already proposed to them."""
    end = time.time() + seconds
    for i in jr['need']:
        if time.time() >= end:
            break
        if str(i) not in jr['positions']:
            jr['positions'][str(i)] = _scan(katago, game, i)
    if all(str(i) in jr['positions'] for i in jr['need']):
        nodes = nodes if nodes is not None else _tree_nodes()
        human = jr.setdefault('human', {})
        for c in corner_walks(game, nodes):
            i, w = c['left'][0], c['left'][1]
            if (levels or {}).get(w) and str(i) not in human:
                if time.time() >= end:
                    return jr
                human[str(i)] = _human(katago, game, i, levels[w]) or {}
        jr.update(status='done', results=joseki_results(game, jr['positions'], nodes, human, seen))
    return jr


def joseki_progress(jr):
    return None if not jr else {'status': jr['status'], 'done': sum(str(i) in jr['positions'] for i in jr['need']),
                                'total': len(jr['need']), 'results': jr.get('results')}


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
                chosen = review.get('level')   # the player's, for both sides (it may be either), else guessed
                review['levels'] = {w: chosen or guess_level(game, review, w) for w in ('B', 'W')}
                review['level_guessed'] = not chosen
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
    return {k: review.get(k) for k in ('status', 'phase', 'total', 'done', 'mistakes', 'levels', 'level_guessed')} | \
        {'deep_done': len(review.get('deep', {})), 'deep_total': deep_total}
