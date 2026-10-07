# KataGo answers cached by position (board after the moves, side to move, ko point, komi, rules) and search
# settings, so a variation explained once is answered from memory when the user asks about it again or
# plays it out on the board, whatever order the stones were placed in.
# Two levels: the session's own cache (kept as long as the session, see sessions.cache_get) and a shared one in
# Valkey (7 days) with an in-process fallback.

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict

import coach
import sessions

log = logging.getLogger('kgcache')

TTL = int(os.environ.get('KATAGO_CACHE_TTL', str(7 * 86400)))
PREFIX = 'go-scan:kg:'
LOCAL_MAX = 500
_local = OrderedDict()
_lock = threading.Lock()   # reviews search several positions at once


def key(payload):
    board, nxt, ko = coach.final_position(payload['matrix'], payload.get('to_play', 'B'), payload.get('moves', []))
    position = [board, nxt, ko, float(payload.get('komi', 7.5)), payload.get('rules', 'chinese')]
    search = [int(payload.get('max_visits', 400)), int(payload.get('top', 5)), bool(payload.get('ownership')),
              sorted(payload.get('avoid', [])), float(payload.get('wide_root_noise') or 0)]
    return PREFIX + hashlib.sha1(json.dumps([position, search]).encode()).hexdigest()


def _valkey():
    from rag.chat import _valkey
    return _valkey()


def peek(payload):
    """Cached answer for this query, or None."""
    k = key(payload)
    try:
        r = _valkey()
        if r is not None:
            v = r.get(k)
            return json.loads(v) if v else None
    except Exception as ex:
        log.warning('Valkey unavailable, using in-process KataGo cache: %s', ex)
    with _lock:
        if k in _local:
            _local.move_to_end(k)
            return _local[k]
    return None


def put(payload, value):
    k = key(payload)
    try:
        r = _valkey()
        if r is not None:
            r.set(k, json.dumps(value), ex=TTL)
            return
    except Exception as ex:
        log.warning('Valkey unavailable, caching KataGo answer in process: %s', ex)
    with _lock:
        _local[k] = value
        while len(_local) > LOCAL_MAX:
            _local.popitem(last=False)


def lookup(payload, session=None):
    """The answer to this query if it was searched before (the session's own cache first), without searching."""
    value = sessions.cache_get(*session, key(payload)[len(PREFIX):]) if session else None
    return value if value is not None else peek(payload)


def cached(run, payload, session=None):
    """run(payload) on a cache miss, remembering the answer; session = (user, session id) also indexes the
    answer in that session."""
    k = key(payload)[len(PREFIX):]
    value = sessions.cache_get(*session, k) if session else None
    if value is not None:
        return value
    value = peek(payload)
    if value is None:
        value = run(payload)
        if value.get('degraded'):   # fewer visits than asked (KataGo's CPU fallback): not kept as the real answer
            return value
        put(payload, value)
    if session:
        sessions.cache_put(*session, k, value)
    return value
