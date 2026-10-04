import pytest

import accounts
import sessions

EMPTY = [[0] * 9 for _ in range(9)]


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'ROOT', tmp_path)
    monkeypatch.setattr(accounts, 'ADMINS', {'admin@x.com'})
    monkeypatch.setattr(sessions, 'PINNED_LIMIT', 2)
    accounts._local.clear()


def new(user, **kw):
    return sessions.create(user, {'base': EMPTY, 'board_size': 9, 'scan_id': None, 'played': [], 'to_play': 'B',
                                  'start_turn': 'B', 'matrix': EMPTY, **kw})


def test_keeps_ten_recent_plus_pinned():
    first = new('u')
    sessions.set_pinned('u', first['id'], True)
    ids = [new('u')['id'] for _ in range(12)]
    listed = sessions.list_sessions('u')
    assert [s['id'] for s in listed['recent']] == ids[::-1][:10]
    assert [s['id'] for s in listed['pinned']] == [first['id']]
    assert sessions.get('u', first['id'])          # pinned: kept although older
    assert sessions.get('u', ids[0]) is None       # 12th most recent unpinned: forgotten


def test_pin_limit_for_users_not_admins():
    for user in ('u', 'admin@x.com'):
        ids = [new(user)['id'] for _ in range(3)]
        sessions.set_pinned(user, ids[0], True)
        sessions.set_pinned(user, ids[1], True)
        if user == 'u':
            with pytest.raises(sessions.LimitReached):
                sessions.set_pinned(user, ids[2], True)
        else:
            assert sessions.set_pinned(user, ids[2], True)['pinned']


def test_users_do_not_see_each_other():
    s = new('a')
    assert sessions.get('b', s['id']) is None
    assert sessions.list_sessions('b')['recent'] == []
    assert sessions.get('a', '../../etc/passwd') is None


def test_update_and_chat_history():
    s = new('u')
    sessions.update('u', s['id'], {'played': [{'r': 1, 'c': 1, 'color': 1}], 'to_play': 'W', 'base': 'ignored'})
    for i in range(6):
        sessions.append_chat('u', s['id'], {'q': f'q{i}', 'a': f'a{i}'})
    s = sessions.get('u', s['id'])
    assert s['to_play'] == 'W' and s['base'] == EMPTY
    assert sessions.summary(s)['moves'] == 1 and sessions.summary(s)['questions'] == 6
    assert sessions.history(s, 2) == [{'role': 'user', 'content': 'q4'}, {'role': 'assistant', 'content': 'a4'},
                                      {'role': 'user', 'content': 'q5'}, {'role': 'assistant', 'content': 'a5'}]


def test_quota(monkeypatch):
    monkeypatch.setitem(accounts.LIMITS, 'katago', 2)
    for user in ('u', 'admin@x.com'):
        accounts.add(user, 'katago', 2)
    with pytest.raises(accounts.QuotaExceeded):
        accounts.ensure('u', 'katago')
    accounts.ensure('admin@x.com', 'katago')   # admins are never limited
    me = accounts.usage('admin@x.com')
    assert me['admin'] and me['usage']['katago'] == {'used': 2, 'limit': None, 'unit': accounts.UNITS['katago']}


def test_iap_header_only_trusted_behind_iap(monkeypatch):
    monkeypatch.setattr(accounts, 'TRUST_IAP', False)
    assert accounts.identity('accounts.google.com:admin@x.com', 'abc123') == 'abc123'
    monkeypatch.setattr(accounts, 'TRUST_IAP', True)
    assert accounts.identity('accounts.google.com:Admin@X.com', 'abc123') == 'admin@x.com'


def test_cloudflare_access_header_only_when_enabled(monkeypatch):
    monkeypatch.setattr(accounts, 'TRUST_IAP', False)
    assert accounts.identity(None, 'abc123', 'Admin@X.com') == 'abc123'
    monkeypatch.setattr(accounts, 'TRUST_CF', True)
    assert accounts.identity(None, 'abc123', 'Admin@X.com') == 'admin@x.com'


def test_oauth2_proxy_header_only_when_enabled(monkeypatch):
    monkeypatch.setattr(accounts, 'TRUST_IAP', False)
    monkeypatch.setattr(accounts, 'TRUST_CF', False)
    assert accounts.identity(None, 'abc123', None, 'Admin@X.com') == 'abc123'
    monkeypatch.setattr(accounts, 'TRUST_FORWARDED', True)
    assert accounts.identity(None, 'abc123', None, 'Admin@X.com') == 'admin@x.com'


def test_write_retries_stale_handle_and_cache_put_never_fails(tmp_path, monkeypatch):
    import errno
    from pathlib import Path
    real, calls = Path.write_text, []

    def flaky(self, *a, **k):
        calls.append(self.name)
        if len(calls) == 1:
            raise OSError(errno.ESTALE, 'Stale file handle')
        return real(self, *a, **k)
    monkeypatch.setattr(Path, 'write_text', flaky)
    sessions._write(tmp_path / 'x.json', {'a': 1})
    assert (tmp_path / 'x.json').read_text() == '{"a": 1}' and calls[0] != calls[1]   # a fresh temporary name
    assert [p.name for p in tmp_path.iterdir()] == ['x.json']

    monkeypatch.setattr(sessions, '_write', lambda f, s: (_ for _ in ()).throw(OSError(errno.EIO, 'boom')))
    monkeypatch.setattr(sessions, '_file', lambda user, sid: tmp_path / 'x.json')
    sessions.cache_put('u', 'sid', 'k', {'v': 1})   # logged, not raised
