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
    for d in range(1, 7):               # 3 GPU hours a day: ~$93 for October, past the $30 credit (6 days counted)
        usage.add('katago:gpu_seconds', 3 * 3600, t=ts(d))
    text, alerts = usage.report(now=ts(7, 0), day=datetime.fromtimestamp(ts(6), usage.VN))
    assert 'dự kiến cả tháng' in text and '❌' in text
    assert any(a.startswith('⚠️ Sắp hết credit') for a in alerts)
    for d in range(7, 12):
        usage.add('katago:gpu_seconds', 3 * 3600, t=ts(d))
    text, alerts = usage.report(now=ts(12, 0))
    assert any(a.startswith('🚨 Hết credit') for a in alerts)


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


def test_report_takes_the_loop_s_datetimes():
    """The morning loop passes datetimes (a timestamp-only report crashed it: no report was ever sent)."""
    now = datetime(2026, 10, 7, 8, 1, tzinfo=usage.VN)
    usage.add('katago:gpu_seconds', 600, t=ts(6))
    text, _ = usage.report(now, now - __import__('datetime').timedelta(days=1))
    assert 'ngày 06/10' in text and '0.17 giờ GPU' in text


def test_projection_runs_from_the_first_count_and_other_processes_add_up(monkeypatch):
    monkeypatch.setattr(usage, 'GPU_HOURLY', 1.0)
    monkeypatch.setattr(usage, 'MODAL_FREE', 30.0)
    usage.add('katago:gpu_seconds', 3600, t=ts(7, 9))   # counting starts on the 7th at 9:00: 1 h by the 8th at 9:00
    text, _ = usage.report(now=ts(8, 9))
    assert 'đếm từ 07/10 09:00' in text and 'chưa tin cậy' in text   # one day of counting only
    assert 'dự kiến cả tháng ≈ $24.6' in text           # 1 h a day: 1 h + 23.6 days left (not 1 h / 7.4 days × 31)
    # a joseki build in its own process, written to its own file
    usage.flush()
    usage.SOURCE = 'joseki'
    try:
        monkeypatch.setattr(usage, '_months', {})
        usage.add('katago:gpu_seconds', 1800, t=ts(8, 8))
        usage.flush()
    finally:
        usage.SOURCE = 'app'
    monkeypatch.setattr(usage, '_months', {})
    assert (usage.ROOT / '2026-10.joseki.json').is_file()
    day = usage.days('2026-10')['2026-10-08']
    assert day['katago:gpu_seconds'] == 1800
    text, _ = usage.report(now=ts(8, 9))
    assert 'tháng này 1.50 giờ' in text and 'gồm 0.50 giờ chạy một lần' in text
    assert 'dự kiến cả tháng ≈ $25.1' in text           # the build is not projected: 0.5 + 24.6


def test_no_projection_alert_on_the_first_day(monkeypatch):
    monkeypatch.setattr(usage, 'GPU_HOURLY', 1.0)
    monkeypatch.setattr(usage, 'MODAL_FREE', 30.0)
    usage.add('katago:gpu_seconds', 3 * 3600, t=ts(7, 9))   # a busy first morning
    text, alerts = usage.report(now=ts(7, 18))
    assert not alerts and 'chưa đủ dữ liệu' in text


def test_modal_figure_from_the_dashboard(monkeypatch):
    monkeypatch.setattr(usage, 'GPU_HOURLY', 1.0)
    monkeypatch.setattr(usage, 'MODAL_FREE', 30.0)
    usage.add('katago:gpu_seconds', 3600, t=ts(5, 9))            # counted: 1 h ≈ $1
    usage.set_actual(16.16, t=ts(7, 12))                         # the dashboard says $16.16 so far
    usage.add('katago:gpu_seconds', 2 * 3600, t=ts(8, 9))        # 2 h after it
    text, alerts = usage.report(now=ts(9, 12))
    assert 'Modal thật' in text and '$16.16' in text and 'tháng này ≈ $18.16' in text and 'còn ≈ $11.84 credit' in text
    assert any('spend limit $0' in a and 'hết khoảng ngày' in a for a in alerts)
