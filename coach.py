# Board context for the chat: turns a position into facts the LLM can explain without guessing.
# KataGo is the authority on move quality; the LLM only explains its numbers in human terms.

import re

COLS = 'ABCDEFGHJKLMNOPQRST'
# 'Q16', 'd4' — not glued to a preceding letter, so 'nước 1' is not read as C1
MOVE_RE = re.compile(r'(?<![^\W\d_])([A-HJ-Ta-hj-t])(1[0-9]|[1-9])(?!\d)')
NAMES = {'B': 'Đen', 'W': 'Trắng'}


def gtp(r, c, n):
    return f'{COLS[c]}{n - r}'


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


def _evaluate(katago, position, player, mv, known):
    """Winrate / score for `player` after `mv` in `position` (a KataGo payload where `player` is to move).
    Uses the root search's numbers when the move was explored, otherwise plays it and searches the reply."""
    if mv in known:
        return known[mv]['winrate'], known[mv]['score_lead'], known[mv]['pv']
    after = katago({**position, 'moves': position.get('moves', []) + [[player, mv]], 'max_visits': 80, 'top': 1,
                    'ownership': False})
    pv = [mv] + (after['moves'][0]['pv'] if after['moves'] else [])
    return 1 - after['winrate'], -after['score_lead'], pv


def build_context(matrix, to_play, question, katago, base=None, base_to_play=None, played=()):
    """Text block with the position and KataGo's verdicts, plus marks for the UI.
    matrix / to_play: position on screen. base / base_to_play / played: the photographed position and the
    moves tried on it since ([["B", "Q16"], ...]), so questions about a played move are judged before it.
    katago(payload) -> KataGo /analyze response."""
    n = len(matrix)
    me = to_play
    played = [list(p) for p in played]
    current = {'matrix': matrix, 'to_play': to_play}
    root = katago({**current, 'top': 40, 'ownership': True})
    best = root['moves'][0] if root['moves'] else None
    lines = [
        'THẾ CỜ HIỆN TẠI (X = Đen, O = Trắng, . = trống; cột A–T bỏ chữ I, hàng 1 ở dưới cùng):',
        ascii_board(matrix),
        f'Đen {sum(v == 1 for row in matrix for v in row)} quân, Trắng {sum(v == 2 for row in matrix for v in row)} quân. '
        f'Tới lượt: {NAMES[me]}. Luật Trung Quốc, komi 7.5.',
    ]
    if played:
        lines.append('Các nước người dùng đã đi thử từ thế cờ trong ảnh: ' +
                     ', '.join(f'{i}. {NAMES[c]} {mv}' for i, (c, mv) in enumerate(played, 1)) + '.')
    lines += [
        '',
        f'ĐÁNH GIÁ CỦA KATAGO ({root.get("visits")} lượt tìm kiếm): {NAMES[me]} thắng {root["winrate"]:.0%}, '
        f'{"hơn" if root["score_lead"] >= 0 else "kém"} {abs(root["score_lead"]):.1f} điểm.',
        'Nước tốt nhất cho bên tới lượt (thứ tự gợi ý trên màn hình; chuỗi = diễn biến dự kiến):',
    ]
    for i, mv in enumerate(root['moves'][:5], 1):
        lines.append(f'  {i}. {mv["move"]}: {NAMES[me]} thắng {mv["winrate"]:.0%}, {mv["score_lead"]:+.1f} điểm; '
                     f'chuỗi {" ".join(mv["pv"][:6])}')

    marks = []
    asked = parse_moves(question, n)
    if asked and best:
        lines += ['', 'CÁC NƯỚC NGƯỜI DÙNG HỎI:']
        known = {m['move']: m for m in root['moves']}
        for r, c in asked:
            mv = gtp(r, c, n)
            at = max((i for i, (_, m) in enumerate(played) if m == mv), default=None)
            if matrix[r][c] and at is not None and base is not None:
                # a move tried on the board: judge it in the position before it was played
                player = played[at][0]
                before = {'matrix': base, 'to_play': base_to_play, 'moves': played[:at]}
                pre = katago({**before, 'top': 40})
                if not pre['moves']:
                    continue
                pbest = pre['moves'][0]
                wr, lead, pv = _evaluate(katago, before, player, mv, {m['move']: m for m in pre['moves']})
                ref, who, note = pbest, player, f' (nước thứ {at + 1} đã đi, đánh giá tại thế cờ trước nó)'
            elif matrix[r][c]:
                lines.append(f'  {mv}: điểm này đã có quân {NAMES["B" if matrix[r][c] == 1 else "W"]} từ thế cờ ban đầu.')
                continue
            else:
                wr, lead, pv = _evaluate(katago, current, me, mv, known)
                ref, who, note = best, me, ''
            loss = max(ref['score_lead'] - lead, 0)
            q = quality(loss)
            lines.append(f'  {mv}{note}: {NAMES[who]} thắng {wr:.0%}, {lead:+.1f} điểm → mất {loss:.1f} điểm so với '
                         f'nước tốt nhất {ref["move"]} → {q}; diễn biến dự kiến {" ".join(pv[:6])}')
            marks.append({'move': mv, 'row': r, 'col': c, 'loss': round(loss, 1), 'quality': q})

    if root.get('ownership'):
        sign = 1 if me == 'B' else -1
        own_black = [[sign * v for v in row] for row in root['ownership']]
        lines += ['', 'VÙNG ẢNH HƯỞNG THEO KATAGO (+ = Đen, − = Trắng):', *zones(own_black, n)]
    return '\n'.join(lines), marks
