# Resource use and what it would cost, reported to the admins on Telegram every morning (and with /usage), with alerts
# when the month (calendar month, Vietnam time) is heading past the budget. Everything is meant to stay in free tiers,
# so the budget is 0 by default: any billable amount is over it.
#
#   KataGo on Modal (GPU L4): the only resource billed by the second. The app times each search; the GPU is up from a
#     request's start until SCALEDOWN seconds after its end (Modal's idle window), overlapping requests counted once.
#     Cost = GPU hours × price, against Modal's monthly free credit.
#   LLM providers, speech: free tiers, so no cost; counted per provider with their rate-limit errors (429), which mean
#     a free quota running out.
#   Oracle VM: Always Free A1 within its monthly OCPU / memory hours; the shape is read from the instance metadata.
#
# Counters are kept in memory and written every minute to USAGE_DIR/<YYYY-MM>.json ({date: {counter: value}}); other
# processes using KataGo (python -m joseki build…) set SOURCE and write <YYYY-MM>.<source>.json, and the report adds
# every file of the month: their use is one-off (counted, not projected). The month's first count is kept (_meta.since):
# the projection runs from there, not from the 1st, when counting started in the middle of a month; with less than
# MIN_DAYS of counting it is shown as unreliable and raises no alert (spending past the budget still does).
#
# Modal's real figure can be given from its dashboard (bot: /modal 16.16): the report then shows that amount plus the
# estimate of what was used after it. With a spend limit of $0 Modal stops every workload once the credit is spent:
# KataGo would stop until the next month, which is what the alerts are about.
#
#   USAGE_BUDGET_USD (0)  MODAL_FREE_CREDIT_USD (30)  MODAL_GPU_USD_PER_HOUR (0.80, L4; +10 % for its CPU / memory)
#   ORACLE_FREE_OCPU_HOURS (1500)  ORACLE_FREE_GB_HOURS (9000)  ORACLE_FREE_STORAGE_GB (200)

import calendar
import json
import logging
import os
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sessions

log = logging.getLogger('usage')

ROOT = Path(os.environ.get('USAGE_DIR', sessions.ROOT.parent / 'usage'))
VN = timezone(timedelta(hours=7))
SCALEDOWN = 120          # seconds a Modal container stays up after its last request (katago/modal_katago.py)
BUDGET = float(os.environ.get('USAGE_BUDGET_USD', '0'))
MODAL_FREE = float(os.environ.get('MODAL_FREE_CREDIT_USD', '30'))
GPU_HOURLY = float(os.environ.get('MODAL_GPU_USD_PER_HOUR', '0.80')) * 1.1
ORACLE_OCPU_HOURS = float(os.environ.get('ORACLE_FREE_OCPU_HOURS', '1500'))
ORACLE_GB_HOURS = float(os.environ.get('ORACLE_FREE_GB_HOURS', '9000'))
ORACLE_STORAGE_GB = float(os.environ.get('ORACLE_FREE_STORAGE_GB', '200'))
LLM_429_ALERT = 10
MIN_DAYS = 2             # days of counting before a projection is trusted enough for an alert       # rate-limit errors a day before warning that a free quota runs out

SOURCE = 'app'           # whose counters this process writes (see above)
_lock = threading.Lock()
_months = {}             # 'YYYY-MM' -> {date: {counter: value}}
_dirty = set()
_gpu_until = [0.0]


def _day(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t, VN)


def _file(month, source=None):
    source = source or SOURCE
    return ROOT / (f'{month}.json' if source == 'app' else f'{month}.{source}.json')


def _month(month):
    if month not in _months:
        f = _file(month)
        _months[month] = (sessions._read(f) or {}) if f.is_file() else {}
    return _months[month]


def add(counter, amount=1.0, t=None):
    d = _day(t)
    with _lock:
        month = _month(d.strftime('%Y-%m'))
        meta = month.setdefault('_meta', {})
        meta['since'] = min(meta.get('since', d.timestamp()), d.timestamp())
        day = month.setdefault(d.strftime('%Y-%m-%d'), {})
        day[counter] = round(day.get(counter, 0) + amount, 3)
        _dirty.add(d.strftime('%Y-%m'))


def katago(start, end, visits=0):
    """A KataGo request (or warm-up) from `start` to `end`: its share of the GPU's up time."""
    with _lock:
        until = max(start, _gpu_until[0])
        up = max(0.0, end + SCALEDOWN - until)
        _gpu_until[0] = max(_gpu_until[0], end + SCALEDOWN)
    add('katago:requests', t=start)
    add('katago:visits', visits, t=start)
    add('katago:gpu_seconds', up, t=start)


def flush():
    with _lock:
        todo = {m: json.loads(json.dumps(_months[m])) for m in _dirty}
        _dirty.clear()
    for month, data in todo.items():
        try:
            sessions._write(_file(month), data)
        except OSError as ex:
            log.warning('usage not written: %s', ex)


def days(month):
    """{date: {counter: value}} of the month, every process's counters added, plus '_meta': {'since': first count}."""
    with _lock:
        out = json.loads(json.dumps(_month(month)))
    for f in ROOT.glob(f'{month}*.json') if ROOT.is_dir() else []:
        if f == _file(month):
            continue
        other = sessions._read(f) or {}
        for key, day in other.items():
            if key == '_meta':
                since = day.get('since')
                if since:
                    out.setdefault('_meta', {})['since'] = min(out.get('_meta', {}).get('since', since), since)
                if 'actual' in day and day.get('actual_at', 0) > out.get('_meta', {}).get('actual_at', 0):
                    out['_meta'].update({k: day[k] for k in ('actual', 'actual_at', 'actual_gpu_seconds')})
                continue
            mine = out.setdefault(key, {})
            for k, v in day.items():
                if isinstance(v, (int, float)) and not k.startswith('_') and k != 'users':
                    mine[k] = round(mine.get(k, 0) + v, 3)
                    mine['oneoff:' + k] = round(mine.get('oneoff:' + k, 0) + v, 3)   # not projected
    return out


_shape = {}


def oracle_shape():
    """(shape, OCPUs, memory GB) of this VM from Oracle's instance metadata, or None elsewhere."""
    if 'v' not in _shape:
        try:
            req = urllib.request.Request('http://169.254.169.254/opc/v2/instance/', headers={'Authorization': 'Bearer Oracle'})
            with urllib.request.urlopen(req, timeout=2) as r:
                d = json.loads(r.read())
            _shape['v'] = (d['shape'], float(d['shapeConfig']['ocpus']), float(d['shapeConfig']['memoryInGBs']))
        except Exception:
            _shape['v'] = None
    return _shape['v']


def set_actual(usd, t=None):
    """Modal's month-to-date cost read on its dashboard: later reports count from it."""
    d = _day(t)
    month = d.strftime('%Y-%m')
    counted = _sum([v for k, v in days(month).items() if k != '_meta'], 'katago:gpu_seconds')
    with _lock:
        meta = _month(month).setdefault('_meta', {})
        meta.update(actual=float(usd), actual_at=d.timestamp(), actual_gpu_seconds=counted)
        _dirty.add(month)
    flush()


def _sum(day_list, prefix):
    return sum(v for d in day_list for k, v in d.items() if k.startswith(prefix) and isinstance(v, (int, float)))


def report(now=None, day=None):
    """-> (text for the admins, alerts). day: the day reported in detail (default: today so far)."""
    now = now if isinstance(now, datetime) else _day(now)   # a datetime, or a timestamp (None: now)
    day = day or now
    month = now.strftime('%Y-%m')
    data = days(month)
    n_days = calendar.monthrange(now.year, now.month)[1]
    first = datetime(now.year, now.month, 1, tzinfo=VN)
    since = max(first, datetime.fromtimestamp(data.get('_meta', {}).get('since', first.timestamp()), VN))
    counted = max((now - since).total_seconds() / 86400, 0.25)          # days of counting so far
    left = max((first.replace(day=n_days) + timedelta(days=1) - now).total_seconds() / 86400, 0)
    project = lambda x: x + x / counted * left                          # so far + the same pace to the month's end
    d = data.get(day.strftime('%Y-%m-%d'), {}) if day.strftime('%Y-%m') == month else days(day.strftime('%Y-%m')).get(day.strftime('%Y-%m-%d'), {})
    alerts = []

    # KataGo on Modal
    gpu_h_day = d.get('katago:gpu_seconds', 0) / 3600
    month_days = [v for k, v in data.items() if k != '_meta']
    gpu_h = _sum(month_days, 'katago:gpu_seconds') / 3600
    oneoff_h = _sum(month_days, 'oneoff:katago:gpu_seconds') / 3600
    proj_h = oneoff_h + project(gpu_h - oneoff_h)
    trusted = counted >= MIN_DAYS
    meta = data.get('_meta', {})
    actual = meta.get('actual') if meta.get('actual_at', 0) >= first.timestamp() else None
    cost, proj_cost = gpu_h * GPU_HOURLY, proj_h * GPU_HOURLY
    if actual is not None:   # Modal's own figure, plus the estimate of what came after it
        cost = actual + max(0.0, gpu_h - meta.get('actual_gpu_seconds', 0) / 3600) * GPU_HOURLY
        proj_cost = cost + (proj_h - gpu_h) * GPU_HOURLY
    billable, proj_billable = max(0.0, cost - MODAL_FREE), max(0.0, proj_cost - MODAL_FREE)
    lines = [f'📊 Go Scan · tài nguyên {"hôm nay" if day.date() == now.date() else "ngày"} {day:%d/%m} '
             f'(tháng {now:%m}: ngày {now.day}/{n_days})'
             + (f'\n   Số liệu tháng này đếm từ {since:%d/%m %H:%M}: dự kiến = đã dùng + cùng nhịp đó tới cuối tháng.'
                if since > first + timedelta(hours=1) else ''), '',
             f'🧠 KataGo (Modal GPU L4): {gpu_h_day:.2f} giờ GPU · {int(d.get("katago:requests", 0))} lượt gọi · '
             f'{int(d.get("katago:visits", 0)):,} lượt tìm'.replace(',', '.'),
             (f'   Modal thật (nhập lúc {datetime.fromtimestamp(meta["actual_at"], VN):%d/%m %H:%M}): ${actual:.2f} '
              f'+ ước tính sau đó → tháng này ≈ ${cost:.2f}, còn ≈ ${max(0.0, MODAL_FREE - cost):.2f} credit\n'
              if actual is not None else '')
             + f'   Đếm được tháng này {gpu_h:.2f} giờ'
             + ('' if actual is not None else f' ≈ ${cost:.2f}')
             + (f' (gồm {oneoff_h:.2f} giờ chạy một lần: dựng cây định thức…)' if oneoff_h else '')
             + f' → dự kiến cả tháng ≈ ${proj_cost:.2f}'
             + (f' (miễn phí ${MODAL_FREE:.0f}: {proj_cost / MODAL_FREE:.0%})' if MODAL_FREE else '')
             + ('' if trusted else f' · mới đếm {counted:.1f} ngày, dự kiến chưa tin cậy')]
    cpu, fallback, failures = (int(d.get(k, 0)) for k in ('katago:cpu_requests', 'katago:cpu_fallback', 'katago:gpu_failures'))
    lines.append(f'   KataGo CPU của VM: {cpu} lượt đánh với AI · {fallback} lượt dự phòng khi GPU lỗi ({failures} lần lỗi)')
    if failures:
        alerts.append(f'⚠️ GPU Modal lỗi {failures} lần hôm {day:%d/%m}, app đã tạm chạy KataGo bằng CPU của VM ({fallback} '
                      'lượt, chậm và ít lượt tìm hơn). Nếu do hết credit Modal thì sẽ kéo dài tới đầu tháng sau.')
    week = [data.get((now - timedelta(days=k)).strftime('%Y-%m-%d'), {}).get('katago:gpu_seconds', 0) for k in range(1, 8)]
    before = [days((now - timedelta(days=k)).strftime('%Y-%m')).get((now - timedelta(days=k)).strftime('%Y-%m-%d'), {})
              .get('katago:gpu_seconds', 0) for k in range(8, 15)]
    if sum(before) > 0 and sum(week) >= 1.5 * sum(before) and sum(week) / 7 * n_days / 3600 * GPU_HOURLY >= 0.5 * MODAL_FREE:
        alerts.append(f'📈 Giờ GPU 7 ngày qua tăng {sum(week) / sum(before) - 1:.0%} so với 7 ngày trước: theo đà này '
                      f'cả tháng ≈ ${sum(week) / 7 * n_days / 3600 * GPU_HOURLY:.2f}.')
    if billable > BUDGET:
        alerts.append(f'🚨 Hết credit Modal: tháng này ≈ ${cost:.2f}, quá ${MODAL_FREE:.0f} miễn phí. Với spend limit $0, '
                      f'KataGo đã hoặc sắp bị Modal dừng tới hết tháng (ngân sách ${BUDGET:.0f}).')
    elif proj_billable > BUDGET and trusted:
        rate = (proj_cost - cost) / left if left else 0
        out_day = now + timedelta(days=(MODAL_FREE - cost) / rate) if rate > 0 and cost < MODAL_FREE else None
        alerts.append(f'⚠️ Sắp hết credit Modal: dự kiến ≈ ${proj_cost:.2f} cả tháng, quá ${MODAL_FREE:.0f} miễn phí'
                      + (f', hết khoảng ngày {out_day:%d/%m}' if out_day else '')
                      + '. Với spend limit $0, Modal sẽ dừng KataGo tới hết tháng (gợi ý, review, đấu AI ngừng chạy). '
                      'Giảm lượt tìm (ROOT_VISITS, USER_DAILY_KATAGO_VISITS) hoặc chuyển KataGo về CPU của VM.')
    elif MODAL_FREE and proj_cost >= 0.8 * MODAL_FREE and trusted:
        alerts.append(f'⚠️ Modal dự kiến dùng {proj_cost / MODAL_FREE:.0%} phần miễn phí tháng này.')

    # LLMs and speech
    llm = {k[4:]: v for k, v in d.items() if k.startswith('llm:') and k.count(':') == 2}
    by_provider = {}
    for k, v in llm.items():
        by_provider[k.split(':')[0]] = by_provider.get(k.split(':')[0], 0) + v
    r429 = {k[8:]: v for k, v in d.items() if k.startswith('llm_429:')}
    lines += ['', f'🤖 AI (gói miễn phí): {int(sum(llm.values()))} lượt trả lời'
              + (f' ({", ".join(f"{p} {int(v)}" for p, v in sorted(by_provider.items(), key=lambda x: -x[1]))})' if llm else '')
              + f' · {int(_sum([d], "llm_tokens:")):,} token'.replace(',', '.'),
              f'   Hết lượt miễn phí (429): {int(sum(r429.values()))}'
              + (f' ({", ".join(f"{p} {int(v)}" for p, v in r429.items())})' if r429 else '')
              + f' · không model nào trả lời: {int(d.get("llm:unavailable", 0))} · câu hỏi chat {int(d.get("chat:requests", 0))}'
              + f', chậm > 60 s: {int(d.get("chat:slow", 0))}',
              f'   Giọng nói: {int(_sum([d], "stt:"))} lần nghe · {int(_sum([d], "tts:"))} đoạn đọc']
    if sum(r429.values()) >= LLM_429_ALERT:
        alerts.append(f'⚠️ {int(sum(r429.values()))} lần hết lượt miễn phí (429) hôm {day:%d/%m}: các model dự phòng '
                      'đang phải gánh, câu trả lời có thể chậm hoặc kém hơn.')
    if d.get('llm:unavailable'):
        alerts.append(f'⚠️ {int(d["llm:unavailable"])} câu hỏi không model nào trả lời được hôm {day:%d/%m}.')

    # Oracle VM
    shape = oracle_shape()
    if shape:
        name, ocpus, mem = shape
        ocpu_h, gb_h = ocpus * 24 * n_days, mem * 24 * n_days
        lines += ['', f'☁️ Oracle VM {name} {ocpus:g} OCPU / {mem:g} GB: cả tháng {ocpu_h:.0f}/{ORACLE_OCPU_HOURS:.0f} '
                  f'OCPU-giờ, {gb_h:.0f}/{ORACLE_GB_HOURS:.0f} GB-giờ miễn phí ({max(ocpu_h / ORACLE_OCPU_HOURS, gb_h / ORACLE_GB_HOURS):.0%})']
        if ocpu_h > ORACLE_OCPU_HOURS or gb_h > ORACLE_GB_HOURS:
            alerts.append('🚨 Cấu hình VM vượt giờ miễn phí của Oracle tháng này: giảm OCPU / RAM.')
    try:
        du = shutil.disk_usage(ROOT.parent if ROOT.parent.exists() else '/')
        lines.append(f'   Ổ đĩa {du.used / 1e9:.1f}/{du.total / 1e9:.0f} GB ({du.used / du.total:.0%}) · '
                     f'dung lượng miễn phí {ORACLE_STORAGE_GB:.0f} GB')
        if du.used / du.total > 0.8:
            alerts.append(f'⚠️ Ổ đĩa đã dùng {du.used / du.total:.0%}.')
    except OSError:
        pass
    lines.append(f'   Người dùng hôm {day:%d/%m}: {int(d.get("users", 0))}')

    total = billable
    lines += ['', f'💵 Chi phí tháng {now:%m}: hiện ≈ ${total:.2f} · dự kiến ≈ ${proj_billable:.2f} '
              f'(ngân sách ${BUDGET:.0f}) {"✅" if proj_billable <= BUDGET else "❌" if trusted else "❔ chưa đủ dữ liệu"}']
    if alerts:
        lines += ['', *alerts]
    return '\n'.join(x for x in lines if x is not None), alerts


def seen_user(user):
    """Count each user once a day."""
    d = _day()
    key = d.strftime('%Y-%m-%d')
    with _lock:
        day = _month(d.strftime('%Y-%m')).setdefault(key, {})
        users = day.setdefault('_users', [])
        if user in users:
            return
        users.append(user)
        day['users'] = len(users)
        _dirty.add(d.strftime('%Y-%m'))


def _mark(key):
    """True the first time `key` is marked today (kept in the counters' file: deploys restart the app)."""
    d = _day()
    with _lock:
        day = _month(d.strftime('%Y-%m')).setdefault(d.strftime('%Y-%m-%d'), {})
        done = day.setdefault('_sent', [])
        if key in done:
            return False
        done.append(key)
        _dirty.add(d.strftime('%Y-%m'))
    return True


def start(send, hour=8):
    """Background: write the counters every minute; with `send`, from `hour` (Vietnam time) send yesterday's report
    once a day, and every hour each new budget alert as soon as it appears (once a day each)."""
    def loop():
        checked = None
        while True:
            time.sleep(60)
            now = _day()
            if send is None:
                flush()
                continue
            try:
                if now.hour >= hour and _mark('report'):
                    send(report(now, now - timedelta(days=1))[0])
                if checked != now.hour:
                    checked = now.hour
                    new = [a for a in report(now)[1] if a[:2] in ('🚨', '⚠️') and _mark(a[:40])]
                    if new:
                        send('Cảnh báo Go Scan:\n\n' + '\n'.join(new))
            except Exception as ex:
                log.warning('usage report failed: %s', ex)
            flush()
    threading.Thread(target=loop, daemon=True, name='usage').start()
