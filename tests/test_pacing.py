import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from igdm.pacing import dm_due_at, in_quiet_hours, is_expired, under_hourly_cap, uniform_delay, window_open

SG = ZoneInfo("Asia/Singapore")
T0 = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)


def test_uniform_delay_bounds():
    rng = random.Random(1)
    for _ in range(100):
        assert timedelta(seconds=5) <= uniform_delay((5, 10), rng) <= timedelta(seconds=10)


def test_dm_due_at_range():
    rng = random.Random(3)
    for _ in range(1000):
        d = dm_due_at(T0, (50, 70), rng) - T0
        assert timedelta(seconds=50) <= d <= timedelta(seconds=70)


def _hk(h, m):
    return datetime(2026, 11, 3, h, m, tzinfo=SG)


def test_quiet_hours_hkt():
    s, e = time(0, 0), time(8, 0)
    assert in_quiet_hours(_hk(23, 59), SG, s, e) is False
    assert in_quiet_hours(_hk(0, 0), SG, s, e) is True
    assert in_quiet_hours(_hk(7, 59), SG, s, e) is True
    assert in_quiet_hours(_hk(8, 0), SG, s, e) is False


def test_quiet_hours_converts_utc_to_local():
    assert in_quiet_hours(datetime(2026, 11, 2, 16, 30, tzinfo=UTC), SG, time(0), time(8)) is True


def test_quiet_hours_wraparound():
    s, e = time(22, 0), time(6, 0)
    assert in_quiet_hours(_hk(23, 0), SG, s, e) is True
    assert in_quiet_hours(_hk(5, 59), SG, s, e) is True
    assert in_quiet_hours(_hk(6, 0), SG, s, e) is False
    assert in_quiet_hours(_hk(12, 0), SG, s, e) is False
    assert in_quiet_hours(_hk(22, 0), SG, s, e) is True


def test_quiet_hours_equal_means_never():
    assert in_quiet_hours(_hk(3, 0), SG, time(4), time(4)) is False


def test_hourly_cap():
    now = T0
    recent = [now - timedelta(minutes=59, seconds=i) for i in range(25)]
    assert under_hourly_cap(recent, now, 25) is False
    older = [now - timedelta(minutes=61)] + [now - timedelta(minutes=5)] * 24
    assert under_hourly_cap(older, now, 25) is True
    assert under_hourly_cap([now - timedelta(minutes=60)] * 25, now, 25) is True  # boundary excluded


def test_is_expired():
    assert is_expired(T0, T0 + timedelta(hours=166), 167) is False
    assert is_expired(T0, T0 + timedelta(hours=167), 167) is True


def test_window_open():
    assert window_open(T0, T0 + timedelta(hours=23, minutes=59)) is True
    assert window_open(T0, T0 + timedelta(hours=24)) is False


def test_typing_time_fits_the_message():
    from igdm.pacing import TYPING_BOUNDS, typing_seconds
    rng = random.Random(4)
    short = [typing_seconds("ok", rng) for _ in range(200)]
    long = [typing_seconds("x" * 400, rng) for _ in range(200)]
    mid = [typing_seconds("perfect, what's the best email to send it to?", rng) for _ in range(200)]
    lo, hi = TYPING_BOUNDS
    assert all(lo <= s <= hi for s in short + long + mid)
    assert sum(short) / 200 < sum(mid) / 200 < sum(long) / 200
