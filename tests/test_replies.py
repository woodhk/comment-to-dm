"""The reply bank and routing pipeline (replies.enabled = True)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from test_engine_replies import YES, Env

from igdm.db import Status
from igdm.engine import REPLY_IN_FLIGHT_KEY, process_replies


def route(**probs):
    return dict(probs)


class REnv(Env):
    def __init__(self):
        super().__init__()
        self.cfg = replace(self.cfg, replies=replace(self.cfg.replies, enabled=True))
        self.ctx.config = self.cfg

    def entry(self, id_):
        return next(e for e in self.cfg.replies.bank if e.id == id_)

    def reply(self, igsid, text, probs):
        self.jev.route_probs[text] = probs
        self.say(igsid, text)
        self.advance(minutes=2)
        return process_replies(self.ctx)

    def asked(self, cid="c1", username="sarah"):
        """Live conversation that has had the email ask (AWAITING_EMAIL, asks == 1)."""
        igsid = self.live(cid, username)
        self.reply(igsid, "yes pls", route(yes=0.95))
        assert self.status(cid) is Status.AWAITING_EMAIL
        self.ig.sent.clear()
        self.jev.calls.clear()
        self.advance(minutes=1)
        return igsid


@pytest.fixture
def env():
    return REnv()


def test_yes_in_offer_sends_followup_pair(env):
    igsid = env.live()
    assert env.reply(igsid, "yes pls", route(yes=0.95)) == 1
    assert env.dms() == [("dm", igsid, env.m.followup_1), ("dm", igsid, env.m.followup_2)]
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_EMAIL and r.asks == 1
    assert r.last_their_text == "yes pls"


def test_bank_entry_reply_moves_to_awaiting_email(env):
    igsid = env.live()
    env.reply(igsid, "the neater one pls", route(neater=0.9))
    (dm,) = env.dms()
    assert dm[2] in env.entry("neater").replies
    r = env.db.get("c1")
    assert r.status is Status.AWAITING_EMAIL and r.asks == 1


def test_asked_stage_bank_reply_increments_asks(env):
    igsid = env.asked()
    env.reply(igsid, "sure", route(asked_yes=0.9))
    (dm,) = env.dms()
    assert dm[2] in env.entry("asked_yes").replies
    assert env.db.get("c1").asks == 2 and env.status() is Status.AWAITING_EMAIL


def test_cap_flags_after_three_asks(env):
    igsid = env.asked()
    env.db.update("c1", asks=3)
    env.reply(igsid, "hmm", route(asked_yes=0.9))
    assert env.dms() == []
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.flag_reason == "asked 3 times, no email"
    assert ("route", "hmm") not in env.jev.calls  # capped before asking Jev


def test_decline_goes_declined(env):
    igsid = env.live()
    env.reply(igsid, "nah all good", route(decline=0.9))
    assert env.dms() == [] and env.status() is Status.DECLINED


def test_email_elsewhere_flags_info(env):
    igsid = env.asked()
    env.reply(igsid, "yeah sure my email is in my bio", route(email_elsewhere=0.92))
    r = env.db.get("c1")
    assert env.dms() == []
    assert r.status is Status.NEEDS_INFO
    assert r.flag_reason == "email is in their bio or somewhere else"
    assert r.flag_todo == "find their email (check their bio) and paste it on the dashboard"
    assert r.last_their_text == "yeah sure my email is in my bio"
    assert r.paused_from is Status.AWAITING_EMAIL


@pytest.mark.parametrize("kind,reason", [("off_topic", "not about the material"),
                                         ("upset", "complaint or upset")])
def test_off_topic_and_upset_flag_you_without_reply(env, kind, reason):
    igsid = env.live()
    env.reply(igsid, "let's build an AI company together", route(**{kind: 0.9}))
    r = env.db.get("c1")
    assert env.dms() == [] and r.status is Status.NEEDS_OWNER and r.flag_reason == reason


def test_flagged_message_not_reprocessed(env):
    igsid = env.live()
    env.reply(igsid, "partnership?", route(off_topic=0.9))
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0


def test_low_confidence_material_goes_custom(env):
    igsid = env.live()
    env.ctx.writer = None
    env.reply(igsid, "is it the slides?", route(neater=0.4, custom=0.45, off_topic=0.15))
    assert env.db.get("c1").flag_reason == "nothing in the bank fits"


def test_low_confidence_unclear_flags_you(env):
    igsid = env.live()
    env.reply(igsid, "??", route(neater=0.3, off_topic=0.35, upset=0.35))
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.flag_reason == "not sure what they mean"


def test_custom_without_writer_flags_you(env):
    igsid = env.live()
    env.reply(igsid, "do you have the robotics one?", route(custom=0.9))
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.flag_reason == "nothing in the bank fits"


def test_only_stage_entries_offered(env):
    igsid = env.live()
    env.reply(igsid, "yes pls", route(yes=0.95))
    offer_opts = env.jev.route_options[-1]
    assert "asked_yes" not in offer_opts and "yes" in offer_opts and "custom" in offer_opts
    env.reply(igsid, "ok", route(asked_yes=0.9))
    asked_opts = env.jev.route_options[-1]
    assert "yes" not in asked_opts and "asked_yes" in asked_opts


def test_thread_passed_to_jev(env):
    igsid = env.live()
    env.reply(igsid, "yes pls", route(yes=0.95))
    thread = env.jev.route_threads[-1]
    name = env.ctx.config.owner.name
    assert name == "Sam" and thread[0]["from"] == name and thread[-1] == {"from": "them", "text": "yes pls"}


def test_jev_failure_defers(env):
    igsid = env.live()
    env.jev.raise_on.add("yes pls")
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0 and env.status() is Status.AWAITING_YES


def test_email_still_captured_directly(env):
    igsid = env.asked()
    env.say(igsid, "sarah@x.com")
    env.advance(minutes=2)
    process_replies(env.ctx)
    assert env.status() is Status.EMAIL_CAPTURED
    assert ("route", "sarah@x.com") not in env.jev.calls


def test_disabled_keeps_old_behaviour():
    env = Env()  # replies disabled in the repo config
    igsid = env.live()
    env.jev.reply_probs["what's in it?"] = {"yes": 0.1, "no": 0.1, "other": 0.8}
    env.say(igsid, "what's in it?")
    env.advance(minutes=2)
    process_replies(env.ctx)
    assert env.status() is Status.NEEDS_OWNER and env.ig.sent == []
    assert all(c[0] != "route" for c in env.jev.calls)


def test_interrupted_bank_send_recovery(env):
    igsid = env.asked()
    env.db.kv_set(REPLY_IN_FLIGHT_KEY, "reply:c1:1")  # asks is still 1: it never finished
    process_replies(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and "interrupted" in r.note


def test_finished_bank_send_marker_is_ignored(env):
    igsid = env.asked()
    env.db.update("c1", asks=2)
    env.db.kv_set(REPLY_IN_FLIGHT_KEY, "reply:c1:1")  # asks moved past 1: it finished
    process_replies(env.ctx)
    assert env.status() is Status.AWAITING_EMAIL and env.db.kv_get(REPLY_IN_FLIGHT_KEY) == ""


# ---- AI-written replies (Task 7)

from fakes import FakeWriter  # noqa: E402

from igdm.llm import LlmError, LlmReply  # noqa: E402

CLEAN = "yes the robotics part is all in there, what's your email and i'll get it to you?"


def ok(text=CLEAN, confident=True, promise=False):
    return LlmReply(text, confident, promise, 100, 20)


def test_custom_reply_sent(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([ok()])
    env.reply(igsid, "do you have the robotics one?", route(custom=0.9))
    assert env.dms() == [("dm", igsid, CLEAN)]
    r = env.db.get("c1")
    assert r.llm_replies == 1 and r.asks == 1 and r.status is Status.AWAITING_EMAIL


def test_custom_regenerates_once_on_voice_problem(env):
    igsid = env.live()
    w = env.ctx.writer = FakeWriter([ok("sure thing — what's your email?"), ok()])
    env.reply(igsid, "robotics?", route(custom=0.9))
    assert env.dms() == [("dm", igsid, CLEAN)]
    assert w.calls[0] is None and any("dash" in p for p in w.calls[1])


def test_custom_twice_bad_flags(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([ok("Quick update: it's ready"), ok("All done. Sent over.")])
    env.reply(igsid, "robotics?", route(custom=0.9))
    r = env.db.get("c1")
    assert env.dms() == [] and r.status is Status.NEEDS_OWNER
    assert r.flag_reason == "the AI reply broke your voice rules twice"


def test_custom_empty_reply_retried_then_flagged(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([ok("  "), ok("")])
    env.reply(igsid, "robotics?", route(custom=0.9))
    assert env.dms() == [] and env.status() is Status.NEEDS_OWNER


def test_custom_not_confident_flags(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([ok(confident=False)])
    env.reply(igsid, "robotics?", route(custom=0.9))
    assert env.dms() == []
    assert env.db.get("c1").flag_reason == "the AI wasn't sure how to answer"


def test_custom_new_promise_flags(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([ok(promise=True)])
    env.reply(igsid, "can we hop on a call?", route(custom=0.9))
    assert env.dms() == []
    assert env.db.get("c1").flag_reason == "the AI reply would promise something new"


def test_llm_error_flags_and_tick_continues(env):
    a = env.live("c1", "sarah")
    b = env.live("c2", "bob")
    env.ctx.writer = FakeWriter([LlmError("non-JSON body")])
    env.jev.route_probs["robotics?"] = route(custom=0.9)
    env.jev.route_probs["yes pls"] = route(yes=0.95)
    env.say(a, "robotics?")
    env.say(b, "yes pls")
    env.advance(minutes=2)
    process_replies(env.ctx)
    r = env.db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.flag_reason.startswith("AI reply failed")
    assert ("dm", a, CLEAN) not in env.dms()
    assert env.status("c2") is Status.AWAITING_EMAIL  # the other conversation still went on


def test_writer_unexpected_exception_flags(env):
    igsid = env.live()
    env.ctx.writer = FakeWriter([RuntimeError("weird")])
    env.reply(igsid, "robotics?", route(custom=0.9))
    assert env.status() is Status.NEEDS_OWNER and env.dms() == []


def test_llm_cap(env):
    igsid = env.asked()
    env.db.update("c1", llm_replies=2)
    w = env.ctx.writer = FakeWriter([ok()])
    env.reply(igsid, "robotics?", route(custom=0.9))
    assert w.calls == [] and env.dms() == []
    assert env.db.get("c1").flag_reason == "AI already answered twice"


# ---- quiet hours cover replies too (Alex, 4 Nov)

def test_replies_wait_for_quiet_hours_to_end(env):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    sgt = ZoneInfo("Asia/Singapore")
    igsid = env.live()
    env.set(datetime(2026, 11, 4, 2, 30, tzinfo=sgt).astimezone(timezone.utc))
    env.jev.route_probs["yes pls"] = route(yes=0.95)
    env.say(igsid, "yes pls")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 0 and env.dms() == []
    env.set(datetime(2026, 11, 4, 8, 31, tzinfo=sgt).astimezone(timezone.utc))
    assert process_replies(env.ctx) == 1 and len(env.dms()) == 2


def test_followup2_waits_for_quiet_hours(env):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from igdm.engine import resume_followups
    sgt = ZoneInfo("Asia/Singapore")
    igsid = env.live()
    env.db.update("c1", status=Status.FOLLOWUP1_SENT, last_user_msg_at=env.t)
    env.set(datetime(2026, 11, 4, 3, 0, tzinfo=sgt).astimezone(timezone.utc))
    env.db.update("c1", last_user_msg_at=env.t - timedelta(hours=1))
    assert resume_followups(env.ctx) == 0 and env.dms() == []
