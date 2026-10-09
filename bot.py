# Playing against the AI: KataGo searches the position, then a move is drawn among its candidates so that the moves
# lose, on average, as many points as a player of the chosen level does. Each candidate's loss is its score next to
# the best move's; candidates are weighted exp(-loss / T), with T set per position so the expected loss is the
# level's (a quiet position lets any reasonable move through, a life-and-death one makes a weak level err now and
# then). A move losing more than the level's cap is never chosen.
#
# Calibrate here: LEVELS[level] = (average points lost per move, most points a single move may lose), or without a
# code change in the environment: BOT_LEVELS='{"1k": [0.8, 7], …}' (levels given there replace these).
# The app shows the AI's and the player's average loss per move during a game, to compare with the table.
# A barely searched candidate usually loses more than its score says (KataGo looked at it once or twice because it
# thought little of it): each candidate's loss gets UNSURE / sqrt(visits) more than the best move's, so such moves
# are drawn as the mediocre moves they are. Without it every level played several stones weaker than its name
# (first table, 1k ≈ 3–5k); leaving them out instead made every level play like a pro.

import json
import math
import os
import random

import coach

# Third table (2026-10-09): with the second one 5k played like a real 1k, so 1.5 points a move is 1k now, and the
# kyu levels are further apart.
LEVELS = {
    '5k': (2.5, 18), '4k': (2.25, 16), '3k': (2.0, 14), '2k': (1.75, 12), '1k': (1.5, 10),
    '1d': (1.3, 8), '2d': (1.1, 6.5), '3d': (0.9, 5), '4d': (0.7, 4), '5d': (0.5, 3),
}
LEVELS.update({k: tuple(v) for k, v in json.loads(os.environ.get('BOT_LEVELS') or '{}').items()})
UNSURE = 1.5        # points added to the loss of a move searched once (less for more visits, see above)
# Games against the AI run on the VM's CPU (app.KATAGO_CPU_URL): fewer visits than the GPU's full search, so that a
# move takes ~5.5 s on the 2 ARM cores (18 visits/s). The review also reads these answers (review._scan).
SEARCH = {'top': 40, 'ownership': False, 'max_visits': int(os.environ.get('PLAY_VISITS', '100')), 'engine': 'cpu'}
RELIABLE = 0.1      # the best move is taken among candidates with at least this share of the top visits
PASS_WHEN_DONE = 0.3     # the AI passes when passing loses at most this (nothing left worth a move)…
PASS_AFTER_PASS = 1.0    # …or this much once the player has passed (filling its own area gains nothing)
RESIGN_WINRATE = 0.02
RESIGN_AFTER = 80   # moves


def _temperature(losses, target):
    """T for which the exp(-loss / T) weighted mean loss is `target` (or as near as the candidates allow)."""
    def mean(t):
        w = [math.exp(-l / t) for l in losses]
        return sum(x * l for x, l in zip(w, losses)) / sum(w)
    lo, hi = 0.01, 100.0
    if mean(hi) <= target:
        return hi
    if mean(lo) >= target:
        return lo
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if mean(mid) < target else (lo, mid)
    return math.sqrt(lo * hi)


def best(res):
    """The best candidate: highest score among those searched enough to trust their score."""
    top = max(m['visits'] for m in res['moves'])
    return max((m for m in res['moves'] if m['visits'] >= RELIABLE * top), key=lambda m: m['score_lead'])


def choose(res, level, rnd=random, opponent_passed=False):
    """A move for the side to move from a KataGo answer -> {move, loss, alternatives (the other allowed candidates,
    least loss first, for when the chosen one turns out illegal), best, best_lead, score_lead, winrate, resign}."""
    target, cap = LEVELS[level]
    moves = res['moves']
    if not moves:
        return {'move': 'pass', 'loss': 0.0, 'alternatives': [], 'best': None, 'best_lead': None,
                'score_lead': res['score_lead'], 'winrate': res['winrate'], 'resign': False}
    best_move = best(res)
    passing = next((m for m in moves if m['move'].lower() == 'pass'), None)
    if passing and best_move['score_lead'] - passing['score_lead'] <= (PASS_AFTER_PASS if opponent_passed else PASS_WHEN_DONE):
        return {'move': 'pass', 'loss': round(max(0.0, best_move['score_lead'] - passing['score_lead']), 2),
                'chosen_lead': passing['score_lead'], 'alternatives': [], 'best': best_move['move'],
                'best_lead': best_move['score_lead'], 'score_lead': res['score_lead'], 'winrate': res['winrate'],
                'resign': False}
    moves = [m for m in moves if m['move'].lower() != 'pass'] or moves   # never a random pass in the middle
    sure = UNSURE / math.sqrt(best_move['visits'])
    cands = sorted(((max(0.0, best_move['score_lead'] - m['score_lead'] + UNSURE / math.sqrt(max(1, m['visits'])) - sure), m)
                    for m in moves), key=lambda x: x[0])
    allowed = [(l, m) for l, m in cands if l <= cap] or cands[:1]
    t = _temperature([l for l, _ in allowed], target)
    pick = rnd.choices(range(len(allowed)), weights=[math.exp(-l / t) for l, _ in allowed])[0]
    loss, chosen = allowed[pick]
    return {'move': chosen['move'], 'loss': round(loss, 2), 'chosen_lead': chosen['score_lead'],
            'alternatives': [m['move'] for _, m in allowed if m is not chosen],
            'best': best_move['move'], 'best_lead': best_move['score_lead'], 'score_lead': res['score_lead'],
            'winrate': res['winrate'], 'resign': False}


def should_resign(res, move_number):
    return move_number >= RESIGN_AFTER and res['winrate'] < RESIGN_WINRATE


# ---- the kyu levels (1k–5k) play joseki in the opening, as players of that strength learn them: an empty corner gets a
# joseki starting point, a corner whose moves are still in KataGo's joseki tree (joseki.py) gets one of the tree's
# branches (best ones more often); once the corner leaves the tree, or the joseki is over, the level's usual choice
# takes over. The dan levels always choose by KataGo. ----
JOSEKI_UNTIL = 40                                    # moves of the opening
START_WEIGHTS = {'Q16': 4, 'R16': 4, 'R17': 1, 'Q15': 1, 'R15': 1}   # 4-4 and 3-4 mostly
RATING_WEIGHTS = {'best': 4, 'good': 2, 'ok': 1}
EMPTY_CORNER_UNTIL = 12                              # empty corners are taken during the first moves only


def _joseki_nodes():
    """KataGo's joseki tree, read again only when its file changes (it grows while being deepened)."""
    import joseki
    f = joseki.load()
    if f is None:
        return None
    stamp = f.stat().st_mtime
    if _joseki_cache.get('stamp') != stamp:
        _joseki_cache.update(stamp=stamp, nodes=json.loads(f.read_text())['nodes'])
    return _joseki_cache['nodes']


_joseki_cache = {}


def joseki_move(moves, rnd=random, nodes=None):
    """A joseki move for the side to move after `moves` ([["B", "Q16" | "pass"]…] on an empty 19×19), or None."""
    import joseki
    import review
    if len(moves) >= JOSEKI_UNTIL:
        return None
    nodes = nodes if nodes is not None else _joseki_nodes()
    if not nodes:
        return None
    board, _, _ = coach.final_position([[0] * 19 for _ in range(19)], 'B', moves)
    placed = [(w, m) for w, m in moves if m != 'pass']
    empty = lambda mv: not board[coach.parse_point(mv, 19)[0]][coach.parse_point(mv, 19)[1]]

    def pick(weighted):
        weighted = [(mv, w) for mv, w in weighted if empty(mv)]
        return rnd.choices([mv for mv, _ in weighted], weights=[w for _, w in weighted])[0] if weighted else None

    me = 'W' if moves and moves[-1][0] == 'B' else 'B'
    quarter = lambda m: review._quarter(coach.parse_point(m, 19))
    free = [ci for ci in range(4) if not any(quarter(m) == ci for _, m in placed)]
    early = len(placed) < EMPTY_CORNER_UNTIL
    # the corner the opponent has just played in, while its moves follow the tree; a corner the opponent has just
    # taken (no stone of ours there) waits while empty corners are left, as players take those first
    last_corner = quarter(moves[-1][1]) if placed and moves[-1][1] != 'pass' else None
    ours_there = last_corner is not None and any(w == me and quarter(m) == last_corner for w, m in placed)
    if last_corner is not None and (ours_there or not (early and free)):
        ci = review._quarter(coach.parse_point(moves[-1][1], 19))
        name, fr, fc = review.CORNERS[ci]
        turn = lambda p: joseki.gtp(*review._turn(coach.parse_point(p, 19), fr, fc))
        back = lambda p, diag: coach.gtp(*review._turn(joseki.rc(joseki.mirror(p) if diag else p), fr, fc), 19)
        corner = [(w, m) for w, m in placed if review._quarter(coach.parse_point(m, 19)) == ci]
        if all(a[0] != b[0] for a, b in zip(corner, corner[1:])):
            for diag in (False, True):
                key = ' '.join(joseki.mirror(turn(m)) if diag else turn(m) for _, m in corner)
                node = nodes.get(key)
                if node and not node.get('settled'):
                    kids = [k for k in node.get('next', []) if not k.get('tenuki')]
                    mv = pick([(back(k['move'], diag), RATING_WEIGHTS.get(k['rating'], 1)) for k in kids])
                    if mv:
                        return mv
    # an empty corner during the first moves: a joseki starting point
    if early:
        if free:
            name, fr, fc = review.CORNERS[rnd.choice(free)]
            diag = rnd.random() < 0.5
            return pick([(coach.gtp(*review._turn(joseki.rc(joseki.mirror(m) if diag else m), fr, fc), 19), w)
                         for m, w in START_WEIGHTS.items()])
    return None
