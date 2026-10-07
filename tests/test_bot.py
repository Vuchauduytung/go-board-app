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
    res = answer([5, -5, -20, -26])                           # one good move; the others lose 10, 25, 31 points
    assert mean_loss(res, '5d') == 0                          # past 5d's cap: never chosen
    assert 0 < mean_loss(res, '5k') < 2                       # 5k sometimes loses 10, never a group of 25+
    # a barely searched move with a wild score is not taken as the best
    res = answer([5, 4.5, 40], visits=[400, 300, 1])
    assert bot.choose(res, '5d', random.Random(0))['best'] == 'A1'


def test_alternatives_and_pass():
    c = bot.choose(answer([3, 2.9, 2.8]), '1d', random.Random(0))
    assert sorted([c['move']] + c['alternatives']) == ['A1', 'A2', 'A3']
    assert bot.choose({'winrate': 0.5, 'score_lead': 0, 'moves': []}, '1d')['move'] == 'pass'


def test_barely_searched_moves_count_as_worse():
    res = answer([5, 4.9, 4.8], visits=[400, 300, 1])          # the last one looked at once: not as good as it says
    c = bot.choose(res, '5d', random.Random(0))
    loss = {c['move']: c['loss']}
    picks = [bot.choose(res, '5k', random.Random(i)) for i in range(300)]
    once = [p['loss'] for p in picks if p['move'] == 'A3']
    assert once and min(once) > 1.2                             # 0.2 points by its score, ~1.4 counted


def test_levels_from_the_environment(monkeypatch):
    import importlib
    monkeypatch.setenv('BOT_LEVELS', '{"1k": [0.5, 4]}')
    b = importlib.reload(bot)
    assert b.LEVELS['1k'] == (0.5, 4) and b.LEVELS['5k'] == (1.5, 15)
    monkeypatch.delenv('BOT_LEVELS')
    importlib.reload(bot)


def test_passes_at_the_end_never_in_the_middle():
    end = answer([0.5, 0.4, 0.3])
    end['moves'][1]['move'] = 'pass'                           # passing loses 0.1: the game is over
    assert all(bot.choose(end, '5k', random.Random(i))['move'] == 'pass' for i in range(50))
    filling = answer([0.5, 0.5, -0.2])
    filling['moves'][2]['move'] = 'pass'                       # 0.7 to gain still: plays on…
    assert bot.choose(filling, '1k', random.Random(0))['move'] != 'pass'
    assert bot.choose(filling, '1k', random.Random(0), opponent_passed=True)['move'] == 'pass'   # …unless you passed
    middle = answer([3, 2, 1, -6])
    middle['moves'][3]['move'] = 'pass'
    assert not any(bot.choose(middle, '5k', random.Random(i))['move'] == 'pass' for i in range(300))


def test_games_run_on_the_cpu():
    assert bot.SEARCH['engine'] == 'cpu' and bot.SEARCH['max_visits'] <= 200 and bot.SEARCH['top'] == 40
