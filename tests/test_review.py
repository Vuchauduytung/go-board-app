import hashlib
import random

import pytest
from sgfmill import sgf

import accounts
import coach
import kgcache
import review
import sessions


def make_sgf(n=19, moves=60, seed=1, setup=(), komi='6.5', passes=()):
    rnd = random.Random(seed)
    game = sgf.Sgf_game(size=n)
    root = game.get_root()
    root.set('KM', komi)
    root.set('PB', 'Black Player')
    root.set('PW', 'White Player')
    if setup:
        root.set_setup_stones(setup, [])
    taken = set(setup)
    colour = 'w' if setup else 'b'
    for k in range(moves):
        node = game.extend_main_sequence()
        if k in passes:
            node.set_move(colour, None)
        else:
            p = rnd.choice([(r, c) for r in range(n) for c in range(n) if (r, c) not in taken])
            taken.add(p)
            node.set_move(colour, p)
        colour = 'w' if colour == 'b' else 'b'
    return game.serialise()


def test_parse_sgf_with_handicap_and_pass():
    data = make_sgf(moves=12, setup=[(3, 3), (15, 15)], passes={5})
    g = review.parse_sgf(data)
    assert g['board_size'] == 19 and g['komi'] == 6.5 and g['black'] == 'Black Player'
    assert g['base'][19 - 1 - 3][3] == 1 and g['base'][19 - 1 - 15][15] == 1   # sgfmill row 0 = bottom
    assert g['moves'][0][0] == 'W' and g['moves'][5][1] == 'pass' and len(g['moves']) == 12


def test_parse_sgf_rejects_bad_files():
    with pytest.raises(review.SgfError):
        review.parse_sgf(b'not an sgf')
    with pytest.raises(review.SgfError):
        review.parse_sgf(sgf.Sgf_game(size=7).serialise())


class FakeKataGo:
    """Deterministic scores per position; never lists the played move, so its loss comes from the next position."""

    def __init__(self):
        self.calls = 0

    def __call__(self, p):
        self.calls += 1
        board, nxt, _ = coach.final_position(p['matrix'], p['to_play'], p.get('moves', []))
        h = int(hashlib.sha1(repr((board, nxt)).encode()).hexdigest(), 16)
        n = len(board)
        empty = [coach.gtp(r, c, n) for r in range(n) for c in range(n) if not board[r][c]]
        lead = (h % 1000) / 100 - 5
        moves = [{'move': mv, 'winrate': 0.5, 'score_lead': lead - k * 0.3, 'visits': 30 - k,
                  'pv': [mv] + empty[k:k + 3]} for k, mv in enumerate(empty[(h % 7):(h % 7) + 20])]
        return {'winrate': 0.5 + lead / 20, 'score_lead': lead, 'visits': p.get('max_visits'),
                'moves': moves[:p.get('top', 5)]}


def run_review(game, kg):
    r = review.new_review(game)
    for _ in range(200):
        r = review.step(game, r, kg, seconds=5)
        if r['status'] == 'done':
            return r
    raise AssertionError('review did not finish')


def test_review_finds_top_mistakes_with_ten_move_lines():
    game = review.parse_sgf(make_sgf(moves=80))
    r = run_review(game, FakeKataGo())
    for who in ('B', 'W'):
        ms = r['mistakes'][who]
        assert 0 < len(ms) <= review.MISTAKES
        assert [m['loss'] for m in ms] == sorted((m['loss'] for m in ms), reverse=True)
        for m in ms:
            assert game['moves'][m['number'] - 1] == [who, m['move']]
            assert len(m['line']) == review.LINE and m['line'][0] == m['best']
    p = review.progress(r)
    assert p['status'] == 'done' and p['done'] == len(game['moves']) + 1 and 'positions' not in p


def test_review_resumes_where_it_stopped():
    game = review.parse_sgf(make_sgf(moves=30))
    kg = FakeKataGo()
    r = review.step(game, review.new_review(game), kg, seconds=0.0001)   # one batch only
    assert 0 < r['done'] < r['total'] and r['status'] == 'running'
    r = run_review(game, kg)
    assert r['status'] == 'done'


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'ROOT', tmp_path)
    accounts._local.clear()
    kgcache._local.clear()


def test_session_cache_answers_repeats_and_dies_with_the_session(storage):
    s = sessions.create('u', {'base': [[0] * 9 for _ in range(9)], 'board_size': 9, 'played': []})
    kg = FakeKataGo()
    payload = {'matrix': [[0] * 9 for _ in range(9)], 'to_play': 'B', 'max_visits': 50}
    first = kgcache.cached(kg, payload, ('u', s['id']))
    kgcache._local.clear()                                    # shared cache gone: the session still knows
    assert kgcache.cached(kg, payload, ('u', s['id'])) == first and kg.calls == 1
    assert kgcache.cached(kg, payload, ('other', s['id'])) == first and kg.calls == 2   # not another user's cache
    sessions.delete('u', s['id'])
    assert not list(sessions.ROOT.glob('*/*.kg'))


def test_best_line_grows_in_batches_and_is_kept(storage):
    game = review.parse_sgf(make_sgf(moves=40))
    kg = FakeKataGo()
    r = run_review(game, kg)
    m = r['mistakes']['B'][0]
    i, line = m['number'] - 1, list(m['line'])
    more = review.extend_line(kg, game, i, line, 10)
    assert len(more) == 10
    assert review.extend_line(kg, game, i, line, 10) == more   # same position, same answer

    s = sessions.create('u', {'base': game['base'], 'board_size': 19, 'played': [], 'game': game, 'review': r})
    sessions.set_mistake_line('u', s['id'], m['number'], line + more)
    stored = sessions.get('u', s['id'])['review']['mistakes']['B'][0]
    assert stored['line'] == line + more
    sessions.set_mistake_line('u', s['id'], m['number'], ['A1'] * 30)   # not a longer version: ignored
    assert sessions.get('u', s['id'])['review']['mistakes']['B'][0]['line'] == line + more


def test_best_line_from_any_position():
    kg = FakeKataGo()
    base = {'matrix': [[0] * 9 for _ in range(9)], 'to_play': 'W', 'komi': 7.5}
    first = review.extend_from(kg, base, 'W', [], 10)
    assert len(first) == 10
    more = review.extend_from(kg, base, 'W', first, 10)
    assert len(more) == 10
    last = kg.calls
    review.extend_from(kg, base, 'W', first + ['pass', 'pass'], 10)   # the game is over: nothing more to search
    assert kg.calls == last
