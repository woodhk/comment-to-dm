"""Threads Alex started by hand get picked up for the AI to continue."""

from datetime import timedelta

import pytest
from test_replies import REnv, route

from igdm.db import Status
from igdm.engine import WATERMARK_KEY, ingest_comments, process_replies, send_due_dm
from igdm.handdm import HAND_SCAN_INTERVAL, import_hand_dms
from igdm.instagram import Comment

HAND = "hey, from that Tokyo reel. I can send the quick link or put together a neater printable version. Up to you!"


class HEnv(REnv):
    def hand(self, igsid, username=None, at=None):
        self.ig.usernames[igsid] = username or igsid.removeprefix("igsid-")
        return self.ig.add_own_message(igsid, HAND, at or self.t)

    def row(self, igsid):
        return self.db.get(f"hand:tokyo:{igsid}")


@pytest.fixture
def env():
    return HEnv()


def test_imports_thread_where_they_replied_last(env):
    env.hand("igsid-tom", "tom")
    env.advance(hours=1)
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 1
    r = env.row("igsid-tom")
    assert r.status is Status.AWAITING_YES and r.kind == "hand" and r.username == "tom"
    assert r.igsid == "igsid-tom" and r.dm1_text == HAND
    env.jev.route_probs["Yes"] = route(yes=0.95)
    assert process_replies(env.ctx) == 1
    assert env.dms() == [("dm", "igsid-tom", env.m.followup_1),
                         ("dm", "igsid-tom", env.m.followup_2)]


def test_imports_unanswered_hand_dm_as_waiting(env):
    env.hand("igsid-amy")
    env.advance(minutes=5)
    assert import_hand_dms(env.ctx) == 1
    assert env.row("igsid-amy").status is Status.AWAITING_YES
    assert process_replies(env.ctx) == 0 and env.dms() == []
    env.advance(hours=2)
    env.jev.route_probs["yes please"] = route(yes=0.95)
    env.say("igsid-amy", "yes please")
    env.advance(minutes=2)
    assert process_replies(env.ctx) == 1 and len(env.dms()) == 2


def test_back_and_forth_where_alex_spoke_last_is_handled_by_owner(env):
    env.hand("igsid-kim")
    env.advance(minutes=10)
    env.say("igsid-kim", "raw is fine")
    env.advance(minutes=10)
    env.ig.add_own_message("igsid-kim", "here you go", env.t)
    env.advance(minutes=5)
    import_hand_dms(env.ctx)
    r = env.row("igsid-kim")
    assert r.status is Status.HANDLED_BY_OWNER and "already talking" in r.note


def test_neater_thread_uses_seen_until(env):
    env.hand("igsid-neo")
    env.advance(minutes=10)
    env.say("igsid-neo", "Ok wait for a neater version")
    env.advance(minutes=1)
    env.say("igsid-neo", "Thanks")
    env.advance(minutes=5)
    promise = env.ig.add_own_message(
        "igsid-neo", "Alright, I'll send it as soon as I finish. If I haven't in 1-2 days remind my ass", env.t)
    env.advance(minutes=3)
    env.say("igsid-neo", "Thank you will do")
    env.advance(minutes=2)
    import_hand_dms(env.ctx)
    r = env.row("igsid-neo")
    assert r.status is Status.AWAITING_YES
    env.jev.route_probs["Thank you will do"] = route(neater=0.9)
    process_replies(env.ctx)
    assert env.jev.calls[-1] == ("route", "Thank you will do")  # only the unanswered message
    thread = env.jev.route_threads[-1]
    assert [m["text"] for m in thread][:2] == [HAND, "Ok wait for a neater version"]
    assert len(thread) == 5
    (dm,) = env.dms()
    assert dm[2] in env.entry("neater").replies


def test_skips_threads_without_phrase(env):
    env.ig.add_own_message("igsid-x", "hey what's up", env.t)
    env.advance(minutes=1)
    env.say("igsid-x", "yo")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 0
    assert not env.db.has_comment("hand:tokyo:igsid-x")


def test_skips_people_with_existing_requests(env):
    igsid = env.live()  # the bot already DMed sarah
    env.hand(igsid)
    env.advance(minutes=1)
    env.say(igsid, "yes")
    env.advance(minutes=11)
    assert import_hand_dms(env.ctx) == 0


def test_never_imports_twice(env):
    env.hand("igsid-tom")
    env.advance(minutes=1)
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 1
    env.advance(minutes=20)
    env.say("igsid-tom", "hello?")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 0


def test_lowers_reply_watermark(env):
    env.hand("igsid-tom")
    env.advance(hours=2)
    said = env.t
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    env.db.kv_set(WATERMARK_KEY, env.t.isoformat())  # replies already looked past it
    import_hand_dms(env.ctx)
    from datetime import datetime
    assert datetime.fromisoformat(env.db.kv_get(WATERMARK_KEY)) < said


def test_scan_interval_and_thread_cap(env):
    for i in range(25):
        env.hand(f"igsid-p{i}")
        env.advance(seconds=10)
    env.advance(minutes=1)
    assert import_hand_dms(env.ctx) == 20
    env.advance(minutes=5)
    assert import_hand_dms(env.ctx) == 0  # inside the 10 minute interval
    env.advance(minutes=HAND_SCAN_INTERVAL.total_seconds() / 60)
    assert import_hand_dms(env.ctx) == 5


def test_off_when_ai_replies_off():
    from test_engine_replies import Env
    env = Env()
    env.ig.usernames["igsid-tom"] = "tom"
    env.ig.add_own_message("igsid-tom", HAND, env.t)
    env.advance(minutes=1)
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    assert import_hand_dms(env.ctx) == 0


def test_hand_rows_do_not_block_dm1_cap(env):
    for i in range(30):
        env.hand(f"igsid-h{i}")
        env.advance(seconds=5)
    env.advance(minutes=1)
    import_hand_dms(env.ctx)
    env.advance(minutes=11)
    import_hand_dms(env.ctx)
    assert sum(1 for r in env.db.with_status(Status.AWAITING_YES) if r.kind == "hand") == 30
    env.ig.comments.setdefault("m1", []).append(Comment("c9", "Tokyo", "zoe", None, env.t))
    ingest_comments(env.ctx)
    sent = False
    for _ in range(60):
        env.advance(minutes=1)
        if send_due_dm(env.ctx):
            sent = True
            break
    assert sent


def test_username_from_conversation_when_lookup_fails(env):
    env.hand("igsid-tom", "tom")
    env.ig.get_username = lambda user_id: None  # Instagram won't look it up by id
    env.advance(minutes=1)
    env.say("igsid-tom", "Yes")
    env.advance(minutes=2)
    import_hand_dms(env.ctx)
    assert env.row("igsid-tom").username == "tom"
