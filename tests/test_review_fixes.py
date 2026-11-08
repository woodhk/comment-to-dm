"""Fixes from the final review: each test reproduces one finding."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fakes import repo_config
from test_handdm import HAND, HEnv
from test_replies import REnv, route

from igdm.db import Status
from igdm.engine import (
    OWNER_HANDLING, ingest_comments, process_replies, resume_followups, send_due_dm,
    send_pending_gotits,
)
from igdm.instagram import Comment, MaybeSent


@pytest.fixture
def env():
    return HEnv()


# 1. carry-on must never re-send a follow-up 2 that may already have gone out
def test_carry_on_from_followup1_does_not_resend_followup2(env):
    igsid = env.live()
    env.jev.route_probs["yes pls"] = route(yes=0.95)
    env.ig.fail_dm[env.m.followup_2] = MaybeSent(0, None, "timeout")
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    process_replies(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.paused_from is Status.FOLLOWUP1_SENT
    del env.ig.fail_dm[env.m.followup_2]
    env.ig.sent.clear()
    assert env.db.carry_on("c1", env.t)
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_EMAIL and r.asks >= 1
    env.advance(minutes=1)
    resume_followups(env.ctx)
    assert env.dms() == []


# 2. the got-it after a pasted email backs off if Alex already replied by hand
def test_gotit_skipped_when_alex_replied_by_hand(env):
    igsid = env.asked()
    env.reply(igsid, "use my work email", route(email_elsewhere=0.95))
    env.advance(minutes=3)
    env.ig.add_own_message(igsid, "found it, sending now", env.t)
    env.advance(minutes=1)
    reel = env.cfg.reels[0]
    env.db.resolve_with_email("c1", "w@work.com", now=env.t, delay_minutes=2,
                              reel_url=reel.url, keyword=reel.keyword)
    env.ig.sent.clear()
    send_pending_gotits(env.ctx)
    assert env.dms() == []
    assert env.status() is Status.EMAIL_CAPTURED
    assert env.db.emails()[0].send_state == "queued"  # the material still goes out


def test_gotit_still_sent_when_alex_did_not_reply(env):
    igsid = env.asked()
    env.reply(igsid, "use my work email", route(email_elsewhere=0.95))
    env.advance(minutes=3)
    reel = env.cfg.reels[0]
    env.db.resolve_with_email("c1", "w@work.com", now=env.t, delay_minutes=2,
                              reel_url=reel.url, keyword=reel.keyword)
    env.ig.sent.clear()
    send_pending_gotits(env.ctx)
    assert len(env.dms()) == 1


# 3. pending / mark-sent never touch emails the bot is about to send
def test_pending_and_mark_sent_skip_queued(env):
    t = env.t
    q = env.db.add_email(comment_id="q", handle="q", igsid=None, email="q@x.com", provided_at=t,
                         reel_url="r", keyword="Tokyo", send_after=t + timedelta(minutes=2))
    m = env.db.add_email(comment_id="m", handle="m", igsid=None, email="m@x.com", provided_at=t,
                         reel_url="r", keyword="Tokyo")
    assert [e.id for e in env.db.emails(pending_only=True)] == [m]
    assert env.db.mark_emailed(None, t) == 1
    assert env.db.email_by_id(q).send_state == "queued"
    assert env.db.mark_emailed([q], t) == 0


# 4. an email already sitting in a hand thread at import is not auto-sent
def test_backlog_email_in_hand_thread_is_manual(env):
    env.hand("igsid-bob", "bob")
    env.advance(hours=1)
    env.say("igsid-bob", "neater pls! bob@x.com")
    env.advance(hours=3)
    from igdm.handdm import import_hand_dms
    import_hand_dms(env.ctx)
    process_replies(env.ctx)
    send_pending_gotits(env.ctx)
    r = env.row("igsid-bob")
    assert r.status is Status.EMAIL_CAPTURED and env.dms() == []
    (e,) = env.db.emails()
    assert e.email == "bob@x.com" and e.send_state == "manual"


# 5. the AI prompt's extra facts come from the reel, not a hard-coded Tokyo line
def test_llm_prompt_uses_reel_notes():
    from igdm.llm import _system
    reel = repo_config().reels[0]
    assert "short checklist" in reel.ai_notes
    owner = repo_config().owner
    assert "short checklist" in _system(reel, owner)
    assert "short checklist" not in _system(replace(reel, ai_notes=""), owner)


# 6. Done is per request: the person is handled normally on a later reel
def test_done_does_not_block_future_requests(env):
    igsid = env.live()
    env.reply(igsid, "partnership?", route(off_topic=0.9))
    env.db.mark_done("c1", env.t)
    r = env.db.get("c1")
    assert r.status not in OWNER_HANDLING and r.status is Status.DONE_BY_OWNER
    env.advance(days=2)
    env.db.update("c1", reel_key="older-reel")  # pretend the first one was another reel
    env.ig.comments.setdefault("m1", []).append(Comment("c2", "Tokyo", "sarah", None, env.t))
    ingest_comments(env.ctx)
    for _ in range(60):
        env.advance(minutes=1)
        if send_due_dm(env.ctx):
            break
    assert env.status("c2") is Status.AWAITING_YES


# 7. a mistyped address is flagged instead of emailed
def test_mistyped_email_flags_info(env):
    igsid = env.asked()
    env.say(igsid, "sarah@gmial.com")
    env.advance(minutes=2)
    process_replies(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_INFO and "gmail.com" in r.flag_reason
    assert env.db.emails() == [] and env.dms() == []


@pytest.mark.parametrize("addr,fix", [("a@gmial.com", "gmail.com"), ("a@gmail.co", "gmail.com"),
                                      ("a@hotmial.com", "hotmail.com"), ("a@yaho.com", "yahoo.com"),
                                      ("a@gmail.com", None), ("a@work.co.uk", None),
                                      ("a@x.com", None), ("a@icloud.com", None)])
def test_suspect_domain(addr, fix):
    from igdm.messages import suspect_domain
    assert suspect_domain(addr) == fix
