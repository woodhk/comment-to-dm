"""Follower milestones and weekly posting badges, for the dashboard's Milestones page.

The bot takes a reading at most once an hour (`sync_milestones`, one of the tick's phases):
the account's follower count, and its feed posts since the last reading (the first reading
fetches every post, so badges count back through the whole history).

Instagram keeps no follower history, so a milestone already passed at the first reading is
earned without a date. After that, a milestone gets the time of the reading that first saw
the count at or past it. Milestones and badges stay earned: a dip in followers, or a quiet
week, never takes one away.

A week runs Monday to Sunday in the config's timezone. Stories aren't counted: the API
doesn't list them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from igdm.config import Config
    from igdm.db import Database
    from igdm.engine import Context

log = logging.getLogger(__name__)

SYNC_KEY = "milestones_synced_at"
SYNC_EVERY = timedelta(hours=1)
# Posts are re-read this far back from the newest one already stored, so a post Instagram
# lists late (a scheduled post, a slow upload) is still picked up.
POST_OVERLAP = timedelta(days=2)
RECENT_WEEKS = 12
# Icons the dashboard can draw for a badge (inline SVGs in dashboard.html).
BADGE_ICONS = ("flame", "bolt", "rocket", "star", "trophy", "crown", "medal", "target")


@dataclass(frozen=True)
class Badge:
    posts: int  # posts in one Monday-to-Sunday week that earn it
    name: str
    icon: str = "star"  # one of BADGE_ICONS


@dataclass(frozen=True)
class MilestonesConfig:
    followers: tuple[int, ...] = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000)
    badges: tuple[Badge, ...] = (
        Badge(3, "Consistent", "flame"),
        Badge(7, "Daily Grinder", "bolt"),
        Badge(14, "Content Machine", "rocket"),
    )


def parse_milestones(raw: Any) -> MilestonesConfig:
    """The optional [milestones] table. Anything left out keeps the default."""
    if raw is None:
        return MilestonesConfig()
    if not isinstance(raw, dict):
        raise ValueError("[milestones] must be a table")
    default = MilestonesConfig()
    followers = raw.get("followers", list(default.followers))
    if (not isinstance(followers, list) or not followers
            or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in followers)):
        raise ValueError("milestones.followers must be a list of follower counts, like [1000, 5000]")
    if len(set(followers)) != len(followers):
        raise ValueError("milestones.followers has the same number twice")
    badges = default.badges
    if "badges" in raw:
        items = raw["badges"]
        if not isinstance(items, list) or not items:
            raise ValueError("[[milestones.badges]] needs at least one badge")
        badges = tuple(_badge(b, i) for i, b in enumerate(items))
        counts = [b.posts for b in badges]
        if len(set(counts)) != len(counts):
            raise ValueError("two [[milestones.badges]] have the same number of posts")
    return MilestonesConfig(tuple(sorted(followers)), tuple(sorted(badges, key=lambda b: b.posts)))


def _badge(b: Any, i: int) -> Badge:
    where = f"[[milestones.badges]] #{i + 1}"
    if not isinstance(b, dict):
        raise ValueError(f"{where} must be a table")
    posts, name, icon = b.get("posts"), b.get("name"), b.get("icon", "star")
    if not isinstance(posts, int) or isinstance(posts, bool) or posts < 1:
        raise ValueError(f"{where}: posts must be a whole number of posts a week, like 3")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}: needs a name")
    if icon not in BADGE_ICONS:
        raise ValueError(f"{where}: icon must be one of {', '.join(BADGE_ICONS)}")
    return Badge(posts, name.strip(), icon)


# -- the bot's hourly reading

def sync_milestones(ctx: Context) -> int:
    """Read the follower count and new posts, at most once an hour. Returns the number of
    milestones newly reached."""
    db, now = ctx.db, ctx.now()
    last = db.kv_get(SYNC_KEY)
    if last and now - datetime.fromisoformat(last) < SYNC_EVERY:
        return 0
    # Recorded first, so an error (say, a missing permission) is retried hourly, not every tick.
    db.kv_set(SYNC_KEY, now.isoformat())
    before = db.latest_followers()
    followers = ctx.ig.follower_count()
    db.record_followers(now.astimezone(ctx.config.tz).date().isoformat(), followers, now)
    reached = db.milestones_reached()
    new = 0
    for m in ctx.config.milestones.followers:
        if m <= followers and m not in reached:
            # Already past it at the reading before (or there was none): no date to give.
            dated = before is not None and before < m
            db.mark_milestone(m, now if dated else None)
            new += dated
            log.info("milestone: %s followers reached", m)
    newest = db.newest_post()
    db.add_posts(ctx.ig.list_posts(since=newest - POST_OVERLAP if newest else None))
    return new


# -- what the dashboard shows

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _streak(hit: list[bool]) -> int:
    n = 0
    for h in reversed(hit):
        if not h:
            break
        n += 1
    return n


def milestones_state(db: Database, cfg: Config, now: datetime) -> dict:
    mc, tz = cfg.milestones, cfg.tz
    synced = db.kv_get(SYNC_KEY)
    followers = db.latest_followers()
    reached = db.milestones_reached()

    ms, prev = [], 0
    next_m = None
    for m in mc.followers:
        earned = m in reached
        if earned:
            progress = 1.0
        elif followers is None:
            progress = 0.0
        else:
            progress = max(0.0, min(1.0, (followers - prev) / (m - prev)))
        at = reached.get(m)
        ms.append({"followers": m, "earned": earned, "reached_at": at.isoformat() if at else None,
                   "progress": round(progress, 4)})
        if not earned and next_m is None:
            next_m = {"followers": m, "to_go": max(0, m - (followers or 0))}
        prev = m

    today = now.astimezone(tz).date()
    this_week = _monday(today)
    counts: dict[date, int] = {}
    for t in db.post_times():
        wk = _monday(t.astimezone(tz).date())
        counts[wk] = counts.get(wk, 0) + 1
    first = min(counts, default=this_week)
    weeks = []
    wk = min(first, this_week)
    while wk <= this_week:
        weeks.append((wk, counts.get(wk, 0)))
        wk += timedelta(weeks=1)

    def top_badge(n: int) -> Badge | None:
        return next((b for b in reversed(mc.badges) if n >= b.posts), None)

    badges = []
    for b in mc.badges:
        won = [w for w, n in weeks if n >= b.posts]
        badges.append({"posts": b.posts, "name": b.name, "icon": b.icon, "times": len(won),
                       "last_week": won[-1].isoformat() if won else None})

    lowest = mc.badges[0].posts
    hit = [n >= lowest for _, n in weeks]
    # This week only adds to the streak once it's hit; until Sunday it can't break it.
    streak = _streak(hit) if hit and hit[-1] else _streak(hit[:-1])
    best = 0
    run = 0
    for h in hit:
        run = run + 1 if h else 0
        best = max(best, run)

    n_now = weeks[-1][1]
    nxt = next((b for b in mc.badges if b.posts > n_now), None)
    have = top_badge(n_now)
    current = {
        "week_start": this_week.isoformat(),
        "posts": n_now,
        "days_left": 6 - today.weekday(),
        "badge": have.name if have else None,
        "next": ({"name": nxt.name, "icon": nxt.icon, "posts": nxt.posts, "to_go": nxt.posts - n_now}
                 if nxt else None),
    }
    recent = []
    for w, n in weeks[-RECENT_WEEKS:]:
        tb = top_badge(n)
        recent.append({"week_start": w.isoformat(), "posts": n,
                       "badge": tb.name if tb else None, "icon": tb.icon if tb else None})

    return {
        "synced_at": synced,
        "followers": followers,
        "milestones": ms,
        "next_milestone": next_m,
        "badges": badges,
        "streak": streak,
        "best_streak": best,
        "streak_badge": mc.badges[0].name,
        "this_week": current,
        "recent": recent,
        "total_posts": sum(counts.values()),
    }
