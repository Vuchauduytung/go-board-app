import json
from datetime import datetime

import pytest

import usage


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, 'ROOT', tmp_path)
    monkeypatch.setattr(usage, '_months', {})
    monkeypatch.setattr(usage, '_dirty', set())
    monkeypatch.setattr(usage, '_gpu_until', [0.0])
    monkeypatch.setattr(usage, 'oracle_shape', lambda: ('VM.Standard.A1.Flex', 2.0, 12.0))


def ts(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=usage.VN).timestamp()


def test_gpu_time_counts_overlaps_once_and_the_idle_window():
    t = ts(5)
    usage.katago(t, t + 10)              # up 10 s + 120 s idle
    usage.katago(t + 20, t + 25)         # inside that window: only the 15 s it moves the window's end
    usage.katago(t + 1000, t + 1030)     # the GPU had stopped: 30 + 120 again
    day = usage.days('2026-10')['2026-10-05']
    assert day['katago:gpu_seconds'] == 130 + 15 + 150 and day['katago:requests'] == 3


def test_budget_alerts_and_projection(monkeypatch):
    monkeypatch.setattr(usage, 'MODAL_FREE', 30.0)
    monkeypatch.setattr(usage, 'GPU_HOURLY', 1.0)
    for d in range(1, 7):               # 3 GPU hours a day: ~$93 for October, past the $30 credit
        usage.add('katago:gpu_seconds', 3 * 3600, t=ts(d))
    text, alerts = usage.report(now=ts(7, 0), day=datetime.fromtimestamp(ts(6), usage.VN))
    assert 'dự kiến cả tháng' in text and '❌' in text
    assert any(a.startswith('⚠️ Sắp vượt') for a in alerts)
    for d in range(7, 12):
        usage.add('katago:gpu_seconds', 3 * 3600, t=ts(d))
    text, alerts = usage.report(now=ts(12, 0))
    assert any(a.startswith('🚨 Đã vượt') for a in alerts)


def test_quiet_month_is_free_and_counters_are_written(monkeypatch):
    usage.add('llm:gemini:gemini-3.5-flash', t=ts(3))
    usage.add('llm_429:gemini', t=ts(3))
    usage.seen_user('a@x.com'); usage.seen_user('a@x.com')
    text, alerts = usage.report(now=ts(3, 20))
    assert '$0.00' in text and '✅' in text and not alerts and 'gemini 1' in text
    usage.flush()
    saved = json.loads((usage.ROOT / '2026-10.json').read_text())
    assert saved['2026-10-03']['llm_429:gemini'] == 1
    assert usage._mark('report') and not usage._mark('report')   # the morning report goes once a day
