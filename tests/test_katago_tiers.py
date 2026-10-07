"""The KataGo tiers of app._katago, without the app's models: the module's function is rebuilt from its source."""
import ast
import json
import time
import types
from pathlib import Path

import pytest


def tiers(monkeypatch, cpu_url='http://cpu'):
    src = Path(__file__).parent.parent.joinpath('app.py').read_text()
    tree = ast.parse(src)
    keep = {'KataGoError', '_post_katago', '_katago_cpu', '_katago'}
    body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in keep]
    mod = types.ModuleType('tiers')
    calls, added = [], []
    mod.__dict__.update(json=json, time=time, os=__import__('os'), urllib=__import__('urllib.request'),
                        log=types.SimpleNamespace(warning=lambda *a: None),
                        usage=types.SimpleNamespace(add=lambda k, *a: added.append(k), katago=lambda *a: None),
                        KATAGO_URL='http://gpu', KATAGO_CPU_URL=cpu_url, CPU_MAX_VISITS=200, GPU_RETRY=300,
                        _gpu_down={'until': 0.0}, _katago_headers=lambda: {})
    exec(compile(ast.Module(body=body, type_ignores=[]), 'app.py', 'exec'), mod.__dict__)
    return mod, calls, added


def test_play_on_cpu_and_fallback_when_the_gpu_fails(monkeypatch):
    mod, calls, added = tiers(monkeypatch)
    gpu_ok = {'v': True}

    def post(url, payload, headers, timeout):
        calls.append((url, payload['max_visits'], 'engine' in payload))
        if url == 'http://gpu' and not gpu_ok['v']:
            raise mod.KataGoError(502, 'KataGo: no credit')
        return {'moves': [], 'winrate': .5, 'score_lead': 0}
    mod._post_katago = post

    r = mod._katago({'max_visits': 200, 'engine': 'cpu'})                  # a game: the CPU, as asked
    assert r['engine'] == 'cpu' and calls[-1] == ('http://cpu', 200, False) and 'degraded' not in r
    r = mod._katago({'max_visits': 400})                                   # the GPU while it works
    assert r['engine'] in ('gpu', 'main') and calls[-1][0] == 'http://gpu'
    gpu_ok['v'] = False
    r = mod._katago({'max_visits': 400})                                   # fails: the CPU, fewer visits, marked
    assert calls[-1] == ('http://cpu', 200, False) and r['degraded'] and 'katago:gpu_failures' in added
    n = len(calls)
    mod._katago({'max_visits': 400})                                       # GPU not tried again for a while
    assert len(calls) == n + 1 and calls[-1][0] == 'http://cpu'
    mod._gpu_down['until'] = 0
    gpu_ok['v'] = True
    assert mod._katago({'max_visits': 400})['engine'] != 'cpu'             # back on the GPU


def test_bad_query_is_not_retried_on_the_cpu(monkeypatch):
    mod, calls, _ = tiers(monkeypatch)

    def post(url, payload, headers, timeout):
        calls.append(url)
        raise mod.KataGoError(422, 'KataGo: bad move')
    mod._post_katago = post
    with pytest.raises(mod.KataGoError):
        mod._katago({'max_visits': 400})
    assert calls == ['http://gpu']


def test_degraded_answers_are_not_cached(monkeypatch):
    import kgcache
    kgcache._local.clear()
    monkeypatch.setattr(kgcache, '_valkey', lambda: None)
    payload = {'matrix': [[0] * 9 for _ in range(9)], 'to_play': 'B', 'max_visits': 400}
    kgcache.cached(lambda p: {'moves': [], 'degraded': True}, payload)
    assert kgcache.peek(payload) is None
    kgcache.cached(lambda p: {'moves': []}, payload)
    assert kgcache.peek(payload) == {'moves': []}
