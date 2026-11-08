"""What the dashboard buttons do, and the got-it DM after Alex pastes an email."""

from datetime import timedelta

import pytest
from test_replies import REnv, route

from igdm.db import Status
from igdm.engine import REPLY_IN_FLIGHT_KEY, process_replies, send_pending_gotits


@pytest.fixture
def env():
    return REnv()


def info_flagged(env):
    igsid = env.asked()
    env.reply(igsid, "my email is in my bio", route(email_elsewhere=0.95))
    assert env.status() is Status.NEEDS_INFO
    env.ig.sent.clear()
    return igsid


def resolve(env, email="sarah@x.com"):
    reel = env.cfg.reels[0]
    return env.db.resolve_with_email("c1", email, now=env.t, delay_minutes=2,
                                     reel_url=reel.url, keyword=reel.keyword, name="Sarah")


def test_resolve_with_email_queues_and_gotit_sent(env):
    igsid = info_flagged(env)
    i = resolve(env)
    assert env.status() is Status.GOTIT_PENDING
    assert env.db.get("c1").note == "email added by you: sarah@x.com"
    row = env.db.email_by_id(i)
    assert row.send_state == "queued" and row.send_after == env.t + timedelta(minutes=2)
    assert row.email == "sarah@x.com" and row.name == "Sarah"
    assert send_pending_gotits(env.ctx) == 1
    (dm,) = env.dms()
    assert dm[1] == igsid and dm[2] in env.m.got_it
    assert env.status() is Status.EMAIL_CAPTURED
    assert send_pending_gotits(env.ctx) == 0 and len(env.dms()) == 1


def test_resolve_with_email_window_closed_no_dm(env):
    info_flagged(env)
    resolve(env)
    env.advance(hours=25)
    send_pending_gotits(env.ctx)
    assert env.dms() == [] and env.status() is Status.EMAIL_CAPTURED


def test_gotit_waits_for_quiet_hours_to_end(env):
    info_flagged(env)
    resolve(env)
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    env.set(datetime(2026, 11, 4, 2, 0, tzinfo=ZoneInfo("Asia/Singapore")).astimezone(timezone.utc))
    env.db.update("c1", last_user_msg_at=env.t - timedelta(hours=1))
    assert send_pending_gotits(env.ctx) == 0 and env.status() is Status.GOTIT_PENDING


def test_interrupted_gotit_not_resent(env):
    info_flagged(env)
    resolve(env)
    env.db.kv_set(REPLY_IN_FLIGHT_KEY, "gotit:c1")
    send_pending_gotits(env.ctx)
    assert env.dms() == [] and env.status() is Status.EMAIL_CAPTURED


def test_carry_on_ignores_alex_reply_before_press(env):
    igsid = env.asked()
    env.reply(igsid, "can you do a call", route(off_topic=0.9))
    assert env.status() is Status.NEEDS_OWNER
    env.advance(minutes=5)
    env.ig.add_own_message(igsid, "sorry no calls but happy to send the slides", env.t)
    env.advance(minutes=1)
    assert env.db.carry_on("c1", env.t) is True
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_EMAIL and r.asks == 0 and r.seen_until == env.t
    assert r.note == "handed back to the bot by you"
    env.advance(minutes=2)
    process_replies(env.ctx)
    assert env.status() is Status.AWAITING_EMAIL  # Alex's reply wasn't taken as a new hand reply
    env.reply(igsid, "ok sure", route(asked_yes=0.9))
    (dm,) = env.dms()
    assert dm[2] in env.entry("asked_yes").replies


def test_carry_on_refused_without_live_paused_from(env):
    env.live()
    env.db.update("c1", status=Status.HANDLED_BY_OWNER)
    env.db.update("c1", status=Status.NEEDS_OWNER)  # from a non-live status: nothing to resume
    assert env.db.carry_on("c1", env.t) is False
    assert env.status() is Status.NEEDS_OWNER


def test_mark_done(env):
    igsid = env.live()
    env.reply(igsid, "partnership?", route(off_topic=0.9))
    env.db.mark_done("c1", env.t)
    r = env.db.get("c1")
    assert r.status is Status.DONE_BY_OWNER and r.note == "done by you on the dashboard"


def test_send_now_requeues_manual(env):
    i = env.db.add_email(comment_id="old", handle="peder", igsid="u", email="p@x.com",
                         provided_at=env.t, reel_url="r", keyword="Tokyo")
    assert env.db.send_now(i, env.t) is True
    assert env.db.email_by_id(i).send_state == "queued"
