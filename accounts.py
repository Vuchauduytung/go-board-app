# Who is asking and how much they may use.
# Identity: the Google account from IAP on Cloud Run (TRUST_IAP_HEADER=1), or on the Oracle VM from oauth2-proxy
# (TRUST_FORWARDED_EMAIL=1) or Cloudflare Access (TRUST_CF_ACCESS=1). Enable only the proxy in front of the app:
# these headers cannot be forged when the app is reachable only through that proxy.
# Otherwise LOCAL_USER, else the browser's random id. Admins (ADMIN_EMAILS) have no limits; other users get
# a daily budget (UTC day) of LLM tokens and KataGo searches, sized for about 10 users on the free tiers.

import hashlib
import logging
import os
import time

log = logging.getLogger('accounts')

ADMINS = {e.strip().lower() for e in os.environ.get('ADMIN_EMAILS', '').split(',') if e.strip()}
TRUST_IAP = os.environ.get('TRUST_IAP_HEADER') == '1'
TRUST_CF = os.environ.get('TRUST_CF_ACCESS') == '1'
TRUST_FORWARDED = os.environ.get('TRUST_FORWARDED_EMAIL') == '1'
LOCAL_USER = os.environ.get('LOCAL_USER', '').strip().lower()
LIMITS = {
    'llm': int(os.environ.get('USER_DAILY_LLM_TOKENS', '200000')),   # ~25 board questions (~8k tokens each), any provider
    'katago': int(os.environ.get('USER_DAILY_KATAGO_VISITS', '150000')),    # ~3 game reviews or ~100 explained moves
}
UNITS = {'llm': 'token AI', 'katago': 'lượt tìm kiếm KataGo'}   # KataGo: visits not served from the cache
PREFIX = 'go-scan:use'

_local = {}   # fallback when Valkey is down (single instance only)


class QuotaExceeded(Exception):
    pass


def identity(iap_email, client_id, cf_email=None, forwarded_email=None):
    if TRUST_IAP and iap_email:
        return iap_email.split(':')[-1].lower()
    if TRUST_CF and cf_email:
        return cf_email.strip().lower()
    if TRUST_FORWARDED and forwarded_email:
        return forwarded_email.strip().lower()
    if LOCAL_USER:
        return LOCAL_USER
    if client_id and client_id.isalnum() and len(client_id) <= 64:
        return client_id
    return 'anonymous'


def is_admin(user):
    return user in ADMINS


def user_dir_name(user):
    """Directory name for a user's files: no e-mail addresses in paths."""
    return hashlib.sha256(user.encode()).hexdigest()[:20]


def _valkey():
    from rag.chat import _valkey
    return _valkey()


def _key(resource, user):
    return f'{PREFIX}:{resource}:{time.strftime("%Y-%m-%d", time.gmtime())}:{user}'


def used(user, resource):
    k = _key(resource, user)
    try:
        r = _valkey()
        if r is not None:
            return int(r.get(k) or 0)
    except Exception as ex:
        log.warning('Valkey unavailable, reading usage in process: %s', ex)
    return _local.get(k, 0)


def ensure(user, resource):
    """Raise QuotaExceeded when a normal user has used up today's budget for `resource`."""
    if not is_admin(user) and used(user, resource) >= LIMITS[resource]:
        limit = f'{LIMITS[resource]:,}'.replace(',', '.')
        raise QuotaExceeded(f'Bạn đã dùng hết {limit} {UNITS[resource]} hôm nay, quay lại vào ngày mai nhé.')


def add(user, resource, amount):
    """Record usage (admins too, so their use can be seen)."""
    if amount <= 0:
        return
    k = _key(resource, user)
    try:
        r = _valkey()
        if r is not None:
            if r.incrby(k, amount) == amount:
                r.expire(k, 2 * 86400)
            return
    except Exception as ex:
        log.warning('Valkey unavailable, counting usage in process: %s', ex)
    _local[k] = _local.get(k, 0) + amount


def usage(user):
    admin = is_admin(user)
    return {'user': user, 'admin': admin,
            'usage': {r: {'used': used(user, r), 'limit': None if admin else LIMITS[r], 'unit': UNITS[r]}
                      for r in LIMITS}}
