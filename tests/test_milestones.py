import random
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fakes import FakeClassifier, FakeInstagram, repo_config

from igdm.config import load_config
from igdm.dashboard import serve
from igdm.db import Database
from igdm.engine import Context
from igdm.instagram import GraphInstagram, InstagramError, Media
from igdm.milestones import Badge, MilestonesConfig, milestones_state, parse_milestones, sync_milestones

# Wednesday 4 Nov 2026, 12:00 UTC (the test config's timezone decides the local week)
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def make_ctx(ig, now=NOW, cfg=None):
    clock = {"now": now}
    ctx = Context(
        config=cfg or repo_config(), db=Database(":memory:"), ig=ig, jev=FakeClassifier(),
        now=lambda: clock["now"], sleep=lambda s: None, rng=random.Random(1),
        own_id="me1", own_username="alex", media_ids={},
    )
    return ctx, clock


def local(cfg, y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=cfg.tz).astimezone(timezone.utc)


# -- config

def test_defaults_when_section_missing():
    cfg = repo_config()
    assert cfg.milestones.followers == (1000, 5000, 10000, 25000, 50000, 100000)
    assert [(b.posts, b.name) for b in cfg.milestones.badges] == [
        (3, "Consistent"), (7, "Daily Grinder"), (14, "Content Machine")]


def test_own_numbers_from_config(tmp_path):
    src = (repo_config.__globals__["ROOT"] / "tests" / "data" / "config.toml").read_text()
    p = tmp_path / "config.toml"
    p.write_text(src + '\n[milestones]\nfollowers = [500, 200]\n\n'
                 '[[milestones.badges]]\nposts = 5\nname = "Five"\nicon = "crown"\n')
    mc = load_config(p).milestones
    assert mc.followers == (200, 500)
    assert mc.badges == (Badge(5, "Five", "crown"),)


@pytest.mark.parametrize("raw, msg", [
    ({"followers": []}, "list of follower counts"),
    ({"followers": [1000, "5k"]}, "list of follower counts"),
    ({"followers": [1000, 1000]}, "same number twice"),
    ({"badges": [{"posts": 0, "name": "x"}]}, "whole number"),
    ({"badges": [{"posts": 3}]}, "needs a name"),
    ({"badges": [{"posts": 3, "name": "a", "icon": "🔥"}]}, "icon must be one of"),
    ({"badges": [{"posts": 3, "name": "a"}, {"posts": 3, "name": "b"}]}, "same number of posts"),
])
def test_bad_config_refused(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_milestones(raw)


# -- the hourly reading

def test_first_reading_marks_passed_milestones_without_a_date():
    ig = FakeInstagram()
    ig.followers = 6200
    ctx, _ = make_ctx(ig)
    assert sync_milestones(ctx) == 0  # nothing newly reached: no date to give
    assert ctx.db.milestones_reached() == {1000: None, 5000: None}
    assert ctx.db.latest_followers() == 6200


def test_later_reading_dates_a_newly_crossed_milestone():
    ig = FakeInstagram()
    ig.followers = 9990
    ctx, clock = make_ctx(ig)
    sync_milestones(ctx)
    ig.followers = 10_004
    clock["now"] = NOW + timedelta(hours=1)
    assert sync_milestones(ctx) == 1
    assert ctx.db.milestones_reached()[10_000] == NOW + timedelta(hours=1)


def test_milestones_stay_earned_after_a_dip():
    ig = FakeInstagram()
    ig.followers = 1200
    ctx, clock = make_ctx(ig)
    sync_milestones(ctx)
    ig.followers = 950
    clock["now"] = NOW + timedelta(hours=2)
    sync_milestones(ctx)
    assert 1000 in ctx.db.milestones_reached()
    s = milestones_state(ctx.db, ctx.config, clock["now"])
    assert s["followers"] == 950 and s["milestones"][0]["earned"]


def test_reads_at_most_hourly_even_after_an_error():
    ig = FakeInstagram()
    ig.fail_followers = InstagramError(400, 10, "permission")
    ctx, clock = make_ctx(ig)
    with pytest.raises(InstagramError):
        sync_milestones(ctx)
    ig.fail_followers = None
    clock["now"] = NOW + timedelta(minutes=30)
    assert sync_milestones(ctx) == 0 and ctx.db.latest_followers() is None
    clock["now"] = NOW + timedelta(minutes=61)
    sync_milestones(ctx)
    assert ctx.db.latest_followers() == 0


def test_posts_read_in_full_once_then_from_the_newest():
    ig = FakeInstagram()
    ig.posts = [Media("p1", NOW - timedelta(days=30)), Media("p2", NOW - timedelta(days=1)),
                Media("p3", None)]
    ctx, clock = make_ctx(ig)
    sync_milestones(ctx)
    assert len(ctx.db.post_times()) == 2  # a post without a time is skipped
    ig.posts.append(Media("p4", NOW + timedelta(minutes=30)))
    clock["now"] = NOW + timedelta(hours=1)
    sync_milestones(ctx)
    assert ig.post_reads == [None, NOW - timedelta(days=3)]
    assert len(ctx.db.post_times()) == 3


def test_tick_runs_the_reading():
    from igdm.engine import tick
    ig = FakeInstagram()
    ig.followers = 1500
    ctx, _ = make_ctx(ig)
    assert tick(ctx)["milestones"] == 0
    assert ctx.db.latest_followers() == 1500


# -- the page's numbers

def _state_with_posts(per_week: list[int], followers=None):
    """per_week: posts in each week, oldest first, ending with the current week."""
    cfg = repo_config()
    db = Database(":memory:")
    this_monday = local(cfg, 2026, 11, 2)  # NOW's local week starts Monday 2 Nov
    for i, n in enumerate(reversed(per_week)):
        monday = this_monday - timedelta(weeks=i)
        db.add_posts(Media(f"w{i}-{k}", monday + timedelta(hours=k)) for k in range(n))
    if followers is not None:
        db.record_followers("2026-11-04", followers, NOW)
    return milestones_state(db, cfg, NOW)


def test_badges_counted_per_week():
    s = _state_with_posts([3, 8, 1, 14, 2])
    times = {b["name"]: b["times"] for b in s["badges"]}
    assert times == {"Consistent": 3, "Daily Grinder": 2, "Content Machine": 1}
    assert s["total_posts"] == 28


def test_streak_keeps_going_until_the_week_is_over():
    s = _state_with_posts([0, 3, 4, 1])  # this week isn't hit yet, but it isn't over
    assert s["streak"] == 2 and s["best_streak"] == 2
    s = _state_with_posts([0, 3, 4, 3])
    assert s["streak"] == 3
    s = _state_with_posts([3, 3, 3, 0, 5, 0])
    assert s["streak"] == 1 and s["best_streak"] == 3


def test_this_week_shows_the_next_badge():
    s = _state_with_posts([4])
    w = s["this_week"]
    assert w["posts"] == 4 and w["badge"] == "Consistent"
    assert w["next"]["name"] == "Daily Grinder" and w["next"]["to_go"] == 3
    assert w["days_left"] == 4  # Wednesday: Thursday to Sunday left
    assert _state_with_posts([15])["this_week"]["next"] is None


def test_quiet_weeks_between_posts_are_listed():
    s = _state_with_posts([2, 0, 0, 7])
    assert [w["posts"] for w in s["recent"]] == [2, 0, 0, 7]
    assert s["recent"][-1]["badge"] == "Daily Grinder"


def test_progress_toward_the_next_milestone():
    s = _state_with_posts([], followers=3000)
    first, second = s["milestones"][:2]
    assert not first["earned"] and first["progress"] == 1.0  # past it, but not read by the bot yet
    s2 = _state_with_posts([], followers=600)
    assert s2["milestones"][0]["progress"] == 0.6
    assert s2["next_milestone"] == {"followers": 1000, "to_go": 400}


def test_empty_before_the_first_reading():
    s = _state_with_posts([])
    assert s["followers"] is None and s["synced_at"] is None
    assert s["streak"] == 0 and s["this_week"]["posts"] == 0
    assert s["next_milestone"] == {"followers": 1000, "to_go": 1000}


# -- the real client and the dashboard

def test_graph_client_reads_followers_and_stops_at_since():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"data": [
                {"id": "a", "timestamp": "2026-11-04T10:00:00+0000"},
                {"id": "b", "timestamp": "2026-11-01T10:00:00+0000"},
                {"id": "c", "timestamp": "2026-10-01T10:00:00+0000"},
            ], "paging": {"next": "https://graph.facebook.com/v26.0/never"}})
        return httpx.Response(200, json={"followers_count": 1234, "id": "ig1"})

    ig = GraphInstagram("t", base_url="https://graph.facebook.com/v26.0", ig_id="ig1",
                        page_id="pg1", transport=httpx.MockTransport(handler))
    assert ig.follower_count() == 1234
    posts = ig.list_posts(since=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert [p.id for p in posts] == ["a", "b"]
    assert "/v26.0/never" not in seen  # stopped before the next page


def test_dashboard_serves_milestones(tmp_path):
    cfg = replace(repo_config(), db_path=tmp_path / "d.sqlite",
                  milestones=MilestonesConfig(followers=(100,), badges=(Badge(1, "One"),)))
    db = Database(cfg.db_path)
    db.record_followers("2026-11-04", 150, NOW)
    db.mark_milestone(100, None)
    server = serve(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = httpx.get(f"http://127.0.0.1:{server.server_address[1]}/api/milestones")
        s = r.json()
        assert r.status_code == 200 and s["followers"] == 150
        assert s["milestones"] == [{"followers": 100, "earned": True, "reached_at": None, "progress": 1.0}]
        assert s["badges"][0]["name"] == "One"
    finally:
        server.shutdown()
        server.server_close()
