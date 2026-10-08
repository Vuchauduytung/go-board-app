"""KataGo's joseki: a tree of corner sequences built by KataGo itself, browsed in the app's 📘 Định thức tab.

The corner is the top-right 11×11 (columns J–T, rows 19–9) of an otherwise empty 19×19 board, komi 7.5. Each position
is searched twice: on the whole board (the value of tenuki, the best move elsewhere), and with the first move kept in
the corner (avoid every other point), so that every corner move gets enough visits to be judged: on an empty board
KataGo spends nearly all of a whole-board search on the empty corners. A position keeps KataGo's best corner move and
those losing at most BRANCH points more, up to WIDTH, each rated best / good / playable, with its loss next to the
whole board's best. Past the first FREE moves the joseki ends when tenuki is the best move on the board and every
corner move loses SETTLED points or more next to it: nothing in the corner is urgent any more. Lines closest to the
best corner move are grown first, so the main lines reach their end within the budget. Moves mirrored across the corner's diagonal from a symmetric position are kept once.

    python -m joseki build [--nodes 2500] [--visits 500] [--url http://katago:8080]   # writes JOSEKI_PATH
    python -m joseki deepen --url http://KATAGO [--max-depth 50]   # grow the built tree, see deepen()
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


# ---- deepening: the lines go on after the joseki, up to DEEP_DEPTH moves. Where the joseki ended the side to move
# plays its best move elsewhere (a real stone: a "tenuki" branch), then the other side answers in the corner or
# tenukis too; two tenukis in a row: the corner is finished ("done"). Each line of the built tree goes on as one line
# (the best move; no new branches inside the built tree either): the complete joseki first (lines that reached their
# end), then the lines the build left unfinished, each class best-first, and each line to its end before the next
# (depth first), so finished lines show up early. Meant for a slow CPU KataGo: one query at a time, saved every few
# positions so the app shows the deeper tree as it grows. ----
DEEP_DEPTH = 50
DEEP_WIDTH, DEEP_BRANCH, DEEP_SINGLE = 1, 0.5, 30
# The first moves are widened once before deepening: up to WIDE_WIDTH corner moves within WIDE_BRANCH points of the
# best one, so that the approaches and answers people play (low / high approach, pincers…) are in the tree even when
# KataGo prefers the 3-3 invasion on an empty board by more than BRANCH; each new branch is then deepened as a line.
WIDE_DEPTH, WIDE_BRANCH, WIDE_WIDTH = 3, 3.0, 6


def widen(katago, tree, visits=300, log=print):
    """Add the wider first-move branches (marked "wide") to the built tree, once."""
    nodes = tree['nodes']
    todo = sorted((k for k, n in nodes.items() if len(k.split()) <= WIDE_DEPTH and not n.get('settled')),
                  key=lambda k: len(k.split()))
    for done, key in enumerate(todo, 1):
        moves = key.split()
        res, corner_res = search_deep(katago, moves, visits, 60)
        whole, corner = _trusted(res), sorted((m for m in _trusted(corner_res) if local(m['move'])),
                                              key=lambda m: -m['score_lead'])
        if not corner:
            continue
        best = max(m['score_lead'] for m in whole + corner)
        node, sym = nodes[key], symmetric(moves)
        have = {k['move'] for k in node['next']}
        for m in corner:
            d = corner[0]['score_lead'] - m['score_lead']
            if d > WIDE_BRANCH or len(node['next']) >= WIDE_WIDTH:
                break
            if m['move'] in have or (sym and mirror(m['move']) in have):
                continue
            node['next'].append({'move': m['move'], 'loss': round(best - m['score_lead'], 2), 'delta': round(d, 2),
                                 'rating': 'best' if d <= 0.5 else 'good' if d <= 1.0 else 'ok', 'wide': True,
                                 'visits': m['visits'], 'winrate': m['winrate'], 'score_lead': m['score_lead']})
            have.add(m['move'])
        if done % 10 == 0:
            log(f'widened {done}/{len(todo)} first-move positions')
    tree['widened'] = True


def http_katago(url):
    import urllib.request

    def run(payload):
        req = urllib.request.Request(url.rstrip('/') + '/analyze', json.dumps(payload).encode(),
                                     {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())
    return run


def _expand(res, corner_res, moves, built_depth):
    """Children of a position being deepened: the built tree's rule, narrower past built_depth, and the tenuki."""
    kids, tenuki, settled = branches(res, moves, corner_res)
    width = 1 if len(moves) >= DEEP_SINGLE else DEEP_WIDTH
    kids = [k for k in kids if k['delta'] <= DEEP_BRANCH][:width]
    if settled:
        away = [m for m in _trusted(res) if not local(m['move'])]
        if away and not local(moves[-1]):   # the other side has just tenukied too: the corner is finished
            return [], tenuki, True, True
        if away:
            best = max(away, key=lambda m: m['score_lead'])
            kids = [{'move': best['move'], 'tenuki': True, 'loss': tenuki or 0.0, 'delta': 0.0, 'rating': 'best',
                     'visits': best['visits'], 'winrate': best['winrate'], 'score_lead': best['score_lead']}]
    return kids, tenuki, settled, False


def deepen(katago, tree, max_depth=DEEP_DEPTH, visits=200, whole_visits=60, save=None, save_every=25,
           hours=None, log=print):
    """Grow `tree` in place, best-first by how far each line is from the best moves, until every line reached
    max_depth or ended (or `hours` passed). save(tree) is called every save_every positions and at the end."""
    nodes = tree['nodes']
    built = max(len(k.split()) for k in nodes)
    stop = time.time() + hours * 3600 if hours else None

    def cost(key):
        c, parts = 0.0, key.split()
        for i in range(1, len(parts)):
            parent = nodes.get(' '.join(parts[:i]), {})
            k = next((x for x in parent.get('next', []) if x['move'] == parts[i]), None)
            c += 2 * (k['delta'] if k else 0.5) + 0.1
        return c

    heap, counter = [], 0
    for key, node in nodes.items():
        depth = len(key.split())
        if depth >= max_depth or node.get('done'):
            continue
        if depth <= WIDE_DEPTH:                                     # the widened first moves: every branch is a line
            for k in node.get('next', [])[1:]:
                if not k.get('tenuki') and f'{key} {k["move"]}' not in nodes:
                    heap.append(((0, cost(key) + 2 * k['delta'] + 0.1, -depth - 1), counter := counter + 1,
                                 key.split() + [k['move']], False))
        if node.get('settled') and not node.get('next'):            # a complete joseki: on with its tenuki
            heap.append(((0, cost(key), -len(key.split())), counter := counter + 1, key.split(), True))
        nxt = node.get('next', [])
        if nxt and not node.get('settled') and f'{key} {nxt[0]["move"]}' not in nodes:   # unfinished: its best move
            heap.append(((1, cost(key) + 2 * nxt[0]['delta'] + 0.1, -len(key.split()) - 1), counter := counter + 1, key.split() + [nxt[0]['move']], False))
        if nxt and node.get('settled') and nxt[0].get('tenuki') and f'{key} {nxt[0]["move"]}' not in nodes:   # resumed
            heap.append(((0, cost(key) + 0.1, -len(key.split()) - 1), counter := counter + 1, key.split() + [nxt[0]['move']], False))
    heapq.heapify(heap)
    done, start = 0, time.time()
    while heap and (stop is None or time.time() < stop):
        (cls, c, _), _, moves, again = heapq.heappop(heap)   # c: the line's own rank, kept by its moves
        key = ' '.join(moves)
        if key in nodes and not again:
            continue
        res, corner_res = search_deep(katago, moves, visits, whole_visits)
        kids, tenuki, settled, finished = _expand(res, corner_res, moves, built)
        nodes[key] = {**nodes.get(key, {}), 'winrate': res['winrate'], 'score_lead': res['score_lead'], 'tenuki': tenuki,
                      'settled': settled, 'next': kids, **({'done': True} if finished else {})}
        if len(moves) < max_depth:
            for k in kids:
                heapq.heappush(heap, ((cls, c, -len(moves) - 1), counter := counter + 1, moves + [k['move']], False))
        done += 1
        if done % save_every == 0:
            _mark_searched(nodes)
            if save:
                save(tree)
            log(f'+{done} positions ({len(nodes)} in all, {len(heap)} waiting, deepest {max(len(k.split()) for k in nodes)}), '
                f'{(time.time() - start) / done:.1f} s each')
    _mark_searched(nodes)
    tree['deepened_at'] = time.strftime('%Y-%m-%d')
    tree['max_depth'] = max_depth
    if save:
        save(tree)
    return tree


def search_deep(katago, moves, visits, whole_visits):
    """As search(), with its own visits for the whole board (only the tenuki is needed from it)."""
    colored = [['B' if i % 2 == 0 else 'W', m] for i, m in enumerate(moves)]
    base = {'matrix': [[0] * N for _ in range(N)], 'to_play': 'B', 'moves': colored, 'komi': 7.5, 'ownership': False}
    taken = set(moves)
    return (katago({**base, 'top': 5, 'max_visits': whole_visits}),
            katago({**base, 'top': 20, 'max_visits': visits, 'avoid': [p for p in OUTSIDE if p not in taken]}))


def _mark_searched(nodes):
    for key, node in nodes.items():
        for k in node.get('next', []):
            k['searched'] = f'{key} {k["move"]}' in nodes


def save_tree(tree, path=None):
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(tree, separators=(',', ':')))
    tmp.replace(path)


def load():
    for f in (PATH, BUNDLED):
        if f.is_file():
            return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build', 'deepen'])
    ap.add_argument('--nodes', type=int, default=2500)
    ap.add_argument('--visits', type=int, default=500)
    ap.add_argument('--url', default=os.environ.get('KATAGO_CPU_URL', 'http://katago:8080'),
                    help='a KataGo service (katago_server.py): everything about joseki runs on the VM\'s CPU one')
    ap.add_argument('--max-depth', type=int, default=DEEP_DEPTH)
    ap.add_argument('--hours', type=float)
    args = ap.parse_args()
    if args.cmd == 'deepen':
        tree = json.loads(load().read_text())
        if not tree.get('widened'):
            widen(http_katago(args.url), tree, log=lambda s: print(s, file=sys.stderr, flush=True))
            save_tree(tree)
        deepen(http_katago(args.url), tree, args.max_depth, visits=min(args.visits, 300), save=save_tree,
               hours=args.hours, log=lambda s: print(s, file=sys.stderr, flush=True))
        print(f'{len(tree["nodes"])} positions -> {PATH}')
        return
    tree = build(http_katago(args.url), args.nodes, args.visits, parallel=1,
                 log=lambda s: print(s, file=sys.stderr, flush=True))
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(tree, separators=(',', ':')))
    print(f'{len(tree["nodes"])} positions -> {PATH}')


if __name__ == '__main__':
    main()
