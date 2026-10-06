# Counting a finished game (area scoring, as the Chinese rules the games are played under): stones still alive plus
# the empty points they surround, White adds komi. Dead stones come from KataGo's ownership (a group mostly owned by
# the other side), and the player can flip any group dead / alive; the dead are taken off before counting, so their
# points go to the side around them. An empty region touching only one colour is that colour's; one touching both
# (an unfinished border) goes point by point to whoever KataGo says owns it, else it is neutral (dame).

DEAD_BELOW = -0.3   # a group's mean ownership, from its own side: below this it is dead
OWNED = 0.5         # an empty point of a mixed region counts for the side owning it this much


def _region(m, r, c):
    """Points of the connected region of (r, c)'s value, and the values found next to it."""
    n, v = len(m), m[r][c]
    seen, stack, edge = {(r, c)}, [(r, c)], set()
    while stack:
        y, x = stack.pop()
        for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= yy < n and 0 <= xx < n:
                if m[yy][xx] == v:
                    if (yy, xx) not in seen:
                        seen.add((yy, xx))
                        stack.append((yy, xx))
                else:
                    edge.add(m[yy][xx])
    return seen, edge


def black_view(ownership, side_to_move):
    """KataGo's ownership is for the side to move, which is the opposite of the last move's colour, not the
    to_play the KataGo service echoes (the first mover: wrong after an odd number of moves or after a pass)."""
    sign = 1 if side_to_move == 'B' else -1
    return [[sign * v for v in row] for row in ownership]


def count(board, own_black, komi, toggles=()):
    """board: rows of 0 / 1 black / 2 white; own_black: KataGo ownership, + for Black; toggles: points of groups whose
    life is flipped from KataGo's. -> {dead, territory: {B, W}, dame, black, white, lead (Black's, komi in)}."""
    n = len(board)
    groups, done = [], set()
    for r in range(n):
        for c in range(n):
            if board[r][c] and (r, c) not in done:
                stones, _ = _region(board, r, c)
                done |= stones
                sign = 1 if board[r][c] == 1 else -1
                mean = sum(own_black[y][x] for y, x in stones) * sign / len(stones)
                groups.append({'stones': stones, 'dead': mean < DEAD_BELOW})
    for r, c in toggles:
        for g in groups:
            if (r, c) in g['stones']:
                g['dead'] = not g['dead']
    dead = {p for g in groups if g['dead'] for p in g['stones']}
    m = [[0 if (r, c) in dead else board[r][c] for c in range(n)] for r in range(n)]
    terr, dame, seen = {1: set(), 2: set()}, set(), set()
    for r in range(n):
        for c in range(n):
            if m[r][c] or (r, c) in seen:
                continue
            pts, edge = _region(m, r, c)
            seen |= pts
            if len(edge) == 1:
                terr[edge.pop()] |= pts
                continue
            for y, x in pts:   # touches both colours (or none): the points KataGo is sure of
                o = own_black[y][x]
                (terr[1] if o > OWNED else terr[2] if o < -OWNED else dame).add((y, x))
    stones = {v: sum(1 for r in range(n) for c in range(n) if m[r][c] == v) for v in (1, 2)}
    black, white = stones[1] + len(terr[1]), stones[2] + len(terr[2]) + komi
    pts = lambda s: sorted([r, c] for r, c in s)
    return {'dead': pts(dead), 'territory': {'B': pts(terr[1]), 'W': pts(terr[2])}, 'dame': pts(dame),
            'black': {'stones': stones[1], 'territory': len(terr[1]), 'total': black},
            'white': {'stones': stones[2], 'territory': len(terr[2]), 'komi': komi, 'total': white},
            'lead': black - white}
