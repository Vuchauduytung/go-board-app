import random

import bot


def answer(leads, visits=None):
    visits = visits or [100 - i for i in range(len(leads))]
    return {'winrate': 0.5, 'score_lead': leads[0],
            'moves': [{'move': f'A{i + 1}', 'score_lead': l, 'visits': v} for i, (l, v) in enumerate(zip(leads, visits))]}


def mean_loss(res, level, n=4000):
    rnd = random.Random(1)
    return sum(bot.choose(res, level, rnd)['loss'] for _ in range(n)) / n


def test_levels_lose_their_average_and_never_past_the_cap():
    res = answer([10 - 0.5 * i for i in range(40)])          # candidates losing 0, 0.5, 1 … 19.5 points
    for level, (target, cap) in bot.LEVELS.items():
        assert abs(mean_loss(res, level) - target) < 0.15, level
        rnd = random.Random(2)
        assert max(bot.choose(res, level, rnd)['loss'] for _ in range(500)) <= cap
    assert mean_loss(res, '5d') < mean_loss(res, '1k') < mean_loss(res, '5k')


def test_one_good_move_and_noisy_candidates():
    res = answer([5, -25, -26, -30])                          # only one move does not lose a group
    assert mean_loss(res, '5d') == 0                          # past 5d's cap: never chosen
    assert 0 < mean_loss(res, '5k') < 2.5                     # 5k sometimes blunders, within its cap
    # a barely searched move with a wild score is not taken as the best
    res = answer([5, 4.5, 40], visits=[400, 300, 1])
    assert bot.choose(res, '5d', random.Random(0))['best'] == 'A1'


def test_alternatives_and_pass():
    c = bot.choose(answer([3, 2.9, 2.8]), '1d', random.Random(0))
    assert sorted([c['move']] + c['alternatives']) == ['A1', 'A2', 'A3']
    assert bot.choose({'winrate': 0.5, 'score_lead': 0, 'moves': []}, '1d')['move'] == 'pass'
