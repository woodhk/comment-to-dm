"""The dashboard's activity feed: what the bot did, newest first, with repeated errors folded."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fakes import repo_config

from igdm.activity import HEARTBEAT_KEY, activity
from igdm.db import Database, Request, Status

NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg():
    return repo_config()


@pytest.fixture
def db():
    return Database(":memory:")


def req(db, cid, status=Status.AWAITING_YES, **kw):
    kw.setdefault("commented_at", NOW - timedelta(hours=5))
    db.insert_request(Request(comment_id=cid, reel_key="tokyo", media_id="m1",
                              username=kw.pop("username", cid), commenter_id=None,
                              text="Tokyo", status=status, **kw))


def ago(minutes):
    return NOW - timedelta(minutes=minutes)


def feed(db, cfg, **kw):
    return activity(db, cfg, NOW, **kw)


def test_first_dm_and_dm_reply_are_told_apart(db, cfg):
    req(db, "c1", username="maya", dm1_text="hey maya", dm1_sent_at=ago(30), igsid="u1")
    db.record_sent("m1", "u1", "c1", "hey maya", ago(30))
    db.record_sent("m2", "u1", "c1", "what's your email?", ago(10))
    items = feed(db, cfg)["items"]
    assert [(i["type"], i["handle"], i["detail"]) for i in items] == [
        ("dm_reply", "maya", "what's your email?"),
        ("dm", "maya", "hey maya"),
    ]
    assert items[0]["at"] == ago(10).isoformat()
    assert items[0]["reel"] == "Tokyo"


def test_public_reply_shows_who_it_was_for(db, cfg):
    req(db, "c1", username="maya")
    db.record_public_reply("check dms", ago(5), comment_id="c1")
    db.record_public_reply("sent!", ago(6))  # from before comment ids were kept
    items = feed(db, cfg)["items"]
    assert [(i["type"], i["handle"], i["detail"]) for i in items] == [
        ("public_reply", "maya", "check dms"),
        ("public_reply", None, "sent!"),
    ]


def test_email_captured_and_sent(db, cfg):
    eid = db.add_email(comment_id="c1", handle="maya", igsid=None, email="maya@x.com",
                       provided_at=ago(20), reel_url="https://www.instagram.com/reel/TESTREEL01/",
                       keyword="Tokyo")
    db.mark_email(eid, "sent", at=ago(18))
    items = feed(db, cfg)["items"]
    assert [(i["type"], i["handle"], i["detail"]) for i in items] == [
        ("email_sent", "maya", "maya@x.com"),
        ("email_captured", "maya", "maya@x.com"),
    ]


def test_handed_to_alex_is_listed_but_other_status_changes_are_not(db, cfg):
    req(db, "c1", username="maya", status=Status.NEEDS_OWNER, flag_reason="asked for a call")
    db.log_event("c1", "status:awaiting_yes->needs_owner", at=ago(3))
    db.log_event("c1", "status:queued->awaiting_yes", at=ago(40))
    items = feed(db, cfg)["items"]
    assert [(i["type"], i["handle"], i["detail"]) for i in items] == [
        ("flagged", "maya", "asked for a call")]


def test_errors_have_a_plain_title_level_and_reason(db, cfg):
    db.log_event(None, "list_comments_failed", "tokyo: connection refused", at=ago(2))
    db.log_event(None, "rate_limited", "too many calls", at=ago(4))
    a, b = feed(db, cfg)["items"]
    assert (a["type"], a["level"], a["title"], a["detail"]) == (
        "error", "error", "Couldn't check comments", "tokyo: connection refused")
    assert (b["level"], b["title"]) == ("warn", "Instagram asked the bot to slow down")


def test_repeated_errors_fold_into_one_row(db, cfg):
    for m in range(10, 70, 2):  # 30 failures, one every 2 minutes
        db.log_event(None, "list_conversations_failed", f"dns fail {m}", at=ago(m))
    items = feed(db, cfg)["items"]
    assert len(items) == 1
    one = items[0]
    assert one["count"] == 30
    assert one["at"] == ago(10).isoformat() and one["first_at"] == ago(68).isoformat()
    assert one["detail"] == "dns fail 10"  # the latest reason


def test_errors_split_by_something_else_happening_stay_separate(db, cfg):
    db.log_event(None, "list_comments_failed", "x", at=ago(1))
    req(db, "c1", username="maya", dm1_text="hey", dm1_sent_at=ago(2), igsid="u1")
    db.record_sent("m1", "u1", "c1", "hey", ago(2))
    db.log_event(None, "list_comments_failed", "x", at=ago(3))
    assert [i["type"] for i in feed(db, cfg)["items"]] == ["error", "dm", "error"]


def test_paging_with_before(db, cfg):
    for m in range(1, 8):
        db.record_public_reply(f"r{m}", ago(m))
    first = feed(db, cfg, limit=3)
    assert [i["detail"] for i in first["items"]] == ["r1", "r2", "r3"]
    assert first["more"] is True
    second = feed(db, cfg, limit=3, before=first["next"])
    assert [i["detail"] for i in second["items"]] == ["r4", "r5", "r6"]
    third = feed(db, cfg, limit=3, before=second["next"])
    assert [i["detail"] for i in third["items"]] == ["r7"] and third["more"] is False


def test_health_ok_after_a_clean_check(db, cfg):
    db.kv_set(HEARTBEAT_KEY, json.dumps({"start": ago(2).isoformat(), "end": ago(1).isoformat()}))
    db.log_event(None, "list_comments_failed", "old", at=ago(30))  # before that check
    h = feed(db, cfg)["health"]
    assert h["state"] == "ok" and h["last_check"] == ago(1).isoformat()


def test_health_trouble_when_the_last_check_hit_an_error(db, cfg):
    db.kv_set(HEARTBEAT_KEY, json.dumps({"start": ago(2).isoformat(), "end": ago(1).isoformat()}))
    db.log_event(None, "list_conversations_failed", "dns", at=ago(1.5))
    h = feed(db, cfg)["health"]
    assert h["state"] == "trouble" and h["error"] == "Couldn't check DMs"


def test_health_stopped_when_no_check_for_a_while(db, cfg):
    db.kv_set(HEARTBEAT_KEY, json.dumps({"start": ago(61).isoformat(), "end": ago(60).isoformat()}))
    assert feed(db, cfg)["health"]["state"] == "stopped"


def test_health_unknown_before_the_first_check(db, cfg):
    assert feed(db, cfg)["health"]["state"] == "unknown"


def test_interleaved_errors_in_one_outage_fold_by_kind(db, cfg):
    for m in range(10, 40):  # an outage: DM checks every minute, comment checks every 5
        db.log_event(None, "list_conversations_failed", "dns", at=ago(m))
        if m % 5 == 0:
            db.log_event(None, "list_comments_failed", "dns", at=ago(m) - timedelta(seconds=1))
    items = feed(db, cfg)["items"]
    assert [(i["title"], i["count"]) for i in items] == [
        ("Couldn't check DMs", 30), ("Couldn't check comments", 6)]
    assert items[1]["at"] == (ago(10) - timedelta(seconds=1)).isoformat()


@pytest.mark.parametrize("raw, plain", [
    ("tokyo: instagram error status=0 code=None: ConnectError: [Errno 8] nodename nor "
     "servname provided, or not known", "No internet connection. The server couldn't reach the internet."),
    ("instagram error status=0 code=None: ReadTimeout: The read operation timed out",
     "Instagram took too long to answer."),
    ("instagram error status=500 code=1: Please reduce the amount of data",
     "Instagram had a problem on its side."),
])
def test_common_errors_read_in_plain_words(db, cfg, raw, plain):
    db.log_event(None, "list_comments_failed", raw, at=ago(1))
    (item,) = feed(db, cfg)["items"]
    assert item["detail"] == plain and item["raw"] == raw


def test_other_errors_keep_their_own_text(db, cfg):
    db.log_event(None, "list_comments_failed", "something new", at=ago(1))
    (item,) = feed(db, cfg)["items"]
    assert item["detail"] == "something new" and item["raw"] == "something new"


def test_a_page_never_splits_an_outage(db, cfg):
    db.record_public_reply("newest", ago(1))
    for m in range(10, 40):
        db.log_event(None, "list_conversations_failed", "dns", at=ago(m))
        if m % 5 == 0:
            db.log_event(None, "list_comments_failed", "dns", at=ago(m) - timedelta(seconds=1))
    db.record_public_reply("oldest", ago(50))
    first = feed(db, cfg, limit=2)  # the limit falls inside the outage's two rows
    assert [(i["type"], i["count"]) for i in first["items"]] == [
        ("public_reply", 1), ("error", 30), ("error", 6)]
    second = feed(db, cfg, limit=2, before=first["next"])
    assert [i["detail"] for i in second["items"]] == ["oldest"]
