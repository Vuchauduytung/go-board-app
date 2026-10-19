import pytest

import accounts
import exercises
import review


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(exercises, 'ROOT', tmp_path)
    monkeypatch.setattr(accounts, 'ADMINS', {'admin@x.com'})


def board(stones, n=9):
    m = [[0] * n for _ in range(n)]
    for (r, c), v in stones.items():
        m[r][c] = v
    return m


def test_classify_by_the_shape_around_the_answer():
    capture = board({(3, 4): 1, (5, 4): 1, (4, 3): 1, (4, 4): 2})
    assert exercises.classify(capture, 'B', 'F5', 50) == 'tesuji'            # takes the white stone
    two = board({(4, 3): 1, (4, 5): 1})
    assert exercises.classify(two, 'B', 'E5', 50) == 'noi-cat'               # joins two black stones
    assert exercises.classify(two, 'W', 'E5', 50) == 'noi-cat'               # cuts them apart
    short = board({(0, 0): 1, (0, 1): 1, (0, 2): 1, (1, 0): 2, (1, 1): 2, (1, 2): 2})
    assert exercises.classify(short, 'B', 'D9', 50) == 'song-chet'           # the edge group's last liberty
    empty = board({})
    assert exercises.classify(empty, 'B', 'E5', 3) == 'khai-cuoc'
    assert exercises.classify(empty, 'B', 'E5', 60) == 'dai-cuc'


def test_exercises_from_a_review_shared_by_every_user():
    moves = [['B', 'E5'], ['W', 'C3'], ['B', 'G7'], ['W', 'C7'], ['B', 'G3']]
    game = review.game_from_moves([[0] * 9 for _ in range(9)], moves, 7.5, 'me', 'AI 3k')
    r = {'status': 'done', 'deep': {'2': {'loss': 1.5, 'best': 'C5', 'line': ['C5']},
                                    '3': {'loss': 6.0, 'best': 'D6', 'line': ['D6', 'E6'], 'good': ['D7', 'C6']},
                                    '1': {'loss': 3.0, 'best': 'pass', 'line': []}}}
    assert exercises.from_review(game, r, ['B'], 'me') == []                 # 1.5 points: too small
    (e,) = exercises.from_review(game, r, ['B', 'W'], 'me')                  # a pass is no exercise
    assert e['to_play'] == 'W' and e['answers'] == ['D6', 'D7', 'C6'] and e['last'] == 'G7' and e['number'] == 4
    assert sum(v for row in e['matrix'] for v in row) == 1 + 2 + 1           # the position before move 4
    ids = exercises.add([e], 'a@x.com')
    assert exercises.add(exercises.from_review(game, r, ['W'], 'me'), 'b@x.com') == ids   # kept once

    (s,) = exercises.listing('b@x.com')
    assert 'answers' not in s and not s['own'] and s['tried'] == 0 and s['kind_name']
    assert 'answers' not in exercises.problem(exercises.get(ids[0]), 'b@x.com')
    wrong = exercises.attempt(ids[0], 'b@x.com', 'A1')
    assert not wrong['ok'] and wrong['answers'][0] == 'D6' and wrong['line'] == ['D6', 'E6']
    assert exercises.attempt(ids[0], 'b@x.com', 'D7')['mine'] == {**wrong['mine'], 'ok': True, 'tries': 2}
    assert exercises.attempt(ids[0], 'c@x.com', 'D6')['mine']['first']
    (s,) = exercises.listing('c@x.com')
    assert s['tried'] == 2 and s['solvers'] == 2 and s['mine']['ok']
    assert exercises.attempt('nope', 'c@x.com', 'D6') is None

    assert not exercises.delete(ids[0], 'b@x.com')                           # only its owner or an admin
    assert exercises.delete(ids[0], 'a@x.com') and exercises.listing('a@x.com') == []


def test_exercises_favour_life_and_death_and_the_review_the_opening(monkeypatch):
    moves = [['B', 'E5'], ['W', 'C3'], ['B', 'G7'], ['W', 'C7'], ['B', 'G3'], ['W', 'A1']]
    game = review.game_from_moves([[0] * 9 for _ in range(9)], moves, 7.5, 'me', 'AI 3k')
    kinds = {2: 'khai-cuoc', 3: 'song-chet', 4: 'khai-cuoc', 5: 'tesuji'}   # by move number
    monkeypatch.setattr(exercises, 'classify', lambda board, who, move, number: kinds[number])
    deep = {'1': {'loss': 5.0, 'best': 'D5'}, '2': {'loss': 3.0, 'best': 'D6'}, '3': {'loss': 4.0, 'best': 'D4'},
            '4': {'loss': 3.5, 'best': 'E4'}}
    got = [e['number'] for e in exercises.from_review(game, {'deep': deep}, ['B', 'W'], 'me')]
    assert got == [3, 5, 2, 4]          # life and death 6 > tesuji 5.25 > opening 2.5, 2 though it lost 5
    # the review the other way round: the opening first
    r = {'positions': {str(i): [0.5, 0, 'A1', 0, 0] for i in range(7)}, 'mistakes': {},
         'deep': {k: {**v, 'line': [], 'kind': kinds[int(k) + 1]} for k, v in deep.items()}}
    review._finish(game, r)
    assert [m['number'] for m in r['mistakes']['W']] == [2, 4] and r['mistakes']['W'][0]['kind'] == 'khai-cuoc'
    assert [m['number'] for m in r['mistakes']['B']] == [5, 3]   # fights still listed, by points lost


def test_one_exercise_for_a_point_missed_move_after_move():
    moves = [['B', 'E5'], ['W', 'C3'], ['B', 'G7'], ['W', 'C7'], ['B', 'G3'], ['W', 'A1'], ['B', 'J9'], ['W', 'A9']]
    game = review.game_from_moves([[0] * 9 for _ in range(9)], moves, 7.5, 'me', 'AI 1d')
    deep = {'2': {'loss': 3.0, 'best': 'D6'}, '4': {'loss': 2.8, 'best': 'D6'}, '6': {'loss': 2.5, 'best': 'D6'},
            '0': {'loss': 2.2, 'best': 'C5'}}
    got = [(e['number'], e['answers'][0]) for e in exercises.from_review(game, {'deep': deep}, ['B'], 'me')]
    assert got == [(3, 'D6'), (1, 'C5')]
    r = {'positions': {str(i): [0.5, 0, 'A1', 0, 0] for i in range(9)}, 'mistakes': {},
         'deep': {k: {**v, 'line': [], 'kind': 'song-chet'} for k, v in deep.items()}}
    review._finish(game, r)
    (a, b) = r['mistakes']['B']
    assert a['number'] == 3 and a['repeats'] == [5, 7] and b['number'] == 1 and 'repeats' not in b
