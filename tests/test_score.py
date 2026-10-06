import score

E, B, W = 0, 1, 2


def own_from(rows):
    """Ownership +1 / -1 / 0 drawn as 'b' / 'w' / '.'."""
    return [[{'b': 1.0, 'w': -1.0, '.': 0.0}[ch] for ch in row] for row in rows]


BOARD = [  # 5x5: a wall on column 2; White has a lone stone in Black's area
    [E, B, B, W, E],
    [E, W, B, W, E],
    [E, E, B, W, E],
    [E, E, B, W, E],
    [E, E, B, W, E],
]
OWN = own_from(['bbbww', 'bbbww', 'bbbww', 'bbbww', 'bbbww'])


def test_dead_stone_counts_for_the_side_around_it():
    res = score.count(BOARD, OWN, komi=0.5)
    assert res['dead'] == [[1, 1]]
    # Black: 6 stones + 9 points (the dead stone's point included); White: 5 stones + 5 points + komi
    assert res['black'] == {'stones': 6, 'territory': 9, 'total': 15}
    assert res['white']['total'] == 10.5 and res['lead'] == 4.5


def test_player_flips_a_group():
    res = score.count(BOARD, OWN, komi=0.5, toggles=[(1, 1)])          # "it is alive"
    assert res['dead'] == [] and res['black']['territory'] < 9
    res = score.count(BOARD, OWN, komi=0.5, toggles=[(0, 3)])          # White's wall dead, the lone stone too
    assert [0, 3] in res['dead'] and [1, 1] in res['dead']


def test_unfinished_border_goes_by_ownership():
    board = [[E] * 5 for _ in range(5)]
    board[2][1], board[2][3] = B, W
    res = score.count(board, own_from(['bb.ww'] * 5), komi=0)
    assert len(res['territory']['B']) == 9 and len(res['territory']['W']) == 9 and len(res['dame']) == 5
