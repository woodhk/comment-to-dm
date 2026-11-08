"""Pausing a reel freezes everything the bot does for it; resuming picks up where it left off."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fakes import FakeMailer
from test_engine_replies import MEDIA
from test_handdm import HEnv
from test_replies import route

from igdm.db import Status
from igdm.engine import (
    PAUSED_AT_PREFIX, WATERMARK_KEY, ingest_comments, process_replies, resume_followups,
    send_due_dm, send_pending_gotits, sync_pauses,
)
from igdm.handdm import HAND_SCAN_KEY, import_hand_dms
from igdm.instagram import Comment
from igdm.outbox import send_due_emails

KEY = PAUSED_AT_PREFIX + "tokyo"


@pytest.fixture
def env():
    return HEnv()


def pause(env, on=True):
    cfg = env.ctx.config
    env.ctx.config = replace(cfg, reels=[replace(r, paused=on) if r.key == "tokyo" else r
                                         for r in cfg.reels])
    sync_pauses(env.ctx)


def comment(env, cid="c1", username="sarah", at=None):
    env.ig.comments.setdefault(MEDIA, []).append(Comment(cid, "Tokyo", username, None, at or env.t))


def dm_goes_out(env, tries=60):
    for _ in range(tries):
        env.advance(minutes=1)
        if send_due_dm(env.ctx):
            return True
    return False


def add_email(env, cid="e1", **kw):
    return env.db.add_email(comment_id=cid, handle="sarah", igsid="u1", email="s@x.com",
                            provided_at=env.t, reel_url=env.ctx.config.reels[0].url,
                            keyword="Tokyo", name="Sam", **kw)


def test_sync_pauses_records_time_then_clears_on_resume(env):
    at = env.t
    pause(env)
    assert env.db.kv_get(KEY) == at.isoformat()
    env.advance(hours=1)
    sync_pauses(env.ctx)
    assert env.db.kv_get(KEY) == at.isoformat()  # not moved forward
    pause(env, False)
    assert env.db.kv_get(KEY) == ""


def test_resume_lowers_reply_and_hand_watermarks(env):
    at = env.t
    pause(env)
    later = (at + timedelta(hours=2)).isoformat()
    env.db.kv_set(WATERMARK_KEY, later)
    env.db.kv_set(HAND_SCAN_KEY, later)
    env.advance(hours=3)
    pause(env, False)
    assert env.db.kv_get(WATERMARK_KEY) == at.isoformat()
    assert env.db.kv_get(HAND_SCAN_KEY) == at.isoformat()


def test_resume_leaves_earlier_watermarks_alone(env):
    early = (env.t - timedelta(hours=1)).isoformat()
    env.db.kv_set(WATERMARK_KEY, early)
    pause(env)
    env.advance(hours=3)
    pause(env, False)
    assert env.db.kv_get(WATERMARK_KEY) == early
    assert env.db.kv_get(HAND_SCAN_KEY) is None  # a missing watermark stays missing


def test_no_dm1_while_paused_then_sent_after_resume(env):
    comment(env)
    ingest_comments(env.ctx)
    pause(env)
    assert not dm_goes_out(env)
    assert env.status() is Status.QUEUED and env.ig.sent == []
    pause(env, False)
    assert dm_goes_out(env)


def test_comment_during_pause_is_ignored_for_good(env):
    pause(env)
    env.advance(minutes=1)
    comment(env)
    ingest_comments(env.ctx)
    assert env.status() is Status.PAUSED_SKIPPED
    assert not [c for c in env.jev.calls if c[0] == "comment"]
    pause(env, False)
    ingest_comments(env.ctx)
    assert not dm_goes_out(env)
    assert env.ig.sent == []


def test_comment_from_before_pause_is_kept_for_resume(env):
    comment(env, at=env.t - timedelta(minutes=1))
    pause(env)
    ingest_comments(env.ctx)
    assert not env.db.has_comment("c1")
    pause(env, False)
    ingest_comments(env.ctx)
    assert env.status() is Status.QUEUED


def test_reply_during_pause_waits_then_handled_after_resume(env):
    igsid = env.live()
    pause(env)
    env.jev.route_probs["yes pls"] = route(yes=0.95)
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0 and env.dms() == []
    env.advance(hours=1)
    pause(env, False)
    assert process_replies(env.ctx) == 1
    assert env.dms() == [("dm", igsid, env.m.followup_1), ("dm", igsid, env.m.followup_2)]


def test_followup2_not_resumed_while_paused(env):
    igsid = env.live()
    env.db.update("c1", status=Status.FOLLOWUP1_SENT, last_user_msg_at=env.t, igsid=igsid)
    pause(env)
    assert resume_followups(env.ctx) == 0 and env.dms() == []
    pause(env, False)
    assert resume_followups(env.ctx) == 1
    assert env.dms() == [("dm", igsid, env.m.followup_2)]


def test_hand_dm_thread_not_picked_up_while_paused_then_picked_up(env):
    pause(env)
    env.hand("igsid-tom", "tom")
    env.advance(hours=1)
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 0
    pause(env, False)
    env.advance(minutes=11)
    assert import_hand_dms(env.ctx) == 1


def test_queued_email_held_while_paused(env):
    env.ctx.mailer = mail = FakeMailer()
    i = add_email(env, send_after=env.t)
    pause(env)
    assert send_due_emails(env.ctx) == 0 and mail.sent == []
    assert env.db.email_by_id(i).send_state == "queued"
    pause(env, False)
    assert send_due_emails(env.ctx) == 1


def test_send_now_email_goes_out_while_paused(env):
    env.ctx.mailer = mail = FakeMailer()
    i = add_email(env)
    pause(env)
    assert env.db.queue_now(i, env.t)
    assert send_due_emails(env.ctx) == 1 and len(mail.sent) == 1


def test_pasted_email_and_gotit_go_out_while_paused(env):
    env.ctx.mailer = mail = FakeMailer()
    igsid = env.live()
    env.db.flag("c1", "info", "email in bio", "paste it", newest=env.t)
    pause(env)
    env.db.resolve_with_email("c1", "s@x.com", now=env.t, delay_minutes=0,
                              reel_url=env.ctx.config.reels[0].url, keyword="Tokyo")
    assert send_due_emails(env.ctx) == 1 and len(mail.sent) == 1
    assert send_pending_gotits(env.ctx) == 1
    assert [d[1] for d in env.dms()] == [igsid]


def test_other_reel_unaffected_by_pause(env):
    sz = env.ctx.config.reels[0]
    rome = replace(sz, key="rome", url="https://www.instagram.com/reel/ROME1/", shortcode="ROME1",
                   keyword="Rome")
    env.ctx.config = replace(env.ctx.config, reels=[replace(sz, paused=True), rome])
    env.ctx.media_ids["rome"] = "m-rome"
    sync_pauses(env.ctx)
    env.ig.comments["m-rome"] = [Comment("r1", "Rome", "ann", None, env.t)]
    ingest_comments(env.ctx)
    assert dm_goes_out(env)
    assert env.db.get("r1").status is Status.AWAITING_YES


def test_comment_again_after_resume_gets_a_dm(env):
    pause(env)
    env.advance(minutes=1)
    comment(env, "c1")
    ingest_comments(env.ctx)
    pause(env, False)
    env.advance(minutes=1)
    comment(env, "c2")
    ingest_comments(env.ctx)
    assert env.db.get("c2").status is Status.QUEUED
    assert dm_goes_out(env)
