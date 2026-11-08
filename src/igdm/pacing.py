from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def uniform_delay(bounds: tuple[float, float], rng: random.Random) -> timedelta:
    return timedelta(seconds=rng.uniform(bounds[0], bounds[1]))


def dm_due_at(commented_at: datetime, seconds: tuple[float, float], rng: random.Random) -> datetime:
    return commented_at + uniform_delay(seconds, rng)


# Before a reply the bot marks their message Seen, pauses, then shows "typing…" for about as
# long as a quick phone typist would take to write it.
SEEN_PAUSE = (2.0, 4.0)  # seconds between Seen and typing…
TYPING_CHARS_PER_SECOND = 12.0
TYPING_BOUNDS = (3.0, 9.0)  # never shorter or longer than this


def typing_seconds(text: str, rng: random.Random) -> float:
    base = 1.5 + len(text) / TYPING_CHARS_PER_SECOND
    lo, hi = TYPING_BOUNDS
    return min(hi, max(lo, base * rng.uniform(0.8, 1.2)))


def in_quiet_hours(now: datetime, tz: ZoneInfo, start: time, end: time) -> bool:
    """True if local time of `now` is in [start, end); wraps midnight; start == end means never."""
    if start == end:
        return False
    t = now.astimezone(tz).time().replace(tzinfo=None)
    if start < end:
        return start <= t < end
    return t >= start or t < end


def under_hourly_cap(sent_times: list[datetime], now: datetime, cap: int) -> bool:
    cutoff = now - timedelta(minutes=60)
    return sum(1 for t in sent_times if cutoff < t <= now) < cap


def is_expired(commented_at: datetime, now: datetime, max_age_hours: int) -> bool:
    return now - commented_at >= timedelta(hours=max_age_hours)


def window_open(last_user_msg_at: datetime, now: datetime) -> bool:
    return now - last_user_msg_at < timedelta(hours=24)
