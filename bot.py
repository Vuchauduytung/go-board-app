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

LEVELS = {
    '5k': (1.5, 15), '4k': (1.35, 13), '3k': (1.2, 11), '2k': (1.05, 9.5), '1k': (0.92, 8),
    '1d': (0.8, 7), '2d': (0.68, 6), '3d': (0.57, 5), '4d': (0.47, 4), '5d': (0.38, 3),
}
LEVELS.update({k: tuple(v) for k, v in json.loads(os.environ.get('BOT_LEVELS') or '{}').items()})
UNSURE = 1.5        # points added to the loss of a move searched once (less for more visits, see above)
SEARCH = coach.ROOT   # the review's own full search, so a finished game is reviewed from what was searched
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
