import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeClassifier, FakeInstagram, repo_config

from igdm.config import load_config
from igdm.db import Database, Request, Status
from igdm.engine import Context, ingest_comments, send_due_dm
from igdm.instagram import Comment, InstagramError, RateLimited
from igdm.messages import render

ROOT = Path(__file__).resolve().parent.parent
SGT = ZoneInfo("Asia/Singapore")
T9 = datetime(2026, 11, 3, 9, 0, tzinfo=SGT)  # after the cutoff, outside quiet hours
MEDIA = "m1"


class Clock:
    def __init__(self, t, ig):
        self.ig = ig
        self.set(t)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = t.astimezone(timezone.utc)
        self.ig.now = self.t

    def advance(self, **kw):
        self.set(self.t + timedelta(**kw))


class Env:
    def __init__(self, mode="full", pacing=None):
        cfg = repo_config()
        if pacing:
            cfg = replace(cfg, pacing=replace(cfg.pacing, **pacing))
        self.cfg = replace(cfg, mode=mode)
        self.db = Database(":memory:")
        self.ig = FakeInstagram("me1", "alex")
        self.jev = FakeClassifier(default_comment=0.95)
        self.clock = Clock(T9, self.ig)
        self.sleeps = []
        self.ctx = Context(
            config=self.cfg, db=self.db, ig=self.ig, jev=self.jev, now=self.clock,
            sleep=self.sleeps.append, rng=random.Random(1), own_id="me1",
            own_username="alex", media_ids={"tokyo": MEDIA},
            comment_poll_interval=timedelta(0),  # these tests poll comments at will
        )

    def comment(self, cid, text="Tokyo", username="sarah", from_id=None, at=None):
        c = Comment(cid, text, username, from_id, at or self.clock.t)
        self.ig.comments.setdefault(MEDIA, []).append(c)
        return c

    def dm1(self, name="sarah"):
        return render(self.cfg.messages.dm1, name=name, keyword="Tokyo")

    def privates(self):
        return [s for s in self.ig.sent if s[0] == "private"]


@pytest.fixture
def env():
    return Env()


def other_row(cid, username, status, **kw):
    return Request(
        comment_id=cid, reel_key="other", media_id="m2", username=username,
        commenter_id=None, text="guide", commented_at=T9 - timedelta(days=1), status=status, **kw,
    )


# ---------------------------------------------------------------- intake

def test_pre_cutoff_and_own_comments_untouched(env):
    cutoff = env.cfg.reels[0].handle_comments_after
    env.comment("old", at=cutoff - timedelta(minutes=1))
    env.comment("mine", username="Alex")
    env.comment("mine2", username="someone", from_id="me1")
    assert ingest_comments(env.ctx) == 1
    assert env.db.get("old").status is Status.PRE_CUTOFF
    assert not env.db.has_comment("mine") and not env.db.has_comment("mine2")
    assert env.jev.calls == []
    assert ingest_comments(env.ctx) == 0  # already stored
    env.clock.advance(hours=2)
    assert send_due_dm(env.ctx) is False
    assert env.ig.sent == []


def test_comment_at_cutoff_is_handled(env):
    env.comment("c1", at=env.cfg.reels[0].handle_comments_after)
    ingest_comments(env.ctx)
    assert env.db.get("c1").status is Status.QUEUED


def test_offtopic_left_for_owner_no_writes(env):
    env.jev.comment_probs["another gatekeeping video"] = 0.1
    env.comment("c1", text="another gatekeeping video")
    assert ingest_comments(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.LEFT_FOR_OWNER and r.jev_yes == 0.1
    env.clock.advance(hours=2)
    assert send_due_dm(env.ctx) is False
    assert env.ig.sent == []


def test_jev_error_not_stored_then_retried(env):
    env.jev.raise_on.add("Tokyo")
    env.comment("c1")
    env.comment("c2", text="me pls", username="bob")
    assert ingest_comments(env.ctx) == 1
    assert not env.db.has_comment("c1") and env.db.has_comment("c2")
    env.jev.raise_on.clear()
    assert ingest_comments(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.QUEUED and r.jev_yes == 0.95


def test_queued_fields(env):
    env.comment("c1", from_id="u1")
    ingest_comments(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.QUEUED and r.kind == "normal"
    assert r.commenter_id == "u1" and r.reel_key == "tokyo" and r.media_id == MEDIA
    delay = r.dm_due_at - r.commented_at
    assert timedelta(seconds=50) <= delay <= timedelta(seconds=70)


def test_intake_oldest_first(env):
    env.comment("late", text="late one", username="a", at=T9 + timedelta(minutes=1))
    env.comment("early", text="early one", username="b", at=T9 - timedelta(minutes=5))
    env.clock.advance(minutes=2)
    ingest_comments(env.ctx)
    assert [t for _, t in env.jev.calls] == ["early one", "late one"]
    # same user twice on the reel: the older one is the one that's queued
    env.comment("x2", username="dup", at=T9 + timedelta(minutes=1))
    env.comment("x1", username="dup", at=T9)
    ingest_comments(env.ctx)
    assert env.db.get("x1").status is Status.QUEUED
    assert env.db.get("x2").status is Status.DUPLICATE


def test_duplicate_same_reel(env):
    env.jev.comment_probs["lol"] = 0.05
    env.comment("c0", text="lol")
    env.comment("c1", at=T9 + timedelta(seconds=1))
    env.comment("c2", text="Tokyo pls", username="Sarah", at=T9 + timedelta(seconds=2))
    env.comment("c3", username="bob", at=T9 + timedelta(seconds=3))
    env.clock.advance(seconds=5)
    assert ingest_comments(env.ctx) == 4
    assert env.db.get("c0").status is Status.LEFT_FOR_OWNER
    assert env.db.get("c1").status is Status.QUEUED  # off-topic row doesn't block
    assert env.db.get("c2").status is Status.DUPLICATE
    assert env.db.get("c3").status is Status.QUEUED
    # a later comment after DM 1 already went out is still a duplicate
    env.clock.advance(minutes=45)
    assert send_due_dm(env.ctx)
    env.comment("c4", at=env.clock.t)
    ingest_comments(env.ctx)
    assert env.db.get("c4").status is Status.DUPLICATE


def test_expired_after_167h(env):
    now = T9 + timedelta(days=10)
    env.clock.set(now)
    # stored already too old
    env.comment("old", username="a", at=now - timedelta(hours=167))
    env.comment("young", username="b", at=now - timedelta(hours=166, minutes=59))
    ingest_comments(env.ctx)
    assert env.db.get("old").status is Status.EXPIRED
    assert env.db.get("young").status is Status.QUEUED
    # queued, but held back until past 167 h -> swept to EXPIRED, never sent
    env.db.kv_set("next_dm1_not_before", (env.clock.t + timedelta(hours=1)).isoformat())
    assert send_due_dm(env.ctx) is False
    assert env.db.get("young").status is Status.QUEUED
    env.clock.advance(minutes=1)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("young").status is Status.EXPIRED
    env.clock.advance(hours=2)
    assert send_due_dm(env.ctx) is False
    assert env.ig.sent == []


def test_waiting_row_expires_even_in_quiet_hours(env):
    env.db.insert_request(other_row("old", "sarah", Status.AWAITING_EMAIL))
    env.clock.set(datetime(2026, 11, 3, 23, 0, tzinfo=SGT))
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=50)  # 23:50, due
    send_due_dm(env.ctx)
    assert env.db.get("c1").status is Status.WAITING
    env.clock.set(datetime(2026, 11, 10, 23, 0, tzinfo=SGT) + timedelta(hours=1))  # 00:00 SGT +7d
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.EXPIRED


# ---------------------------------------------------------------- DM 1

def test_dm_waits_50_to_70_seconds(env):
    env.comment("c1")
    env.clock.advance(seconds=10)
    ingest_comments(env.ctx)
    env.clock.set(T9 + timedelta(seconds=49))
    assert send_due_dm(env.ctx) is False
    assert env.ig.sent == []
    sent = False
    while env.clock.t < T9 + timedelta(seconds=70) and not sent:
        env.clock.advance(seconds=1)
        sent = send_due_dm(env.ctx)
    assert sent
    assert env.clock.t <= T9 + timedelta(seconds=70)
    assert env.ig.sent[0] == ("private", "c1", env.dm1())
    assert env.ig.sent[1][:2] == ("public", "c1")
    assert len(env.ig.sent) == 2
    assert any(5 <= s <= 10 for s in env.sleeps)
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_YES
    assert r.igsid == "igsid-sarah" and r.dm1_text == env.dm1()
    assert r.dm1_sent_at == env.clock.t
    assert env.db.is_bot_message("mid1")
    pool = env.cfg.messages.public_replies
    assert env.db.recent_public_replies(1)[0] in pool
    # next DM 1 gap is 60-180 s from now
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert timedelta(seconds=60) <= nb - env.clock.t <= timedelta(seconds=180)
    # never sent twice
    env.clock.advance(hours=1)
    assert send_due_dm(env.ctx) is False
    assert len(env.privates()) == 1


def test_display_name_used(env):
    env.ig.display_names["u1"] = "Sam Lee"
    env.comment("c1", username="samlee", from_id="u1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    assert env.privates()[0][2] == env.dm1("Sam")


def test_display_name_prefers_known_igsid(env):
    env.db.insert_request(other_row("old", "samlee", Status.DECLINED, igsid="ig-77"))
    env.ig.display_names["ig-77"] = "Samuel"
    env.ig.display_names["u1"] = "Wrong"
    env.comment("c1", username="samlee", from_id="u1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    assert env.privates()[0][2] == env.dm1("Samuel")


def test_backlog_pacing(env):
    start = T9 - timedelta(hours=1)
    comments = [
        env.comment(f"c{i:03d}", username=f"user{i}", at=start + timedelta(seconds=i * 7))
        for i in range(100)
    ]
    random.Random(5).shuffle(env.ig.comments[MEDIA])
    env.clock.set(T9)
    assert ingest_comments(env.ctx) == 100
    for _ in range(60):
        send_due_dm(env.ctx)
        env.clock.advance(seconds=60)
    privates = env.privates()
    assert 0 < len(privates) <= 25
    assert [p[1] for p in privates] == [c.id for c in comments[: len(privates)]]
    assert len(privates) >= 15  # not stalled
    times = [env.db.get(p[1]).dm1_sent_at for p in privates]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= timedelta(seconds=60) for g in gaps)
    # within the first hour the cap can't bind, so gap <= 180 s + one 60 s tick
    assert all(g <= timedelta(seconds=180 + 60) for g in gaps)


def test_hourly_cap_rolling():
    env = Env(pacing={"backlog_gap_seconds": (1.0, 1.0)})
    for i in range(60):
        env.comment(f"c{i:03d}", username=f"u{i}", at=T9 - timedelta(hours=1, seconds=-i))
    ingest_comments(env.ctx)
    for _ in range(180 * 2):  # 3 hours, every 30 s
        send_due_dm(env.ctx)
        env.clock.advance(seconds=30)
    times = sorted(env.db.get(p[1]).dm1_sent_at for p in env.privates())
    assert len(times) >= 50
    for t in times:
        assert sum(1 for x in times if t <= x < t + timedelta(hours=1)) <= 25


def test_quiet_hours_blocks_dm1(env):
    env.clock.set(datetime(2026, 11, 4, 1, 59, 30, tzinfo=SGT))  # quiet hours are 02:00-08:30
    env.comment("c1")
    env.clock.advance(seconds=10)
    ingest_comments(env.ctx)
    assert env.db.get("c1").dm_due_at <= datetime(2026, 11, 4, 2, 0, 40, tzinfo=SGT)
    env.clock.advance(seconds=50)  # due, but it's now quiet hours
    while env.clock.t < datetime(2026, 11, 4, 8, 30, tzinfo=SGT):
        assert send_due_dm(env.ctx) is False
        env.clock.advance(minutes=1)
    assert env.ig.sent == []
    assert send_due_dm(env.ctx) is True
    assert env.ig.sent[0][:2] == ("private", "c1")


def test_private_reply_refused_goes_to_alex(env):
    env.comment("c1")
    env.comment("c2", username="bob", at=T9 + timedelta(seconds=1))
    env.ig.fail_private["c1"] = InstagramError(400, 10, "This message is sent outside of allowed window.")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER
    assert "This message is sent outside of allowed window." in r.note
    assert r.dm1_sent_at is None
    assert env.ig.sent == []
    assert [e for _, e, _ in env.db.events("c1") if e == "dm1_failed"] == ["dm1_failed"]
    for _ in range(5):
        env.clock.advance(minutes=1)
        send_due_dm(env.ctx)
    assert env.ig.private_attempts.count("c1") == 1
    assert env.privates()[0][1] == "c2"


def test_rate_limited_backs_off_and_retries(env):
    env.comment("c1")
    env.ig.fail_private["c1"] = RateLimited(429, 4, "slow down")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.QUEUED
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb == env.clock.t + timedelta(minutes=15)
    del env.ig.fail_private["c1"]
    env.clock.advance(minutes=14)
    assert send_due_dm(env.ctx) is False
    env.clock.advance(minutes=1)
    assert send_due_dm(env.ctx) is True
    assert env.privates() == [("private", "c1", env.dm1())]


def test_unexpected_error_never_double_dms(env):
    env.comment("c1")
    env.ig.fail_private["c1"] = KeyError("message_id")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    with pytest.raises(KeyError):
        send_due_dm(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "KeyError" in r.note
    del env.ig.fail_private["c1"]
    env.clock.advance(minutes=5)
    send_due_dm(env.ctx)
    assert env.ig.private_attempts == ["c1"]


def test_interrupted_send_goes_to_alex(env):
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    env.db.kv_set("dm1_in_flight", "dm:c1")  # process died mid-send last time
    assert send_due_dm(env.ctx) is False
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "interrupted" in r.note
    assert env.ig.private_attempts == []
    assert not env.db.kv_get("dm1_in_flight")


def test_interrupted_public_only_send_goes_to_manual():
    env = Env(mode="public_only")
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    env.db.kv_set("dm1_in_flight", "public:c1")
    assert send_due_dm(env.ctx) is False
    r = env.db.get("c1")
    assert r.status is Status.MANUAL_DM_NEEDED and "interrupted" in r.note
    assert env.ig.sent == []


def test_in_flight_marker_set_during_send(env):
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    seen = []
    orig = env.ig.send_private_reply
    env.ig.send_private_reply = lambda *a: (seen.append(env.db.kv_get("dm1_in_flight")), orig(*a))[1]
    assert send_due_dm(env.ctx)
    assert seen == ["dm:c1"] and not env.db.kv_get("dm1_in_flight")


def test_public_reply_failure_only_logged(env):
    env.comment("c1")
    env.ig.fail_public["c1"] = InstagramError(400, 100, "cannot reply")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.db.get("c1").status is Status.AWAITING_YES
    assert any("cannot reply" in d for _, _, d in env.db.events("c1"))
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb - env.clock.t <= timedelta(seconds=180)


def test_public_reply_rate_limited_backs_off(env):
    env.comment("c1")
    env.ig.fail_public["c1"] = RateLimited(429, 4, "slow down")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.db.get("c1").status is Status.AWAITING_YES
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb == env.clock.t + timedelta(minutes=15)


def test_repeat_commenter_gets_same_email_dm(env):
    env.db.add_email(comment_id="old", handle="Sarah", igsid=None, email="s@x.com",
                     provided_at=T9 - timedelta(days=2), reel_url="https://x", keyword="Other")
    env.comment("c1")
    ingest_comments(env.ctx)
    assert env.db.get("c1").kind == "repeat"
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    text = render(env.cfg.messages.repeat_dm1, name="sarah", keyword="Tokyo")
    assert env.privates() == [("private", "c1", text)]
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_SAME_EMAIL and r.dm1_text == text


def test_repeat_kind_without_email_falls_back(env):
    env.comment("c1")
    ingest_comments(env.ctx)
    env.db.update("c1", kind="repeat")  # email vanished before send time
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    assert env.privates() == [("private", "c1", env.dm1())]
    assert env.db.get("c1").status is Status.AWAITING_YES


def test_engaged_person_waits(env):
    env.db.insert_request(other_row("old", "sarah", Status.AWAITING_EMAIL, igsid="igsid-sarah"))
    env.comment("c1")
    env.comment("c2", username="bob", at=T9 + timedelta(seconds=30))
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.db.get("c1").status is Status.WAITING
    assert env.privates() == [("private", "c2", env.dm1("bob"))]
    env.clock.advance(minutes=5)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.WAITING
    env.db.update("old", status=Status.EMAIL_CAPTURED)
    assert send_due_dm(env.ctx) is True
    assert env.privates()[-1][:2] == ("private", "c1")
    assert env.db.get("c1").status is Status.AWAITING_YES


def test_superseded_awaiting_yes(env):
    env.db.insert_request(other_row("old", "sarah", Status.AWAITING_YES, igsid="igsid-sarah",
                                    dm1_sent_at=T9 - timedelta(hours=25)))
    env.db.insert_request(other_row("older", "sarah", Status.DECLINED))
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    assert env.db.get("old").status is Status.SUPERSEDED
    assert env.db.get("older").status is Status.DECLINED
    assert env.db.get("c1").status is Status.AWAITING_YES


def test_public_only_mode():
    env = Env(mode="public_only")
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.privates() == []
    assert [s[:2] for s in env.ig.sent] == [("public", "c1")]
    r = env.db.get("c1")
    assert r.status is Status.MANUAL_DM_NEEDED and r.dm1_text is None
    assert r.dm1_sent_at == env.clock.t  # counted by the hourly cap
    assert env.db.kv_get("next_dm1_not_before")
    env.clock.advance(hours=1)
    assert send_due_dm(env.ctx) is False
    assert len(env.ig.sent) == 1


def test_recent_unanswered_dm1_blocks_new_one(env):
    sent = T9 - timedelta(hours=23)
    env.db.insert_request(other_row("old", "sarah", Status.AWAITING_YES, igsid="igsid-sarah",
                                    dm1_sent_at=sent))
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.WAITING
    assert env.db.get("old").status is Status.AWAITING_YES
    env.clock.set(sent + timedelta(hours=24))  # old DM 1 now 24 h unanswered
    assert send_due_dm(env.ctx) is True
    assert env.privates() == [("private", "c1", env.dm1())]
    assert env.db.get("old").status is Status.SUPERSEDED


@pytest.mark.parametrize("status", [Status.NEEDS_OWNER, Status.HANDLED_BY_OWNER])
def test_alex_handling_person_no_dm(env, status):
    env.db.insert_request(other_row("old", "Sarah", status))
    env.comment("c1")
    env.comment("c2", username="bob", at=T9 + timedelta(seconds=1))
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.note == "you're already handling this person"
    assert env.privates() == [("private", "c2", env.dm1("bob"))]
    assert "c1" not in env.ig.private_attempts


def test_public_only_counts_toward_hourly_cap():
    env = Env(mode="public_only", pacing={"backlog_gap_seconds": (1.0, 1.0)})
    for i in range(40):
        env.comment(f"c{i:03d}", username=f"u{i}", at=T9 - timedelta(hours=1, seconds=-i))
    ingest_comments(env.ctx)
    for _ in range(60):  # 60 min, every 30 s
        send_due_dm(env.ctx)
        env.clock.advance(seconds=30)
    assert len(env.ig.sent) == 25
    times = env.db.dm1_times_since(T9 - timedelta(hours=1))
    assert len(times) == 25
    assert env.db.get("c000").status is Status.MANUAL_DM_NEEDED


def test_list_comments_failure_isolated_per_reel():
    env = Env()
    second = replace(env.cfg.reels[0], key="second", keyword="Second")
    env.cfg = replace(env.cfg, reels=[env.cfg.reels[0], second])
    env.ctx.config = env.cfg
    env.ctx.media_ids["second"] = "m2"
    env.ig.comments["m2"] = [Comment("s1", "Second", "bob", None, T9)]
    orig = env.ig.list_comments

    def flaky(media_id):
        if media_id == MEDIA:
            raise InstagramError(500, 2, "service unavailable")
        return orig(media_id)

    env.ig.list_comments = flaky
    env.comment("c1")
    assert ingest_comments(env.ctx) == 1
    assert env.db.get("s1").status is Status.QUEUED
    assert not env.db.has_comment("c1")
    assert any("service unavailable" in d for _, e, d in env.db.events(None)
               if e == "list_comments_failed")


# ---------------------------------------------------------------- final review fixes
from igdm.instagram import MaybeSent  # noqa: E402


def test_waiting_repeat_commenter_released_gets_repeat_dm1(env):
    """Email captured while this row was WAITING: ask about the same email, not again."""
    env.db.insert_request(other_row("old", "sarah", Status.AWAITING_EMAIL, igsid="igsid-sarah"))
    env.comment("c1")
    ingest_comments(env.ctx)
    assert env.db.get("c1").kind == "normal"  # no email on file at intake
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.WAITING
    env.db.add_email(comment_id="old", handle="sarah", igsid="igsid-sarah", email="s@x.com",
                     provided_at=env.clock.t, reel_url="https://x", keyword="Other")
    env.db.update("old", status=Status.EMAIL_CAPTURED)
    env.clock.advance(minutes=5)
    assert send_due_dm(env.ctx) is True
    text = render(env.cfg.messages.repeat_dm1, name="sarah", keyword="Tokyo")
    assert env.privates() == [("private", "c1", text)]
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_SAME_EMAIL and r.dm1_text == text


@pytest.mark.parametrize("err", [
    KeyError("recipient_id"),
    InstagramError(0, None, "ConnectError: down"),
    MaybeSent(0, None, "ReadTimeout: lost"),
])
def test_dm1_maybe_sent_is_counted_and_pauses_an_hour(env, err):
    env.comment("c1")
    env.comment("c2", username="bob", at=T9 + timedelta(seconds=1))
    env.ig.fail_private["c1"] = err
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    if isinstance(err, InstagramError):
        assert send_due_dm(env.ctx) is False
    else:
        with pytest.raises(KeyError):
            send_due_dm(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "check whether it went out" in r.note
    assert r.dm1_sent_at == env.clock.t
    assert env.db.dm1_times_since(env.clock.t - timedelta(hours=1)) == [env.clock.t]
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb == env.clock.t + timedelta(hours=1)
    assert not env.db.kv_get("dm1_in_flight")
    env.clock.advance(minutes=59)
    assert send_due_dm(env.ctx) is False
    assert env.ig.private_attempts == ["c1"]
    env.clock.advance(minutes=1)
    assert send_due_dm(env.ctx) is True
    assert env.ig.private_attempts == ["c1", "c2"]


def test_dm1_plain_refusal_not_paused(env):
    env.comment("c1")
    env.ig.fail_private["c1"] = InstagramError(400, 10, "outside window")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    send_due_dm(env.ctx)
    assert env.db.get("c1").dm1_sent_at is None
    assert not env.db.kv_get("next_dm1_not_before")


def test_public_only_maybe_sent_is_counted_and_pauses():
    env = Env(mode="public_only")
    env.comment("c1")
    env.ig.fail_public["c1"] = MaybeSent(0, None, "ReadTimeout: lost")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    r = env.db.get("c1")
    assert r.status is Status.MANUAL_DM_NEEDED and "check whether it was posted" in r.note
    assert r.dm1_sent_at == env.clock.t
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb == env.clock.t + timedelta(hours=1)


def test_interrupted_send_is_counted(env):
    env.comment("c1")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    env.db.kv_set("dm1_in_flight", "dm:c1")
    send_due_dm(env.ctx)
    assert env.db.get("c1").dm1_sent_at == env.clock.t
    nb = datetime.fromisoformat(env.db.kv_get("next_dm1_not_before"))
    assert nb == env.clock.t + timedelta(hours=1)


@pytest.mark.parametrize("who", [("alex", None), ("ALEX", None), ("renamed", "me1")])
def test_alex_already_replied_publicly_no_dm(env, who):
    username, from_id = who
    env.comment("c1")
    env.ig.comment_replies["c1"] = [Comment("r1", "sent!", username, from_id, T9)]
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    r = env.db.get("c1")
    assert r.status is Status.HANDLED_BY_OWNER and "replied" in r.note
    assert env.ig.sent == [] and env.ig.private_attempts == []


def test_public_reply_by_someone_else_does_not_block(env):
    env.comment("c1")
    env.ig.comment_replies["c1"] = [Comment("r1", "me too", "bob", "9", T9)]
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.privates() == [("private", "c1", env.dm1())]


def test_alex_public_reply_check_failure_proceeds(env):
    env.comment("c1")
    env.ig.fail_list_replies["c1"] = InstagramError(500, 2, "service unavailable")
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is True
    assert env.privates() == [("private", "c1", env.dm1())]
    assert any(e == "reply_check_failed" for _, e, _ in env.db.events("c1"))


def test_alex_public_reply_checked_in_public_only_mode():
    env = Env(mode="public_only")
    env.comment("c1")
    env.ig.comment_replies["c1"] = [Comment("r1", "sent!", "alex", "me1", T9)]
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx) is False
    assert env.db.get("c1").status is Status.HANDLED_BY_OWNER
    assert env.ig.sent == []


def test_name_skips_emoji_token(env):
    env.comment("c1", from_id="42")
    env.ig.display_names["42"] = "✨ Sam"
    ingest_comments(env.ctx)
    env.clock.advance(minutes=44)
    assert send_due_dm(env.ctx)
    assert env.privates()[0][2] == env.dm1("Sam")


from igdm.engine import COMMENT_POLL_INTERVAL  # noqa: E402


def _count_list_comments(env):
    calls = []
    orig = env.ig.list_comments

    def counted(media_id):
        calls.append(env.clock.t)
        if env.ig_fail_comments:
            raise env.ig_fail_comments
        return orig(media_id)

    env.ig_fail_comments = None
    env.ig.list_comments = counted
    return calls


def test_comment_polling_runs_on_every_minute_tick():
    env = Env()
    env.ctx.comment_poll_interval = COMMENT_POLL_INTERVAL
    assert COMMENT_POLL_INTERVAL < timedelta(seconds=60)  # a 60 s tick always polls
    calls = _count_list_comments(env)
    env.comment("c1")
    assert ingest_comments(env.ctx) == 1
    env.comment("c2", username="bob")
    env.clock.advance(seconds=20)  # an extra pass in between doesn't poll again
    assert ingest_comments(env.ctx) == 0
    assert len(calls) == 1
    env.clock.advance(seconds=40)
    assert ingest_comments(env.ctx) == 1
    assert len(calls) == 2


def test_comment_polling_is_per_reel():
    env = Env()
    env.ctx.comment_poll_interval = COMMENT_POLL_INTERVAL
    second = replace(env.cfg.reels[0], key="second", keyword="Second")
    env.cfg = replace(env.cfg, reels=[env.cfg.reels[0], second])
    env.ctx.config = env.cfg
    env.ctx.media_ids["second"] = "m2"
    calls = _count_list_comments(env)
    ingest_comments(env.ctx)
    assert len(calls) == 2  # both reels polled once
    env.db.kv_set("comments_polled:second", (env.clock.t - timedelta(minutes=10)).isoformat())
    ingest_comments(env.ctx)
    assert len(calls) == 3  # only the overdue reel


def test_rate_limited_comment_listing_backs_off_15_min(env):
    calls = _count_list_comments(env)
    env.ig_fail_comments = RateLimited(429, 4, "slow down")
    env.comment("c1")
    assert ingest_comments(env.ctx) == 0
    until = datetime.fromisoformat(env.db.kv_get("comment_backoff_until"))
    assert until == env.clock.t + timedelta(minutes=15)
    env.ig_fail_comments = None
    env.clock.advance(minutes=14, seconds=59)
    assert ingest_comments(env.ctx) == 0
    assert len(calls) == 1
    env.clock.advance(seconds=1)
    assert ingest_comments(env.ctx) == 1
    assert len(calls) == 2
