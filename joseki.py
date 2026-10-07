"""KataGo's joseki: a tree of corner sequences built by KataGo itself, browsed in the app's 📘 Định thức tab.

The corner is the top-right 11×11 (columns J–T, rows 19–9) of an otherwise empty 19×19 board, komi 7.5. Each position
is searched twice: on the whole board (the value of tenuki, the best move elsewhere), and with the first move kept in
the corner (avoid every other point), so that every corner move gets enough visits to be judged: on an empty board
KataGo spends nearly all of a whole-board search on the empty corners. A position keeps KataGo's best corner move and
those losing at most BRANCH points more, up to WIDTH, each rated best / good / playable, with its loss next to the
whole board's best. Past the first FREE moves the joseki ends when tenuki is the best move on the board and every
corner move loses SETTLED points or more next to it: nothing in the corner is urgent any more. Lines closest to the
best corner move are grown first, so the main lines reach their end within the budget. Moves mirrored across the corner's diagonal from a symmetric position are kept once.

    python -m joseki build [--nodes 2500] [--visits 500]   # on the server: KataGo searches, writes JOSEKI_PATH
"""
import argparse
import heapq
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

N = 19
COLS = 'ABCDEFGHJKLMNOPQRST'
REGION = 11                       # rows 0..10 from the top, columns 8..18
STARTS = ['Q16', 'R16', 'R17', 'Q15', 'R15']   # 4-4, 3-4, 3-3, 4-5, 3-5 (their mirrors are the same joseki)
BRANCH = 1.5                      # points a branch may lose next to the best corner move
WIDTH = 4                         # branches per position
SETTLED = 0.2                     # tenuki best and this much better than any corner move: the joseki is over
TENUKI_BEST = 0.1                 # tenuki within this of the best move counts as the best
MIN_SHARE = 0.03                  # visits a candidate needs (share of the top move's) to be trusted
FREE = 3                          # the first moves (start, approach or invasion, answer) never end the joseki
MAX_DEPTH = 24
PATH = Path(os.environ.get('JOSEKI_PATH', Path(os.environ.get('DATA_DIR', '/data')).parent / 'joseki' / 'tree.json'))
BUNDLED = Path(__file__).parent / 'josekidb' / 'tree.json'   # a built tree shipped with the code


def rc(mv):
    return N - int(mv[1:]), COLS.index(mv[0])


def gtp(r, c):
    return f'{COLS[c]}{N - r}'


def local(mv):
    if mv.lower() == 'pass':
        return False
    r, c = rc(mv)
    return r < REGION and c >= N - REGION


def mirror(mv):
    """Across the top-right corner's diagonal (T19–K10)."""
    r, c = rc(mv)
    return gtp(N - 1 - c, N - 1 - r)


def symmetric(moves):
    return sorted(moves) == sorted(mirror(m) for m in moves)


OUTSIDE = [gtp(r, c) for r in range(N) for c in range(N) if not (r < REGION and c >= N - REGION)]


def search(katago, moves, visits):
    """-> (whole-board answer, answer with the first move kept in the corner)."""
    colored = [['B' if i % 2 == 0 else 'W', m] for i, m in enumerate(moves)]
    base = {'matrix': [[0] * N for _ in range(N)], 'to_play': 'B', 'moves': colored, 'komi': 7.5, 'ownership': False}
    return (katago({**base, 'top': 5, 'max_visits': max(100, visits // 2)}),
            katago({**base, 'top': 20, 'max_visits': visits, 'avoid': OUTSIDE}))


def _trusted(res):
    cands = [m for m in res['moves'] if m['move'].lower() != 'pass']
    if not cands:
        return []
    top = max(m['visits'] for m in cands)
    return [m for m in cands if m['visits'] >= max(3, MIN_SHARE * top)] or cands[:1]


def branches(res, moves, corner_res=None):
    """res: whole-board answer, corner_res: the corner-only one (default: res's corner moves) -> (children [{move, loss,
    rating, visits, winrate, score_lead}], tenuki loss or None, settled)."""
    whole = _trusted(res)
    corner = sorted((m for m in _trusted(corner_res or res) if local(m['move'])), key=lambda m: -m['score_lead'])
    if not whole and not corner:
        return [], None, True
    best = max(m['score_lead'] for m in whole + corner)
    away = [m for m in whole if not local(m['move'])]
    tenuki = round(best - max(m['score_lead'] for m in away), 2) if away else None
    if not corner or (len(moves) >= FREE and tenuki is not None and tenuki <= TENUKI_BEST
                      and best - corner[0]['score_lead'] >= tenuki + SETTLED):
        return [], tenuki, True
    first = corner[0]['score_lead']
    sym = symmetric(moves)
    out = []
    for m in corner:
        d = first - m['score_lead']
        if d > BRANCH or len(out) >= WIDTH:
            break
        if sym and any(mirror(m['move']) == o['move'] for o in out):
            continue
        out.append({'move': m['move'], 'loss': round(best - m['score_lead'], 2), 'delta': round(d, 2),
                    'rating': 'best' if d <= 0.5 else 'good' if d <= 1.0 else 'ok',
                    'visits': m['visits'], 'winrate': m['winrate'], 'score_lead': m['score_lead']})
    return out, tenuki, False


def build(katago, max_nodes=2500, visits=500, parallel=8, log=print):
    """Best-first: the lines losing least are grown first, until max_nodes positions are searched."""
    nodes, seen = {}, 0
    heap = [(0.0, i, [m]) for i, m in enumerate(STARTS)]
    heapq.heapify(heap)
    counter = len(STARTS)
    start = time.time()
    with ThreadPoolExecutor(parallel) as pool:
        while heap and len(nodes) < max_nodes:
            batch = [heapq.heappop(heap) for _ in range(min(parallel, len(heap), max_nodes - len(nodes)))]
            for (cost, _, moves), (res, corner_res) in zip(batch, pool.map(lambda b: search(katago, b[2], visits), batch)):
                kids, tenuki, settled = branches(res, moves, corner_res)
                key = ' '.join(moves)
                nodes[key] = {'winrate': res['winrate'], 'score_lead': res['score_lead'], 'tenuki': tenuki,
                              'settled': settled, 'next': kids}
                if len(moves) < MAX_DEPTH:
                    for k in kids:
                        counter += 1
                        heapq.heappush(heap, (cost + 2 * k['delta'] + 0.1, counter, moves + [k['move']]))
            seen += len(batch)
            if seen % 40 < parallel:
                log(f'{len(nodes)} positions, {len(heap)} waiting, {time.time() - start:.0f}s')
    for key, node in nodes.items():   # branches never searched (out of budget) stay as leaves without data
        for k in node['next']:
            k['searched'] = f'{key} {k["move"]}' in nodes
    return {'version': 1, 'built_at': time.strftime('%Y-%m-%d'), 'visits': visits, 'komi': 7.5,
            'region': 'J19-T9', 'starts': STARTS, 'nodes': nodes}


def load():
    for f in (PATH, BUNDLED):
        if f.is_file():
            return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--nodes', type=int, default=2500)
    ap.add_argument('--visits', type=int, default=500)
    args = ap.parse_args()
    from app import _katago
    tree = build(_katago, args.nodes, args.visits, log=lambda s: print(s, file=sys.stderr, flush=True))
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(tree, separators=(',', ':')))
    print(f'{len(tree["nodes"])} positions -> {PATH}')


if __name__ == '__main__':
    main()
