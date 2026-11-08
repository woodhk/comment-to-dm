import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeClassifier, FakeInstagram, repo_config

from igdm.config import load_config
from igdm.db import Database, Request, Status
from igdm.engine import (
    Context, REPLY_BACKOFF_KEY, REPLY_IN_FLIGHT_KEY, WATERMARK_KEY, ingest_comments,
    process_replies, resume_followups, send_due_dm,
)
from igdm.instagram import Comment, InstagramError, RateLimited

ROOT = Path(__file__).resolve().parent.parent
SGT = ZoneInfo("Asia/Singapore")
T9 = datetime(2026, 11, 3, 9, 0, tzinfo=SGT)
MEDIA = "m1"
YES = {"yes": 0.95, "no": 0.02, "other": 0.03}
NO = {"yes": 0.02, "no": 0.95, "other": 0.03}


class Crash(Exception):
    pass


class Env:
    def __init__(self):
        self.cfg = repo_config()
        self.m = self.cfg.messages
        self.db = Database(":memory:")
        self.ig = FakeInstagram("me1", "alex")
        self.jev = FakeClassifier(default_comment=0.95)
        self.t = T9.astimezone(timezone.utc)
        self.ig.now = self.t
        self.sleeps = []  # (seconds, number of sends so far)
        self.ctx = Context(
            config=self.cfg, db=self.db, ig=self.ig, jev=self.jev, now=lambda: self.t,
            sleep=self.sleep, rng=random.Random(1), own_id="me1",
            own_username="alex", media_ids={"tokyo": MEDIA},
            comment_poll_interval=timedelta(0),  # these tests poll comments at will
        )

    def sleep(self, s):
        self.sleeps.append((s, len(self.ig.sent)))

    def set(self, t):
        self.t = t
        self.ig.now = t

    def advance(self, **kw):
        self.set(self.t + timedelta(**kw))

    def live(self, cid="c1", username="sarah"):
        """Comment, then send DM 1 through the Task 7 engine. Returns the person's igsid."""
        self.ig.comments.setdefault(MEDIA, []).append(Comment(cid, "Tokyo", username, None, self.t))
        ingest_comments(self.ctx)
        for _ in range(50):
            self.advance(minutes=1)
            if send_due_dm(self.ctx):
                break
        r = self.db.get(cid)
        assert r.status in (Status.AWAITING_YES, Status.AWAITING_SAME_EMAIL)
        self.ig.sent.clear()
        self.sleeps.clear()
        self.jev.calls.clear()
        self.advance(minutes=3)  # past the backlog gap
        return r.igsid

    def say(self, igsid, text, after=None):
        """The person sends a message `after` (timedelta) from now (default: now)."""
        return self.ig.add_user_message(igsid, text, self.t + (after or timedelta()))

    def dms(self):
        return [s for s in self.ig.sent if s[0] == "dm"]

    def status(self, cid="c1"):
        return self.db.get(cid).status


@pytest.fixture
def env():
    return Env()


def followups(env, igsid):
    return [("dm", igsid, env.m.followup_1), ("dm", igsid, env.m.followup_2)]


def test_yes_sends_two_followups_separately(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    replied = env.t
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)
    between = [s for s, n in env.sleeps if n == 1]
    assert len(between) == 1 and 4 <= between[0] <= 12  # the gap sits between the two messages
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_EMAIL and r.last_user_msg_at == replied
    # both follow-ups are known bot sends
    assert env.jev.calls == [("reply", "yes pls")]
    # next tick: the echoes aren't mistaken for Alex, nothing is re-sent
    env.advance(minutes=1)
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == followups(env, igsid)
    assert env.status() is Status.AWAITING_EMAIL


def test_reply_not_processed_before_settle(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(seconds=30)
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == [] and env.jev.calls == []
    assert env.status() is Status.AWAITING_YES
    env.advance(seconds=61)  # 91 s quiet
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_multi_bubble_email_wins(env):
    igsid = env.live()
    env.jev.reply_probs["yes!"] = YES
    env.say(igsid, "yes!")
    env.advance(seconds=20)
    env.say(igsid, "Sarah@Gmail.com")
    emailed = env.t
    env.advance(seconds=80)  # 100 s after "yes!" but only 80 s after the email
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == []
    env.advance(seconds=15)
    assert process_replies(env.ctx) == 1
    assert len(env.ig.sent) == 1
    kind, who, text = env.ig.sent[0]
    assert (kind, who) == ("dm", igsid) and text in env.m.got_it
    assert env.jev.calls == [("email", "sarah@gmail.com")]  # no reply check, just the email one
    seen_pause, typing = [s for s, n in env.sleeps]  # Seen, a pause, then typing…
    assert 2 <= seen_pause <= 4 and 3 <= typing <= 9
    [e] = env.db.emails()
    reel = env.cfg.reels[0]
    assert e.instagram_handle == "sarah" and e.email == "sarah@gmail.com"
    assert e.provided_at == emailed and e.reel_url == reel.url and e.reel_keyword == reel.keyword
    assert e.ig_user_id == igsid and e.comment_id == "c1"
    r = env.db.get("c1")
    assert r.status is Status.EMAIL_CAPTURED and r.last_user_msg_at == emailed
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0  # captured rows aren't live: nothing more
    assert len(env.ig.sent) == 1


def test_no_reply_nothing_happens(env):
    env.live()
    for _ in range(3):
        env.advance(hours=1)
        assert process_replies(env.ctx) == 0
        assert resume_followups(env.ctx) == 0
    assert env.status() is Status.AWAITING_YES
    assert env.ig.sent == [] and env.jev.calls == []


def test_declined(env):
    igsid = env.live()
    env.jev.reply_probs["nah i'm good"] = NO
    env.say(igsid, "nah i'm good")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.DECLINED and env.ig.sent == []


def test_unclear_goes_to_alex(env):
    igsid = env.live()
    env.say(igsid, "what's in it?")  # unknown to the fake -> "other"
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "what's in it?" in r.note
    assert env.ig.sent == []


def _after_followups(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    env.ig.sent.clear()
    env.sleeps.clear()
    env.advance(minutes=1)
    return igsid


def test_email_wait_then_alex(env):
    igsid = _after_followups(env)
    env.say(igsid, "sure")
    env.advance(minutes=10)
    assert process_replies(env.ctx) == 0
    assert env.status() is Status.AWAITING_EMAIL
    env.advance(minutes=21)  # 31 min after "sure"
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "sure" in r.note
    assert env.ig.sent == [] and env.db.emails() == []
    assert ("reply", "sure") not in env.jev.calls


def test_email_after_sure_is_captured(env):
    igsid = _after_followups(env)
    env.say(igsid, "sure")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0  # waiting for the email
    env.advance(minutes=3)
    env.say(igsid, "it's sarah@gmail.com")
    emailed = env.t
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    [e] = env.db.emails()
    assert e.email == "sarah@gmail.com" and e.provided_at == emailed
    assert env.status() is Status.EMAIL_CAPTURED
    [(kind, who, text)] = env.ig.sent
    assert who == igsid and text in env.m.got_it


def test_alex_manual_reply_backs_off(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.ig.add_own_message(igsid, "hey! what's your email?", env.t + timedelta(seconds=30))
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.HANDLED_BY_OWNER
    assert env.ig.sent == [] and env.jev.calls == []
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == [] and env.db.emails() == []


def test_alex_reply_mid_conversation_backs_off_immediately(env):
    igsid = _after_followups(env)
    env.ig.add_own_message(igsid, "lmk your email", env.t)
    env.advance(seconds=10)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.HANDLED_BY_OWNER


def test_bot_echo_with_different_id_not_mistaken(env):
    env.ig.echo_ids_differ = True
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    env.advance(minutes=1)
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1  # the echoed follow-ups don't count as Alex
    assert env.status() is Status.EMAIL_CAPTURED
    assert len(env.dms()) == 3


def test_unknown_outgoing_with_bot_text_outside_120s_is_alex(env):
    igsid = _after_followups(env)
    # same text as a bot send, but 10 min later: Alex pasted it by hand
    env.ig.add_own_message(igsid, env.m.followup_2, env.t + timedelta(minutes=10))
    env.advance(minutes=11)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.HANDLED_BY_OWNER


def test_window_expired(env):
    igsid = env.live()
    env.jev.reply_probs["yes"] = YES
    env.say(igsid, "yes")
    env.advance(hours=25)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.WINDOW_EXPIRED and "yes" in r.note
    assert env.ig.sent == []


def _repeat_commenter(env):
    env.db.add_email(comment_id="old-c", handle="Sarah", igsid=None, email="old@x.com",
                     provided_at=env.t - timedelta(days=3), reel_url="https://old", keyword="Old")
    igsid = env.live()
    assert env.status() is Status.AWAITING_SAME_EMAIL
    return igsid


def test_same_email_yes_reuses_email(env):
    igsid = _repeat_commenter(env)
    env.jev.reply_probs["yep same one"] = YES
    env.say(igsid, "yep same one")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    rows = [e for e in env.db.emails() if e.comment_id == "c1"]
    assert len(rows) == 1
    e = rows[0]
    assert e.email == "old@x.com" and e.reel_url == env.cfg.reels[0].url
    assert e.reel_keyword == "Tokyo" and e.instagram_handle == "sarah"
    assert env.status() is Status.EMAIL_CAPTURED
    [(kind, who, text)] = env.ig.sent
    assert who == igsid and text in env.m.got_it


def test_same_email_new_address_wins(env):
    igsid = _repeat_commenter(env)
    env.say(igsid, "use new@y.com instead")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    [e] = [e for e in env.db.emails() if e.comment_id == "c1"]
    assert e.email == "new@y.com"
    assert env.jev.calls == [("email", "new@y.com")]  # no yes/no question, just the email check


@pytest.mark.parametrize("reply,probs", [("no thanks", NO), ("which email?", None)])
def test_same_email_no_or_other_goes_to_alex(env, reply, probs):
    igsid = _repeat_commenter(env)
    if probs:
        env.jev.reply_probs[reply] = probs
    env.say(igsid, reply)
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.NEEDS_OWNER
    assert env.ig.sent == [] and [e.comment_id for e in env.db.emails()] == ["old-c"]


def test_resume_followups_after_crash(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)

    def crash(s):
        if env.ig.sent:  # in the gap after follow-up 1 went out
            raise Crash("laptop lid closed")

    env.ctx.sleep = crash
    with pytest.raises(Crash):
        process_replies(env.ctx)
    assert env.ig.sent == [("dm", igsid, env.m.followup_1)]
    assert env.status() is Status.FOLLOWUP1_SENT
    env.ctx.sleep = env.sleep
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0  # never re-sends follow-up 1
    assert resume_followups(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)
    assert env.status() is Status.AWAITING_EMAIL
    assert resume_followups(env.ctx) == 0
    assert process_replies(env.ctx) == 0
    assert len(env.ig.sent) == 2


def test_resume_followups_window_closed(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_2] = InstagramError(500, 2, "temporary")
    process_replies(env.ctx)
    assert env.status() is Status.FOLLOWUP1_SENT
    env.ig.fail_dm.clear()
    env.advance(hours=24)
    assert resume_followups(env.ctx) == 1
    assert env.status() is Status.WINDOW_EXPIRED
    assert env.dms() == [("dm", igsid, env.m.followup_1)]


def test_followup2_failure_retried_without_resending_followup1(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_2] = InstagramError(500, 2, "temporary")
    process_replies(env.ctx)
    assert resume_followups(env.ctx) == 0  # still failing: state kept
    assert env.status() is Status.FOLLOWUP1_SENT
    assert env.ig.sent == [("dm", igsid, env.m.followup_1)]
    env.ig.fail_dm.clear()
    env.advance(minutes=1)
    assert process_replies(env.ctx) == 0
    assert resume_followups(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)
    assert env.status() is Status.AWAITING_EMAIL


def test_followup1_failure_retried_next_tick(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_1] = InstagramError(500, 2, "temporary")
    assert process_replies(env.ctx) == 0
    assert env.status() is Status.AWAITING_YES and env.ig.sent == []
    assert any(e[1] == "followup_failed" for e in env.db.events("c1"))
    env.ig.fail_dm.clear()
    env.advance(minutes=1)
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_rate_limited_stops_tick_and_relists(env):
    a = env.live("c1", "sarah")
    b = env.live("c2", "bob")
    env.jev.reply_probs["yes pls"] = YES
    env.say(a, "yes pls")
    env.say(b, "yes pls", after=timedelta(seconds=1))
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_1] = RateLimited(429, 4, "slow down")
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == [] and len(env.jev.calls) == 1  # stopped after the first one
    env.ig.fail_dm.clear()
    env.advance(minutes=20)
    assert process_replies(env.ctx) == 2
    assert env.status("c1") is Status.AWAITING_EMAIL and env.status("c2") is Status.AWAITING_EMAIL
    assert len(env.dms()) == 4


def test_got_it_failure_still_captured(env):
    igsid = env.live()
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=2)
    for line in env.m.got_it:
        env.ig.fail_dm[line] = InstagramError(400, 10, "outside window")
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.EMAIL_CAPTURED and "got-it" in (r.note or "")
    assert [e.email for e in env.db.emails()] == ["sarah@gmail.com"]
    assert env.ig.sent == []


def test_jev_error_defers_then_retries(env):
    igsid = env.live()
    env.jev.raise_on.add("yes pls")
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0
    assert env.status() is Status.AWAITING_YES and env.ig.sent == []
    env.jev.raise_on.clear()
    env.jev.reply_probs["yes pls"] = YES
    env.advance(hours=1)  # well past the 5-min overlap
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_deferred_conversation_relisted(env):
    a = env.live("c1", "sarah")
    b = env.live("c2", "bob")
    env.jev.raise_on.add("yes pls")  # Sarah's reply can't be classified yet
    env.jev.reply_probs["sure!"] = YES
    env.say(a, "yes pls")
    a_at = env.t
    env.advance(minutes=10)
    env.say(b, "sure!")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1  # Bob acted on, Sarah deferred
    wm = datetime.fromisoformat(env.db.kv_get(WATERMARK_KEY))
    assert wm == a_at - timedelta(seconds=1)
    env.jev.raise_on.clear()
    env.jev.reply_probs["yes pls"] = YES
    env.advance(hours=1)
    assert process_replies(env.ctx) == 1
    assert env.status("c1") is Status.AWAITING_EMAIL
    wm2 = datetime.fromisoformat(env.db.kv_get(WATERMARK_KEY))
    assert wm2 > a_at


def test_watermark_default_ignores_non_live_rows(env):
    env.db.insert_request(Request(
        comment_id="m0", reel_key="tokyo", media_id=MEDIA, username="zed", commenter_id=None,
        text="Tokyo", commented_at=env.t - timedelta(days=2), status=Status.MANUAL_DM_NEEDED,
        dm1_sent_at=env.t - timedelta(days=2),
    ))
    seen = []
    orig = env.ig.list_conversations
    env.ig.list_conversations = lambda own, since: seen.append(since) or orig(own, since)
    process_replies(env.ctx)
    assert seen[-1] == env.t - timedelta(minutes=5)  # no LIVE rows: now - 5 min
    env.live()
    dm1_at = env.db.get("c1").dm1_sent_at
    env.db.kv_set(WATERMARK_KEY, "")  # unset
    process_replies(env.ctx)
    assert seen[-1] == dm1_at - timedelta(minutes=5)


def test_list_messages_failure_defers(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_messages[f"conv-{igsid}"] = InstagramError(500, 1, "boom")
    assert process_replies(env.ctx) == 0
    del env.ig.fail_messages[f"conv-{igsid}"]
    env.advance(hours=1)
    assert process_replies(env.ctx) == 1


def test_list_conversations_failure_is_logged(env):
    env.live()
    env.ig.list_conversations = lambda own, since: (_ for _ in ()).throw(
        InstagramError(500, 1, "down"))
    assert process_replies(env.ctx) == 0
    assert any(e[1] == "list_conversations_failed" for e in env.db.events(None))


def test_unrelated_conversation_ignored(env):
    env.live()
    env.say("stranger", "hi alex love your reels")
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == [] and env.jev.calls == []


@pytest.mark.parametrize("kind,status,expect", [
    ("f1", Status.AWAITING_YES, Status.NEEDS_OWNER),
    ("f2", Status.FOLLOWUP1_SENT, Status.NEEDS_OWNER),
    ("gotit", Status.AWAITING_EMAIL, Status.EMAIL_CAPTURED),
])
def test_interrupted_reply_send_never_resent(env, kind, status, expect):
    igsid = env.live()
    env.db.update("c1", status=status, last_user_msg_at=env.t)
    if kind == "gotit":
        env.db.add_email(comment_id="c1", handle="sarah", igsid=igsid, email="s@x.com",
                         provided_at=env.t, reel_url="u", keyword="k")
    env.say(igsid, "yes pls", after=timedelta(seconds=1))
    env.jev.reply_probs["yes pls"] = YES
    env.db.kv_set(REPLY_IN_FLIGHT_KEY, f"{kind}:c1")
    env.advance(minutes=5)
    process_replies(env.ctx)
    resume_followups(env.ctx)
    assert env.status() is expect
    assert env.ig.sent == []
    assert env.db.kv_get(REPLY_IN_FLIGHT_KEY) == ""


def test_fake_list_messages_caps_at_20(env):
    for i in range(25):
        env.say("x", f"m{i}", after=timedelta(seconds=i))
    msgs = env.ig.list_messages("conv-x")
    assert [m.text for m in msgs] == [f"m{i}" for i in range(5, 25)]


# ---------------------------------------------------------------- fix round 1

def test_late_reply_with_email_still_captured(env):
    igsid = env.live()
    env.say(igsid, "yes! sarah@gmail.com")
    replied = env.t
    env.advance(hours=25)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.EMAIL_CAPTURED and r.note == "window closed, no got-it DM"
    assert r.last_user_msg_at == replied
    [e] = env.db.emails()
    assert e.email == "sarah@gmail.com" and e.provided_at == replied
    assert env.ig.sent == [] and env.sleeps == []
    assert env.jev.calls == [("email", "sarah@gmail.com")]


def test_late_reply_without_email_window_expired(env):
    igsid = env.live()
    env.say(igsid, "sure")
    env.advance(hours=25)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.WINDOW_EXPIRED
    assert env.db.emails() == [] and env.ig.sent == []


def test_followup1_failures_capped_at_three(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_1] = InstagramError(400, 10, "user unavailable")
    for _ in range(2):
        assert process_replies(env.ctx) == 0
        assert env.status() is Status.AWAITING_YES
        env.advance(minutes=1)
    assert process_replies(env.ctx) == 1  # third failure hands it to Alex
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "user unavailable" in r.note
    env.ig.fail_dm.clear()
    for _ in range(3):
        env.advance(minutes=1)
        process_replies(env.ctx)
        resume_followups(env.ctx)
    assert env.ig.sent == []


def test_followup2_failures_capped_across_resume(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_2] = InstagramError(500, 2, "temporary")
    process_replies(env.ctx)  # failure 1
    assert resume_followups(env.ctx) == 0  # failure 2
    assert env.status() is Status.FOLLOWUP1_SENT
    assert resume_followups(env.ctx) == 1  # failure 3 -> Alex
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "temporary" in r.note
    env.ig.fail_dm.clear()
    assert resume_followups(env.ctx) == 0
    assert env.ig.sent == [("dm", igsid, env.m.followup_1)]


def test_rate_limit_does_not_count_toward_cap(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_1] = RateLimited(429, 4, "slow down")
    for _ in range(4):
        process_replies(env.ctx)
        env.advance(minutes=16)
    assert env.status() is Status.AWAITING_YES
    env.ig.fail_dm.clear()
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def _backoff_until(env):
    return datetime.fromisoformat(env.db.kv_get(REPLY_BACKOFF_KEY))


def test_rate_limit_in_process_replies_backs_off_both(env):
    a = env.live("c1", "sarah")
    b = env.live("c2", "bob")
    env.jev.reply_probs["yes pls"] = YES
    env.say(a, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_2] = InstagramError(500, 2, "temporary")
    process_replies(env.ctx)  # c1 -> FOLLOWUP1_SENT
    env.ig.fail_dm = {env.m.followup_1: RateLimited(429, 4, "slow down")}
    env.say(b, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0
    assert _backoff_until(env) == env.t + timedelta(minutes=15)
    env.ig.fail_dm.clear()
    sent = list(env.ig.sent)
    calls = len(env.jev.calls)
    env.advance(minutes=14)
    assert process_replies(env.ctx) == 0 and resume_followups(env.ctx) == 0
    assert env.ig.sent == sent and len(env.jev.calls) == calls
    assert env.status("c1") is Status.FOLLOWUP1_SENT
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert resume_followups(env.ctx) == 1
    assert env.status("c1") is Status.AWAITING_EMAIL and env.status("c2") is Status.AWAITING_EMAIL


def test_rate_limit_in_resume_backs_off(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    env.ig.fail_dm[env.m.followup_2] = RateLimited(429, 4, "slow down")
    process_replies(env.ctx)
    assert _backoff_until(env) == env.t + timedelta(minutes=15)
    env.ig.fail_dm.clear()
    assert resume_followups(env.ctx) == 0
    env.advance(minutes=15)
    assert resume_followups(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_rate_limited_list_conversations_backs_off(env):
    env.live()
    orig = env.ig.list_conversations
    env.ig.list_conversations = lambda own, since: (_ for _ in ()).throw(
        RateLimited(429, 4, "slow down"))
    assert process_replies(env.ctx) == 0
    assert _backoff_until(env) == env.t + timedelta(minutes=15)
    calls = []
    env.ig.list_conversations = lambda own, since: calls.append(since) or orig(own, since)
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0 and calls == []
    env.advance(minutes=11)
    process_replies(env.ctx)
    assert len(calls) == 1


def _old_history(env, igsid):
    """Messages already in the thread before DM 1: Alex by hand, and the person."""
    env.ig.add_own_message(igsid, "hey long time, how are you?", env.t - timedelta(days=2))
    env.ig.add_user_message(igsid, "all good! send me stuff at old2@z.com",
                            env.t - timedelta(days=2) + timedelta(minutes=5))
    env.ig.add_user_message(igsid, "yes please", env.t - timedelta(minutes=1))


def test_history_before_dm1_ignored(env):
    _old_history(env, "igsid-sarah")
    env.jev.reply_probs["yes please"] = YES
    igsid = env.live()
    assert igsid == "igsid-sarah"
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0
    assert env.status() is Status.AWAITING_YES
    assert env.ig.sent == [] and env.jev.calls == [] and env.db.emails() == []
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.jev.calls == [("reply", "yes pls")]  # old texts not joined in
    assert env.ig.sent == followups(env, igsid)
    assert env.status() is Status.AWAITING_EMAIL


def test_history_before_dm1_ignored_repeat_commenter(env):
    _old_history(env, "igsid-sarah")
    igsid = _repeat_commenter(env)
    env.advance(minutes=5)
    assert process_replies(env.ctx) == 0
    assert env.status() is Status.AWAITING_SAME_EMAIL
    assert env.ig.sent == [] and env.jev.calls == []
    env.jev.reply_probs["yep"] = YES
    env.say(igsid, "yep")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    [e] = [e for e in env.db.emails() if e.comment_id == "c1"]
    assert e.email == "old@x.com"  # not the old2@z.com from before DM 1
    assert env.jev.calls == [("reply", "yep")]
    assert env.status() is Status.EMAIL_CAPTURED


# ---------------------------------------------------------------- final review fixes
from igdm.instagram import MaybeSent  # noqa: E402

LOST = MaybeSent(0, None, "ReadTimeout: lost")


def test_followup1_maybe_sent_never_retried(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.ig.fail_dm[env.m.followup_1] = LOST
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "check whether it went out" in r.note
    assert env.dms() == []  # follow-up 2 not sent either
    del env.ig.fail_dm[env.m.followup_1]
    env.advance(minutes=5)
    process_replies(env.ctx)
    resume_followups(env.ctx)
    assert env.dms() == [] and not env.db.kv_get(REPLY_IN_FLIGHT_KEY)


def test_followup2_maybe_sent_never_retried(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.ig.fail_dm[env.m.followup_2] = LOST
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "check whether it went out" in r.note
    assert env.dms() == [("dm", igsid, env.m.followup_1)]
    del env.ig.fail_dm[env.m.followup_2]
    env.advance(minutes=5)
    resume_followups(env.ctx)
    process_replies(env.ctx)
    assert env.dms() == [("dm", igsid, env.m.followup_1)]


def test_got_it_maybe_sent_captured_with_note(env):
    igsid = env.live()
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=2)
    for line in env.m.got_it:
        env.ig.fail_dm[line] = LOST
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.EMAIL_CAPTURED and "check whether it went out" in r.note
    assert [e.email for e in env.db.emails()] == ["sarah@gmail.com"]


def test_reply_a_few_seconds_before_dm1_time_is_seen(env):
    """Instagram's clock may run a little behind the Mac's."""
    igsid = env.live()
    sent = env.db.get("c1").dm1_sent_at
    env.jev.reply_probs["yes pls"] = YES
    env.ig.add_user_message(igsid, "yes pls", sent - timedelta(seconds=3))
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_capture_queues_email_two_minutes_later(env):
    igsid = env.live()
    env.ig.display_names[igsid] = "Sam Lee"
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    process_replies(env.ctx)
    env.advance(minutes=1)
    env.say(igsid, "sarah@x.com")
    newest = env.t
    env.advance(minutes=2)
    process_replies(env.ctx)
    (row,) = env.db.emails()
    assert row.send_state == "queued" and row.name == "Sam"
    assert row.send_after == newest + timedelta(minutes=2)



def _capture_with_after_got_it(env):
    from dataclasses import replace
    env.cfg = replace(env.cfg, email=replace(env.cfg.email, after_got_it_seconds=(20.0, 30.0)))
    env.ctx.config = env.cfg
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    process_replies(env.ctx)
    env.advance(minutes=1)
    env.say(igsid, "sarah@x.com")
    newest = env.t
    env.advance(minutes=2)
    return newest


def test_email_goes_20_to_30_seconds_after_the_got_it(env):
    _capture_with_after_got_it(env)
    process_replies(env.ctx)
    got_it = env.ig.sent[-1][2]
    assert got_it in env.cfg.messages.got_it
    (row,) = env.db.emails()
    assert row.send_state == "queued"
    assert env.t + timedelta(seconds=20) <= row.send_after <= env.t + timedelta(seconds=30)


def test_email_keeps_the_old_time_when_the_got_it_fails(env):
    newest = _capture_with_after_got_it(env)
    for text in env.cfg.messages.got_it:
        env.ig.fail_dm[text] = InstagramError(400, 10, "outside the window")
    process_replies(env.ctx)
    (row,) = env.db.emails()
    assert row.send_after == newest + timedelta(minutes=2)


# ---- acting human: Seen, typing…, and quick answers while someone is chatting

def _yes_and_followups(env):
    """They say yes at normal pace; the bot sends both follow-ups. Returns igsid, send time."""
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    sent_at = env.t
    env.ig.sent.clear()
    env.ig.actions.clear()
    env.sleeps.clear()
    return igsid, sent_at


def test_reply_goes_seen_then_typing_then_message(env):
    igsid = env.live()
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.ig.actions == [(igsid, "mark_seen", 0), (igsid, "typing_on", 0),
                              (igsid, "typing_on", 1)]  # typing… again before follow-up 2
    (p1, n1), (p2, n2), (p3, n3) = env.sleeps
    assert 2 <= p1 <= 4 and 3 <= p2 <= 9 and 4 <= p3 <= 12
    assert (n1, n2, n3) == (0, 0, 1)


def test_seen_and_typing_failing_never_stops_the_reply(env):
    igsid = env.live()
    env.ig.fail_actions = InstagramError(500, None, "boom")
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_no_seen_when_the_bot_hands_the_chat_to_alex(env):
    igsid = env.live()
    env.say(igsid, "who is this?")  # unknown to Jev: handed to Alex, nothing sent
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.NEEDS_OWNER
    assert env.ig.actions == []


def test_someone_chatting_is_answered_in_seconds(env):
    igsid, sent_at = _yes_and_followups(env)
    env.set(sent_at + timedelta(seconds=40))
    env.say(igsid, "sarah@gmail.com")  # 40 s after the bot's last message: chatting
    env.advance(seconds=4)
    assert process_replies(env.ctx) == 0  # a moment in case they're adding something
    env.advance(seconds=2)
    assert process_replies(env.ctx) == 1
    assert len(env.dms()) == 1 and env.status() is Status.EMAIL_CAPTURED


def test_a_slow_answer_keeps_the_normal_wait(env):
    igsid, sent_at = _yes_and_followups(env)
    env.set(sent_at + timedelta(seconds=150))
    env.say(igsid, "sarah@gmail.com")  # 2.5 min after the bot: not chatting
    env.advance(seconds=30)
    assert process_replies(env.ctx) == 0
    env.advance(seconds=61)
    assert process_replies(env.ctx) == 1
    assert len(env.dms()) == 1


# ---- quick checks between the one-minute ticks

from igdm.engine import chatting, idle  # noqa: E402


def run_idle(env, seconds, stop_after=None):
    """Run idle() with a wait that moves the clock. Returns the waits it asked for."""
    waits = []

    def wait(s):
        waits.append(s)
        env.advance(seconds=s)
        return stop_after is not None and len(waits) >= stop_after
    idle(env.ctx, seconds, wait)
    return waits


def _count(env, name):
    calls = []
    orig = getattr(env.ig, name)
    setattr(env.ig, name, lambda *a: (calls.append(a), orig(*a))[1])
    return calls


def test_chatting_is_who_the_bot_wrote_to_in_the_last_3_minutes(env):
    igsid, sent_at = _yes_and_followups(env)
    assert chatting(env.ctx, sent_at + timedelta(minutes=2)) == {igsid: sent_at}
    assert chatting(env.ctx, sent_at + timedelta(seconds=181)) == {}


def test_chatting_leaves_out_conversations_that_are_over(env):
    igsid, sent_at = _yes_and_followups(env)
    env.db.update("c1", status=Status.NEEDS_OWNER)
    assert chatting(env.ctx, sent_at + timedelta(seconds=30)) == {}


def test_quick_check_reads_a_thread_again_only_when_it_changes(env):
    igsid, sent_at = _yes_and_followups(env)
    reads = _count(env, "list_messages")
    env.set(sent_at + timedelta(seconds=12))
    assert process_replies(env.ctx, only=chatting(env.ctx, env.t)) == 0
    assert len(reads) == 1  # one look: only the bot's own message so far
    env.advance(seconds=12)
    assert process_replies(env.ctx, only=chatting(env.ctx, env.t)) == 0
    assert len(reads) == 1  # nothing changed in the thread, so it isn't read again
    env.say(igsid, "sarah@gmail.com")
    env.advance(seconds=12)
    assert process_replies(env.ctx, only=chatting(env.ctx, env.t)) == 1
    assert len(reads) == 2 and env.status() is Status.EMAIL_CAPTURED


def test_quick_check_skips_everyone_else_and_keeps_the_watermark(env):
    igsid, sent_at = _yes_and_followups(env)
    other = env.live("c2", username="bob")  # DM 1 went out 3 min ago: not chatting
    env.say(other, "yes pls")
    env.jev.reply_probs["yes pls"] = YES
    env.advance(minutes=2)
    env.db.kv_set(WATERMARK_KEY, "2026-11-03T00:00:00+00:00")
    hot = {igsid: sent_at}  # as if only sarah were chatting
    assert process_replies(env.ctx, only=hot) == 0
    assert env.status("c2") is Status.AWAITING_YES and env.ig.sent == []
    assert env.db.kv_get(WATERMARK_KEY) == "2026-11-03T00:00:00+00:00"


def test_idle_just_waits_when_nobody_is_chatting(env):
    env.live()
    env.advance(minutes=5)
    assert run_idle(env, 60) == [60]


def test_idle_answers_someone_chatting_within_seconds(env):
    igsid, sent_at = _yes_and_followups(env)
    env.say(igsid, "sarah@gmail.com", after=timedelta(seconds=3))
    waits = run_idle(env, 60)
    assert env.status() is Status.EMAIL_CAPTURED and len(env.dms()) == 1
    (at,) = [r["sent_at"] for r in env.db.rows(
        "SELECT sent_at FROM sent_messages WHERE sent_at > ?", (sent_at.isoformat(),))]
    assert datetime.fromisoformat(at) - sent_at <= timedelta(seconds=3 + 15)
    assert all(10 <= w <= 15 for w in waits[:-1]) and abs(sum(waits) - 60) < 1e-6


def test_idle_stops_quick_checks_3_minutes_after_the_bot_wrote(env):
    igsid, sent_at = _yes_and_followups(env)
    env.set(sent_at + timedelta(seconds=170))
    waits = run_idle(env, 60)
    assert 10 <= waits[0] <= 15 and len(waits) == 2  # one quick check, then out to the tick
    assert abs(sum(waits) - 60) < 1e-6


def test_idle_sends_a_first_dm_when_it_is_due(env):
    env.ig.comments.setdefault(MEDIA, []).append(Comment("c1", "Tokyo", "sarah", None, env.t))
    ingest_comments(env.ctx)
    due = env.db.get("c1").dm_due_at
    run_idle(env, 90, stop_after=2)  # wakes when it's due, sends, then stops
    assert env.db.get("c1").dm1_sent_at == due and env.status() is Status.AWAITING_YES
    assert [s[0] for s in env.ig.sent] == ["private", "public"]


def test_idle_returns_when_the_bot_is_stopping(env):
    igsid, sent_at = _yes_and_followups(env)
    assert len(run_idle(env, 60, stop_after=1)) == 1


# ---- review fixes: quiet hours mid-reply, pausing between ticks, failed quick checks

QUIET = datetime(2026, 11, 4, 2, 0, 1, tzinfo=SGT).astimezone(timezone.utc)


def _late_yes(env):
    """DM 1 went out; they say yes just before quiet hours start (02:00 SGT)."""
    igsid = env.live()
    env.set(datetime(2026, 11, 4, 1, 57, tzinfo=SGT).astimezone(timezone.utc))
    env.jev.reply_probs["yes pls"] = YES
    env.say(igsid, "yes pls")
    env.set(datetime(2026, 11, 4, 1, 59, 50, tzinfo=SGT).astimezone(timezone.utc))
    return igsid


def test_quiet_hours_starting_during_seen_and_typing_holds_the_reply(env):
    igsid = _late_yes(env)
    env.ctx.sleep = lambda s: env.set(QUIET)  # the Seen pause runs past 02:00
    assert process_replies(env.ctx) == 0
    assert env.ig.sent == [] and env.status() is Status.AWAITING_YES
    assert env.db.kv_get("followup_failures:c1") is None  # not counted as a failure
    env.ctx.sleep = env.sleep
    env.set(datetime(2026, 11, 4, 8, 31, tzinfo=SGT).astimezone(timezone.utc))
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_quiet_hours_starting_between_the_two_followups_holds_the_second(env):
    igsid = _late_yes(env)

    def sleep(s):
        if env.ig.sent:  # follow-up 1 is out; the gap runs past 02:00
            env.set(QUIET)
    env.ctx.sleep = sleep
    assert process_replies(env.ctx) == 1
    assert env.ig.sent == [("dm", igsid, env.m.followup_1)]
    assert env.status() is Status.FOLLOWUP1_SENT
    assert env.db.kv_get("followup_failures:c1") is None
    env.ctx.sleep = env.sleep
    assert resume_followups(env.ctx) == 0  # still quiet
    env.set(datetime(2026, 11, 4, 8, 31, tzinfo=SGT).astimezone(timezone.utc))
    assert resume_followups(env.ctx) == 1
    assert env.ig.sent == followups(env, igsid)


def test_quiet_hours_stop_the_rest_of_the_conversations(env):
    a = env.live("c1", username="sarah")
    b = env.live("c2", username="bob")
    env.set(datetime(2026, 11, 4, 1, 57, tzinfo=SGT).astimezone(timezone.utc))
    env.jev.reply_probs["yes pls"] = YES
    env.say(a, "yes pls")
    env.say(b, "yes pls")
    env.set(datetime(2026, 11, 4, 1, 59, 50, tzinfo=SGT).astimezone(timezone.utc))

    send = env.ig.send_message

    def send_then_quiet(*a):
        mid = send(*a)
        if len(env.ig.sent) == 2:  # the first conversation is done; now it's quiet
            env.set(QUIET)
        return mid
    env.ig.send_message = send_then_quiet
    process_replies(env.ctx)
    assert len(env.ig.sent) == 2  # one conversation answered, the other held
    assert sorted([env.status("c1"), env.status("c2")]) == sorted(
        [Status.AWAITING_EMAIL, Status.AWAITING_YES])
    assert env.jev.calls.count(("reply", "yes pls")) == 1  # nothing spent on the held one


def test_pausing_a_reel_stops_quick_checks_before_the_next_tick(env):
    from dataclasses import replace
    env.ig.comments.setdefault(MEDIA, []).append(Comment("c1", "Tokyo", "sarah", None, env.t))
    ingest_comments(env.ctx)
    paused = replace(env.cfg, reels=[replace(env.cfg.reels[0], paused=True)])

    def reload():  # what the dashboard's Pause does, picked up before each quick action
        env.ctx.config = paused
    waits = []

    def wait(s):
        waits.append(s)
        env.advance(seconds=s)
        return len(waits) >= 3
    idle(env.ctx, 90, wait, before=reload)
    assert env.ig.sent == [] and env.status() is Status.QUEUED


def test_a_failed_quick_check_stops_quick_checks_until_the_tick(env):
    igsid, sent_at = _yes_and_followups(env)
    lists = []

    def down(*a):
        lists.append(1)
        raise InstagramError(0, None, "ConnectError: no internet")
    env.ig.list_conversations = down
    waits = run_idle(env, 60)
    assert len(lists) == 1  # tried once, then waited for the tick
    assert abs(sum(waits) - 60) < 1e-6 and len(waits) == 2


def test_chat_checks_never_spin_even_if_configured_to_zero(env):
    from dataclasses import replace
    env.ctx.config = replace(env.cfg, pacing=replace(env.cfg.pacing, chat_poll_seconds=(0.0, 0.0)))
    igsid, sent_at = _yes_and_followups(env)
    waits = run_idle(env, 10)
    assert all(w >= 1 for w in waits[:-1]) and len(waits) <= 11


def test_idle_sends_a_due_email_within_seconds(env):
    from fakes import FakeMailer
    env.ctx.mailer = FakeMailer()
    reel = env.cfg.reels[0]
    env.db.add_email(comment_id="manual:x", handle="", igsid=None, email="m@x.com",
                     provided_at=env.t, reel_url=reel.url, keyword=reel.keyword, name="Marwane",
                     send_after=env.t + timedelta(seconds=2), by_owner=True)
    waits = run_idle(env, 60, stop_after=3)
    assert [to for to, _, _ in env.ctx.mailer.sent] == ["m@x.com"]
    assert waits[0] <= 5


# -- made-up email addresses are flagged, not sent to
def test_made_up_email_is_flagged(env):
    igsid = env.live()
    env.jev.email_probs["email@totallyreal.com"] = 0.1
    env.say(igsid, "email@totallyreal.com")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_INFO
    assert r.flag_reason == "the address looks made up: email@totallyreal.com"
    assert env.db.emails() == [] and env.ig.sent == []
    assert env.jev.calls == [("email", "email@totallyreal.com")]


def test_real_email_still_sent(env):
    igsid = env.live()
    env.jev.email_probs["sarah@gmail.com"] = 0.95
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.EMAIL_CAPTURED
    assert [e.email for e in env.db.emails()] == ["sarah@gmail.com"]
    [(kind, who, text)] = env.ig.sent
    assert who == igsid and text in env.m.got_it


def test_email_check_down_still_sends(env):
    igsid = env.live()
    env.jev.raise_on.add("sarah@gmail.com")
    env.say(igsid, "sarah@gmail.com")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.EMAIL_CAPTURED
    assert [e.email for e in env.db.emails()] == ["sarah@gmail.com"]
    assert len(env.ig.sent) == 1
    assert [ev for _, ev, _ in env.db.events("c1")].count("jev_error") == 1


def test_same_email_reuse_not_checked(env):
    igsid = _repeat_commenter(env)
    env.jev.email_probs["old@x.com"] = 0.0  # would be flagged if it were checked
    env.jev.reply_probs["yep same one"] = YES
    env.say(igsid, "yep same one")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1
    assert env.status() is Status.EMAIL_CAPTURED
    assert all(c[0] != "email" for c in env.jev.calls)
