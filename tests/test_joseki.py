import joseki


def test_corner_and_mirror():
    assert joseki.local('Q16') and joseki.local('K10') and joseki.local('J9') and not joseki.local('H10') and not joseki.local('J8') and not joseki.local('D4')
    assert joseki.mirror('R16') == 'Q17' and joseki.mirror('Q16') == 'Q16' and joseki.mirror('K16') == 'Q10'
    assert joseki.symmetric(['Q16', 'R17']) and not joseki.symmetric(['R16'])


def fake_katago(p):
    """Corner moves around the last stone lose a little more each; D4 elsewhere is worth `away` points less."""
    played = {m for _, m in p['moves']}
    away = 3.0 if len(played) < 4 else -3.0   # after 4 moves tenuki is 3 points better: the joseki is over
    cand = ['R14', 'P17', 'O16', 'R12', 'Q14', 'S16']
    moves = [{'move': m, 'score_lead': 1.0 - 0.4 * i, 'visits': 200 - 20 * i, 'winrate': 0.5}
             for i, m in enumerate(c for c in cand if c not in played)]
    if not p.get('avoid'):   # the whole board: tenuki too, the corner moves barely searched
        moves = [{**m, 'visits': 2} for m in moves] + [{'move': 'D4', 'score_lead': 1.0 - away, 'visits': 400, 'winrate': 0.5}]
    else:
        assert 'D4' in p['avoid'] and 'Q16' not in p['avoid']
    return {'winrate': 0.5, 'score_lead': 0.5, 'moves': moves}


def test_build_grows_lines_and_ends_them_with_tenuki():
    tree = joseki.build(fake_katago, max_nodes=60, parallel=2, log=lambda s: None)
    nodes = tree['nodes']
    assert set(joseki.STARTS) <= set(nodes)
    q16 = nodes['Q16']
    assert [k['move'] for k in q16['next']][:2] == ['R14', 'P17'] and q16['next'][0]['rating'] == 'best'
    assert all(k['loss'] - q16['next'][0]['loss'] <= joseki.BRANCH for k in q16['next'])
    assert len(q16['next']) <= joseki.WIDTH and q16['tenuki'] == 3.0
    ended = [k for k, n in nodes.items() if n['settled']]
    assert ended and all(len(k.split()) >= max(4, joseki.FREE) for k in ended)
    # a symmetric start: R14 and its mirror P17… only one of each mirrored pair under 4-4
    assert not any(k['move'] == joseki.mirror(o['move']) for k in nodes['Q16']['next'] for o in nodes['Q16']['next'] if k is not o)
