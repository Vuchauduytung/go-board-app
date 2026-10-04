# Board context for the chat: turns a position into facts the LLM can explain without guessing.
# KataGo is the authority on move quality; the LLM only explains its numbers in human terms.
# When a move is explained, KataGo is asked for the 20 best and 20 worst moves that could have been played
# instead, and the 10 best and 10 worst replies after it.

import os
import re

COLS = 'ABCDEFGHJKLMNOPQRST'
# 'Q16', 'd4' — not glued to a preceding letter, so 'nước 1' is not read as C1
MOVE_RE = re.compile(r'(?<![^\W\d_])([A-HJ-Ta-hj-t])(1[0-9]|[1-9])(?!\d)')
POINT_RE = re.compile(r'^([A-HJ-T])(1[0-9]|[1-9])$')
SUGGESTED_RE = re.compile(r'gợi\s*ý\s*(?:số\s*)?(\d{1,2})', re.I)                       # "nước gợi ý 2"
PLAYED_RE = re.compile(r'nước\s+(?:thứ|số)\s*(\d{1,3})', re.I)                          # "nước thứ 5"
LAST_RE = re.compile(r'nước\s+(?:vừa\s+(?:đi|đặt)|cuối(?:\s+cùng)?)|last\s+move', re.I)  # "nước vừa đi"
NAMES = {'B': 'Đen', 'W': 'Trắng'}
OTHER = {'B': 'W', 'W': 'B'}

REPLIES = 10        # best and worst replies listed after an explained move
ALTERNATIVES = 20   # best and worst moves listed instead of an explained move
# Settings of the main search of a position. Queries with the same settings hit the cache, so the root
# analysis, the suggestions button and the first variations query of a position share one KataGo search.
ROOT = {'top': 40, 'ownership': True, 'max_visits': int(os.environ.get('ROOT_VISITS', '400'))}
WORST_VISITS = int(os.environ.get('WORST_VISITS', '200'))


def gtp(r, c, n):
    return f'{COLS[c]}{n - r}'


def parse_point(mv, n):
    """'Q16' -> (row from top, col), None for anything else (pass, off the board)."""
    m = POINT_RE.match(str(mv).strip().upper())
    if not m:
        return None
    r, c = n - int(m.group(2)), COLS.index(m.group(1))
    return (r, c) if 0 <= r < n and c < n else None


def parse_moves(text, n, limit=3):
    """Board coordinates written in the question ("Q16", "d4"), in order, without duplicates."""
    out = []
    for col, row in MOVE_RE.findall(text):
        c, r = COLS.index(col.upper()), n - int(row)
        if c < n and 0 <= r < n and (r, c) not in out:
            out.append((r, c))
    return out[:limit]


def ascii_board(m):
    n = len(m)
    head = '    ' + ' '.join(COLS[:n])
    rows = [f'{n - r:>2}  ' + ' '.join('.XO'[v] for v in m[r]) + f'  {n - r}' for r in range(n)]
    return '\n'.join([head, *rows, head])


def group(m, r, c):
    """Stones of the group at (r, c) and its liberties."""
    n, color = len(m), m[r][c]
    stones, libs, stack = {(r, c)}, set(), [(r, c)]
    while stack:
        y, x = stack.pop()
        for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= yy < n and 0 <= xx < n:
                if m[yy][xx] == 0:
                    libs.add((yy, xx))
                elif m[yy][xx] == color and (yy, xx) not in stones:
                    stones.add((yy, xx))
                    stack.append((yy, xx))
    return stones, libs


def play(m, r, c, color):
    """Board after `color` (1 black, 2 white) plays (r, c), and the captured points."""
    n, nxt, captured = len(m), [row[:] for row in m], []
    nxt[r][c] = color
    for y, x in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
        if 0 <= y < n and 0 <= x < n and nxt[y][x] == 3 - color:
            stones, libs = group(nxt, y, x)
            if not libs:
                for yy, xx in stones:
                    nxt[yy][xx] = 0
                captured += stones
    return nxt, captured


def final_position(matrix, to_play, moves=()):
    """(board, side to move, ko point) once `moves` are played on `matrix`: what a KataGo answer depends on."""
    n, board, nxt, ko = len(matrix), matrix, to_play, None
    for color, mv in moves:
        nxt, ko = OTHER[color], None
        rc = parse_point(mv, n)
        if rc is None or board[rc[0]][rc[1]]:   # pass (KataGo rejects a move on a stone)
            continue
        board, captured = play(board, *rc, 1 if color == 'B' else 2)
        if len(captured) == 1:
            stones, libs = group(board, *rc)
            if len(stones) == 1 and len(libs) == 1:
                ko = captured[0]
    return board, nxt, ko


def quality(loss_points):
    """Label a move by the points it loses against KataGo's best move."""
    if loss_points <= 0.5:
        return 'tốt nhất / ngang nước tốt nhất'
    if loss_points <= 2:
        return 'tốt (chênh nhỏ)'
    if loss_points <= 5:
        return 'kém chính xác'
    if loss_points <= 10:
        return 'sai lầm'
    return 'sai lầm nghiêm trọng'


def zones(own_black, n):
    """Average ownership (+ = Black, - = White) over corners, sides and centre."""
    k = round(n / 3)
    bands = {0: range(0, k), 1: range(k, n - k), 2: range(n - k, n)}
    vname = {0: 'trên', 1: 'giữa', 2: 'dưới'}
    hname = {0: 'trái', 1: 'giữa', 2: 'phải'}
    out = []
    for vi in range(3):
        for hi in range(3):
            cells = [own_black[r][c] for r in bands[vi] for c in bands[hi]]
            avg = sum(cells) / len(cells)
            where = 'Trung tâm' if vi == hi == 1 else \
                f'Góc {vname[vi]}-{hname[hi]}' if vi != 1 and hi != 1 else \
                f'Cạnh {vname[vi]}' if hi == 1 else f'Cạnh {hname[hi]}'
            side = 'Đen' if avg > 0 else 'Trắng'
            label = ('chắc chắn của ' + side) if abs(avg) > 0.6 else \
                ('nghiêng về ' + side) if abs(avg) > 0.25 else 'đang tranh chấp'
            out.append(f'- {where}: {label} ({avg:+.2f})')
    return out


def sequence(pv, first, limit=8):
    """'Đen Q16 → Trắng R14 → …': a KataGo line with the colour of every move."""
    out, color = [], first
    for mv in pv[:limit]:
        out.append(f'{NAMES[color]} {mv}')
        color = OTHER[color]
    return ' → '.join(out)


def _brief(m):
    return {k: m[k] for k in ('move', 'winrate', 'score_lead', 'visits', 'pv')}


def variations(katago, position, count=REPLIES):
    """KataGo's `count` best and `count` worst moves for the side to move in `position`, each with its expected line.
    The best come from the normal search; a second search may not play anything the first one looked at, so
    plausible-but-bad moves get evaluated too, and the worst are the lowest-scoring moves of both searches."""
    first = katago({**position, **ROOT})
    moves = first['moves']
    seen = {m['move'] for m in moves}
    second = katago({**position, 'top': 40, 'ownership': False, 'max_visits': WORST_VISITS,
                     'avoid': sorted(seen), 'wide_root_noise': 0.1}) if moves else {'moves': []}
    rest = moves[count:] + [m for m in second['moves'] if m['move'] not in seen]
    searched = [m for m in rest if m['visits'] >= 2]   # one visit = network guess, no line behind it
    worst = sorted(searched if len(searched) >= count else rest, key=lambda m: m['score_lead'])[:count]
    return {'winrate': first['winrate'], 'score_lead': first['score_lead'], 'visits': first.get('visits'),
            'best': [_brief(m) for m in moves[:count]], 'worst': [_brief(m) for m in worst]}


def explained_moves(question, n, root, played, limit=3):
    """Points the question is about: coordinates ("Q16"), KataGo's suggestion N ("nước gợi ý 2"),
    the user's move N ("nước thứ 5") or their last move ("nước vừa đi")."""
    out = parse_moves(question, n, limit)
    refs = [root['moves'][int(k) - 1]['move'] for k in SUGGESTED_RE.findall(question)
            if 1 <= int(k) <= len(root['moves'])]
    refs += [played[int(k) - 1][1] for k in PLAYED_RE.findall(question) if 1 <= int(k) <= len(played)]
    if played and LAST_RE.search(question):
        refs.append(played[-1][1])
    for mv in refs:
        rc = parse_point(mv, n)
        if rc and rc not in out:
            out.append(rc)
    return out[:limit]


def _line(i, m, who, limit=8):
    return (f'  {i}. {m["move"]}: {NAMES[who]} thắng {m["winrate"]:.0%}, {m["score_lead"]:+.1f} điểm; '
            f'chuỗi {sequence(m["pv"], who, limit)}')


def build_context(matrix, to_play, question, katago, base=None, base_to_play=None, played=(), komi=7.5):
    """Text block with the position and KataGo's verdicts, plus marks for the UI.
    matrix / to_play: position on screen. base / base_to_play / played: the starting position and the
    moves played on it since ([["B", "Q16"], ...]), so questions about a played move are judged before it.
    katago(payload) -> KataGo /analyze response (cached: positions explained before cost nothing)."""
    n = len(matrix)
    me = to_play
    played = [list(p) for p in played]
    current = {'matrix': matrix, 'to_play': to_play, 'komi': komi}
    root = katago({**current, **ROOT})
    best = root['moves'][0] if root['moves'] else None
    lines = [
        'THẾ CỜ HIỆN TẠI (X = Đen, O = Trắng, . = trống; cột A–T bỏ chữ I, hàng 1 ở dưới cùng):',
        ascii_board(matrix),
        f'Đen {sum(v == 1 for row in matrix for v in row)} quân, Trắng {sum(v == 2 for row in matrix for v in row)} quân. '
        f'Tới lượt: {NAMES[me]}. Luật Trung Quốc, komi {komi:g}.',
    ]
    if played:
        lines.append('Các nước người dùng đã đặt từ thế cờ ban đầu (số = số hiện trên quân): ' +
                     ', '.join(f'{i}. {NAMES[c]} {mv}' for i, (c, mv) in enumerate(played, 1)) + '.')
    lines += [
        '',
        f'ĐÁNH GIÁ CỦA KATAGO ({root.get("visits")} lượt tìm kiếm): {NAMES[me]} thắng {root["winrate"]:.0%}, '
        f'{"hơn" if root["score_lead"] >= 0 else "kém"} {abs(root["score_lead"]):.1f} điểm.',
        'Nước tốt nhất cho bên tới lượt (thứ tự gợi ý trên màn hình; chuỗi = diễn biến dự kiến):',
    ]
    for i, mv in enumerate(root['moves'][:5], 1):
        lines.append(_line(i, mv, me))

    marks = []
    asked = explained_moves(question, n, root, played) if best else []
    if asked:
        lines += ['', 'CÁC NƯỚC ĐƯỢC HỎI:']
    for r, c in asked:
        mv = gtp(r, c, n)
        at = max((i for i, (_, m) in enumerate(played) if m == mv), default=None)
        if matrix[r][c] and at is not None and base is not None:
            # a move played on the board: judge it in the position before it was played
            player = played[at][0]
            before = {'matrix': base, 'to_play': base_to_play, 'moves': played[:at], 'komi': komi}
            pre = katago({**before, **ROOT})
            note = f' (nước thứ {at + 1} đã đi, đánh giá tại thế cờ trước nó)'
        elif matrix[r][c]:
            lines.append(f'  {mv}: điểm này đã có quân {NAMES["B" if matrix[r][c] == 1 else "W"]} từ thế cờ ban đầu.')
            continue
        else:
            player, before, pre, note = me, current, root, ''
        if not pre['moves']:
            continue
        ref = pre['moves'][0]
        alts = variations(katago, before, ALTERNATIVES)   # its first search is `pre`, from the cache
        known = {m['move']: m for m in alts['best'] + alts['worst']} | {m['move']: m for m in pre['moves']}
        after = {**before, 'moves': before.get('moves', []) + [[player, mv]]}
        var = variations(katago, after, REPLIES)
        reply = OTHER[player]
        if mv in known:
            wr, lead, pv = known[mv]['winrate'], known[mv]['score_lead'], known[mv]['pv']
        else:
            wr, lead = 1 - var['winrate'], -var['score_lead']
            pv = [mv] + (var['best'][0]['pv'] if var['best'] else [])
        loss = max(ref['score_lead'] - lead, 0)
        q = quality(loss)
        lines.append(f'- {mv}{note}: {NAMES[player]} thắng {wr:.0%}, {lead:+.1f} điểm → mất {loss:.1f} điểm so với '
                     f'nước tốt nhất {ref["move"]} → {q}; diễn biến dự kiến {sequence(pv, player)}')
        lines.append(f'  Thay cho {mv}, {ALTERNATIVES} lựa chọn tốt nhất của {NAMES[player]} ở thế cờ đó '
                     f'(số liệu tính cho {NAMES[player]}):')
        lines += ['  ' + _line(i, m, player, 6) for i, m in enumerate(alts['best'], 1)]
        lines.append(f'  Thay cho {mv}, {ALTERNATIVES} lựa chọn kém nhất của {NAMES[player]} trong các nước KataGo đã xét:')
        lines += ['  ' + _line(i, m, player, 6) for i, m in enumerate(alts['worst'], 1)]
        lines.append(f'  Sau {mv}, {REPLIES} biến tốt nhất cho {NAMES[reply]} (số liệu tính cho {NAMES[reply]}):')
        lines += ['  ' + _line(i, m, reply) for i, m in enumerate(var['best'], 1)]
        lines.append(f'  Sau {mv}, {REPLIES} biến kém nhất cho {NAMES[reply]} trong các nước KataGo đã xét '
                     f'(cách đáp sai và {NAMES[player]} trừng phạt thế nào):')
        lines += ['  ' + _line(i, m, reply) for i, m in enumerate(var['worst'], 1)]
        marks.append({'move': mv, 'row': r, 'col': c, 'loss': round(loss, 1), 'quality': q})

    if root.get('ownership'):
        sign = 1 if me == 'B' else -1
        own_black = [[sign * v for v in row] for row in root['ownership']]
        lines += ['', 'VÙNG ẢNH HƯỞNG THEO KATAGO (+ = Đen, − = Trắng):', *zones(own_black, n)]
    return '\n'.join(lines), marks


ACTION_TYPES = ('play', 'remove', 'back_to', 'to_play', 'restart')


def clean_actions(actions, n):
    """Board edits the model asked for, keeping only well-formed ones; the browser checks legality."""
    out = []
    for a in (actions if isinstance(actions, list) else [])[:10]:
        t = a.get('type') if isinstance(a, dict) else None
        if t == 'play':
            moves = [{'color': m['color'], 'point': str(m['point']).strip().upper()} for m in a.get('moves') or []
                     if isinstance(m, dict) and m.get('color') in NAMES and parse_point(m.get('point', ''), n)]
            if moves:
                out.append({'type': t, 'moves': moves[:60]})
        elif t == 'remove':
            points = [str(p).strip().upper() for p in a.get('points') or [] if parse_point(p, n)]
            if points:
                out.append({'type': t, 'points': points[:n * n]})
        elif t == 'back_to' and isinstance(a.get('move_number'), int) and a['move_number'] >= 0:
            out.append({'type': t, 'move_number': a['move_number']})
        elif t == 'to_play' and a.get('color') in NAMES:
            out.append({'type': t, 'color': a['color']})
        elif t == 'restart':
            out.append({'type': t})
    return out


VAR_RE = re.compile(r'\[\s*var\s*(\d+)\s*\]|\bvar\s?(\d+)\b', re.I)


def clean_variations(variations, n, played, first, answer):
    """Variations named in a coach answer, renamed Var<first>, Var<first + 1>… so names stay unique in a session,
    each with `start`: the entries of `played` (the board's [{r, c, color}]) its first move is played after.
    -> (variations, answer with [VarN] rewritten to the new names); the browser checks legality."""
    out, names = [], {}
    for v in (variations if isinstance(variations, list) else [])[:20]:
        if not isinstance(v, dict):
            continue
        moves = [{'color': m['color'], 'point': str(m['point']).strip().upper()} for m in v.get('moves') or []
                 if isinstance(m, dict) and m.get('color') in NAMES and parse_point(m.get('point', ''), n)][:30]
        local = VAR_RE.fullmatch(str(v.get('name') or '').strip())
        if not moves or not local or (local.group(1) or local.group(2)) in names:
            continue
        keep = v.get('from_move')
        start = list(played)
        if isinstance(keep, int) and keep >= 0:
            k, cut = 0, len(played)
            for i, p in enumerate(played):   # like back_to: drop from the (keep + 1)th placed stone on
                if p.get('color') and (k := k + 1) > keep:
                    cut = i
                    break
            start = start[:cut]
        name = f'Var{first + len(out)}'
        names[local.group(1) or local.group(2)] = name
        out.append({'name': name, 'title': str(v.get('title') or '').strip()[:80], 'start': start, 'moves': moves})

    def rename(m):   # earlier variations of the session keep their names
        k = m.group(1) or m.group(2)
        new = names.get(k) or (f'Var{k}' if 0 < int(k) < first else None)
        return f'[{new}]' if new else m.group(0).strip('[] ')
    return out, VAR_RE.sub(rename, answer)
