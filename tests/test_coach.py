import coach
import kgcache

N = 9


def empty(n=N):
    return [[0] * n for _ in range(n)]


def test_capture_and_ko_point():
    # White stone at E5 surrounded on three sides; Black D5 captures it and leaves a ko
    m = empty()
    for mv, v in [('E6', 1), ('E4', 1), ('F5', 1), ('D6', 2), ('D4', 2), ('C5', 2), ('E5', 2)]:
        r, c = coach.parse_point(mv, N)
        m[r][c] = v
    board, nxt, ko = coach.final_position(m, 'B', [['B', 'D5']])
    assert board[4][4] == 0 and board[4][3] == 1   # E5 captured, D5 played
    assert nxt == 'W' and ko == (4, 4)


def test_cache_key_is_by_position_not_by_path():
    a = {'matrix': empty(), 'to_play': 'B', 'moves': [['B', 'C3'], ['W', 'G7'], ['B', 'C7']]}
    b = {'matrix': empty(), 'to_play': 'B', 'moves': [['B', 'C7'], ['W', 'G7'], ['B', 'C3']]}
    m = empty()
    for mv, v in [('C3', 1), ('C7', 1), ('G7', 2)]:
        r, c = coach.parse_point(mv, N)
        m[r][c] = v
    c = {'matrix': m, 'to_play': 'W'}
    assert kgcache.key(a) == kgcache.key(b) == kgcache.key(c)
    assert kgcache.key({**c, 'to_play': 'B'}) != kgcache.key(c)
    assert kgcache.key({**c, 'max_visits': 100}) != kgcache.key(c)


class FakeKataGo:
    """Moves A1..: the plain search returns 20 moves, the one avoiding them 30 more, all worse."""

    def __init__(self):
        self.calls = []

    def __call__(self, p):
        self.calls.append(p)
        n = len(p['matrix'])
        points = [coach.gtp(r, c, n) for r in range(n) for c in range(n)]
        if p.get('avoid'):
            pool = [mv for mv in points if mv not in p['avoid']][:30]
            moves = [{'move': mv, 'winrate': 0.3, 'score_lead': -5 - i, 'visits': 2, 'pv': [mv, 'A1']}
                     for i, mv in enumerate(pool)]
        else:
            moves = [{'move': mv, 'winrate': 0.5, 'score_lead': 1 - i * 0.1, 'visits': 50 - i, 'pv': [mv, 'B2', 'C3']}
                     for i, mv in enumerate(points[:20])]
        return {'winrate': 0.55, 'score_lead': 1.0, 'visits': 400, 'moves': moves[:p.get('top', 5)],
                'ownership': [[0.0] * n for _ in range(n)] if p.get('ownership') else None}


def test_variations_best_and_worst():
    kg = FakeKataGo()
    v = coach.variations(kg, {'matrix': empty(), 'to_play': 'B'}, 10)
    first, second = kg.calls[0], kg.calls[1]
    favourites = [m['move'] for m in kg(first)['moves']]
    assert len(v['best']) == 10 and len(v['worst']) == 10
    assert [m['move'] for m in v['best']] == favourites[:10]
    assert v['worst'][0]['score_lead'] == -34   # the lowest-scoring move of the second search
    assert set(second['avoid']) == set(favourites)


def test_explaining_a_suggestion_asks_for_its_continuations():
    kg = FakeKataGo()
    text, marks = coach.build_context(empty(), 'B', 'Giải thích nước gợi ý 2', kg)
    second = kg({'matrix': empty(), 'to_play': 'B', **coach.ROOT})['moves'][1]['move']
    assert marks and marks[0]['move'] == second
    assert any(p.get('moves') == [['B', second]] for p in kg.calls)   # searched the position after it
    assert f'Thay cho {second}, 20 lựa chọn tốt nhất của Đen' in text
    assert f'Thay cho {second}, 20 lựa chọn kém nhất của Đen' in text
    assert f'Sau {second}, 10 biến tốt nhất cho Trắng' in text
    assert f'Sau {second}, 10 biến kém nhất cho Trắng' in text
    assert 'Trắng ' in text and '→' in text   # lines carry colours


def test_played_move_references():
    kg = FakeKataGo()
    played = [['B', 'C3'], ['W', 'G7']]
    m = empty()
    m[6][2], m[2][6] = 1, 2
    text, marks = coach.build_context(m, 'B', 'nước vừa đi có tốt không?', kg, base=empty(), base_to_play='B',
                                      played=played)
    assert marks[0]['move'] == 'G7'
    assert 'nước thứ 2 đã đi' in text


def test_cached_variations_are_reused():
    kg = FakeKataGo()
    store = {}

    def cached(p):
        k = kgcache.key(p)
        if k not in store:
            store[k] = kg(p)
        return store[k]

    coach.build_context(empty(), 'B', 'Nước A9 có tốt không?', cached)
    n = len(kg.calls)
    coach.build_context(empty(), 'B', 'Nước A9 có tốt không?', cached)
    assert len(kg.calls) == n   # second time: everything from the cache


def test_clean_actions():
    acts = coach.clean_actions([
        {'type': 'play', 'moves': [{'color': 'B', 'point': 'c3'}, {'color': 'X', 'point': 'D4'},
                                   {'color': 'W', 'point': 'Z99'}]},
        {'type': 'remove', 'points': ['J9', 'K1']},
        {'type': 'back_to', 'move_number': 3},
        {'type': 'back_to', 'move_number': -1},
        {'type': 'to_play', 'color': 'W'},
        {'type': 'restart'},
        {'type': 'rm -rf'},
        'junk',
    ], N)
    assert acts == [{'type': 'play', 'moves': [{'color': 'B', 'point': 'C3'}]},
                    {'type': 'remove', 'points': ['J9']},
                    {'type': 'back_to', 'move_number': 3},
                    {'type': 'to_play', 'color': 'W'},
                    {'type': 'restart'}]


def test_twenty_alternatives_and_ten_replies():
    kg = FakeKataGo()
    text, _ = coach.build_context(empty(), 'B', 'Nước A9 có tốt không?', kg)
    section = text.split('Thay cho A9, 20 lựa chọn tốt nhất')[1]
    best_alts = section.split('Thay cho A9, 20 lựa chọn kém nhất')[0]
    worst_alts = section.split('Thay cho A9, 20 lựa chọn kém nhất')[1].split('Sau A9')[0]
    replies = section.split('Sau A9, 10 biến tốt nhất')[1].split('Sau A9, 10 biến kém nhất')
    count = lambda block: sum(1 for line in block.splitlines() if ' thắng ' in line)
    assert count(best_alts) == 20 and count(worst_alts) == 20
    assert count(replies[0]) == 10 and count(replies[1].split('VÙNG')[0]) == 10
