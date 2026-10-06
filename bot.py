# Playing against the AI: KataGo searches the position, then a move is drawn among its candidates so that the moves
# lose, on average, as many points as a player of the chosen level does. Each candidate's loss is its score next to
# the best move's; candidates are weighted exp(-loss / T), with T set per position so the expected loss is the
# level's (a quiet position lets any reasonable move through, a life-and-death one makes a weak level err now and
# then). A move losing more than the level's cap is never chosen.
#
# Calibrate here: LEVELS[level] = (average points lost per move, most points a single move may lose).
# The app shows the AI's and the player's average loss per move during a game, to compare with the table.

import math
import random

import coach

LEVELS = {
    '5k': (2.2, 30), '4k': (1.9, 25), '3k': (1.65, 20), '2k': (1.45, 16), '1k': (1.25, 13),
    '1d': (1.05, 10), '2d': (0.9, 8), '3d': (0.75, 6), '4d': (0.6, 4.5), '5d': (0.45, 3),
}
SEARCH = coach.ROOT   # the review's own full search, so a finished game is reviewed from what was searched
RELIABLE = 0.1      # the best move is taken among candidates with at least this share of the top visits
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


def choose(res, level, rnd=random):
    """A move for the side to move from a KataGo answer -> {move, loss, alternatives (the other allowed candidates,
    least loss first, for when the chosen one turns out illegal), best, best_lead, score_lead, winrate, resign}."""
    target, cap = LEVELS[level]
    moves = res['moves']
    if not moves:
        return {'move': 'pass', 'loss': 0.0, 'alternatives': [], 'best': None, 'best_lead': None,
                'score_lead': res['score_lead'], 'winrate': res['winrate'], 'resign': False}
    best_move = best(res)
    cands = sorted(((max(0.0, best_move['score_lead'] - m['score_lead']), m) for m in moves), key=lambda x: x[0])
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
