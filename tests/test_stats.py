import json
from datetime import datetime, timedelta, timezone

import pytest
from fakes import repo_config

from igdm.config import Price
from igdm.db import Database, Request, Status
from igdm.engine import IG_BLOCKED_KEY
from igdm.stats import dashboard_state

NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)  # 10:00 SGT


@pytest.fixture
def cfg():
    from dataclasses import replace
    c = repo_config()
    return replace(c, prices={"gpt-6-luna": Price(per_m_input=1.0, per_m_output=2.0)})


@pytest.fixture
def db():
    return Database(":memory:")


def req(db, cid, status, username=None, kind="normal", **kw):
    kw.setdefault("commented_at", NOW - timedelta(hours=5))
    db.insert_request(Request(comment_id=cid, reel_key="tokyo", media_id="m1",
                              username=username or cid, commenter_id=None, text="Tokyo",
                              status=status, kind=kind, **kw))


def email(db, cid, at=NOW, **kw):
    return db.add_email(comment_id=cid, handle=cid, igsid=None, email=f"{cid}@x.com",
                        provided_at=at, reel_url="https://www.instagram.com/reel/TESTREEL01/",
                        keyword="Tokyo", **kw)


def state(db, cfg):
    return dashboard_state(db, cfg, NOW)


def test_cards_counts(db, cfg):
    req(db, "a", Status.EMAIL_CAPTURED)
    email(db, "a")
    req(db, "b", Status.AWAITING_EMAIL, last_user_msg_at=NOW - timedelta(hours=1))
    req(db, "c", Status.AWAITING_YES)  # no reply yet: not mid-conversation
    db.log_usage("openai", "gpt-6-luna", "reply", input_tokens=1_000_000)
    cards = state(db, cfg)["cards"]
    assert cards["emails"] == 1 and cards["mid_conversation"] == 1
    assert cards["spend_usd"] == pytest.approx(1.0) and cards["spend_missing_price"] is False


def test_flags_include_info_you_and_email_failures(db, cfg):
    req(db, "a", Status.AWAITING_EMAIL)
    db.flag("a", "info", "email is in their bio or somewhere else", "find it")
    db.update("a", last_their_text="my email is in my bio")
    req(db, "b", Status.AWAITING_YES)
    db.flag("b", "you", "not about the material", "reply yourself")
    req(db, "c", Status.EMAIL_CAPTURED)
    i = email(db, "c", send_after=NOW)
    db.mark_email(i, "failed", error="550 no such user")
    req(db, "d", Status.EMAIL_CAPTURED)
    email(db, "d")  # manual: captured before auto-send
    flags = state(db, cfg)["flags"]
    by = {f["handle"]: f for f in flags}
    assert by["a"]["kind"] == "info" and by["a"]["quote"] == "my email is in my bio"
    assert by["a"]["can_carry_on"] is True and by["a"]["reel"] == "Tokyo"
    assert by["b"]["kind"] == "you" and by["b"]["reason"] == "not about the material"
    assert by["c"]["kind"] == "you" and by["c"]["email_id"] == i and "550" in by["c"]["reason"]
    assert by["d"]["email_id"] is not None and "Send now" in by["d"]["todo"]
    assert all(f["thread_url"] == "https://www.instagram.com/direct/inbox/" for f in flags)


def test_left_alone_not_in_flags(db, cfg):
    req(db, "x", Status.LEFT_FOR_OWNER)
    s = state(db, cfg)
    assert s["flags"] == [] and s["left_alone"]["count"] == 1
    assert s["left_alone"]["items"][0]["handle"] == "x"


def test_funnel_counts_per_reel(db, cfg):
    req(db, "a", Status.EMAIL_CAPTURED, dm1_sent_at=NOW, last_user_msg_at=NOW)
    i = email(db, "a", send_after=NOW)
    db.mark_email(i, "sent", at=NOW)
    req(db, "b", Status.AWAITING_YES, dm1_sent_at=NOW)
    req(db, "c", Status.QUEUED)
    req(db, "h", Status.AWAITING_YES, kind="hand", dm1_sent_at=NOW)
    req(db, "p", Status.PRE_CUTOFF)
    req(db, "l", Status.LEFT_FOR_OWNER)
    (f,) = state(db, cfg)["funnel"]
    assert f == {"key": "tokyo", "reel": "Tokyo", "since": "2026-11-02T17:00:00+00:00",
                 "commented": 3, "dmed": 3, "hand": 1, "replied": 1, "gave_email": 1, "emailed": 1}


def test_funnel_lists_the_newest_reel_first(db, cfg):
    from dataclasses import replace
    old = cfg.reels[0]
    new = replace(old, key="ai", keyword="AI", url="https://www.instagram.com/reel/TESTREEL02/",
                  handle_comments_after=old.handle_comments_after + timedelta(days=2))
    funnel = state(db, replace(cfg, reels=[old, new]))["funnel"]
    assert [f["key"] for f in funnel] == ["ai", "tokyo"]


def test_emails_per_day_has_14_days(db, cfg):
    email(db, "a", at=NOW)
    email(db, "b", at=NOW - timedelta(days=1))
    email(db, "c", at=NOW - timedelta(days=1, hours=1))
    days = state(db, cfg)["emails_per_day"]
    assert len(days) == 14 and days[-1] == {"date": "2026-11-04", "count": 1}
    assert days[-2] == {"date": "2026-11-03", "count": 2}


def test_email_table(db, cfg):
    i = email(db, "a", send_after=NOW)
    db.mark_email(i, "sent", at=NOW)
    (row,) = state(db, cfg)["emails"]
    assert row["email"] == "a@x.com" and row["state"] == "sent" and row["reel"] == "Tokyo"


def test_mid_window_left(db, cfg):
    req(db, "a", Status.AWAITING_EMAIL, last_user_msg_at=NOW - timedelta(hours=20),
        last_their_text="sure")
    (m,) = state(db, cfg)["mid"]
    assert m["window_left_minutes"] == 240 and m["last_text"] == "sure" and m["last_by"] == "them"


def test_mid_shows_bot_message_when_newer(db, cfg):
    req(db, "a", Status.AWAITING_EMAIL, last_user_msg_at=NOW - timedelta(hours=2),
        last_their_text="sure")
    db.record_sent("mid1", "u", "a", "what's your email?", NOW - timedelta(hours=1))
    (m,) = state(db, cfg)["mid"]
    assert m["last_text"] == "what's your email?" and m["last_by"] == "bot"


def test_no_reply_split_by_kind(db, cfg):
    req(db, "bot1", Status.AWAITING_YES, dm1_sent_at=NOW - timedelta(hours=3))
    req(db, "hand1", Status.AWAITING_YES, kind="hand", dm1_sent_at=NOW - timedelta(days=1))
    nr = state(db, cfg)["no_reply"]
    assert [x["handle"] for x in nr["bot"]] == ["bot1"]
    assert [x["handle"] for x in nr["hand"]] == ["hand1"]


def test_spend_by_model_and_missing_price(db, cfg):
    db.log_usage("openai", "gpt-6-luna", "reply", input_tokens=500_000, output_tokens=500_000)
    db.log_usage("typesafe", "jev-latest", "route")
    db.log_usage("manual", "gpt-6-luna", "one-off", usd_override=0.4,
                 at=NOW - timedelta(days=10))
    s = state(db, cfg)
    sp = s["spend"]
    assert sp["total"] == pytest.approx(1.9) and sp["last_7_days"] == pytest.approx(1.5)
    by = {m["model"]: m for m in sp["by_model"]}
    assert by["jev-latest"]["missing_price"] is True
    assert s["cards"]["spend_missing_price"] is True


def test_ig_blocked(db, cfg):
    assert state(db, cfg)["ig_blocked_since"] is None
    db.kv_set(IG_BLOCKED_KEY, NOW.isoformat())
    assert state(db, cfg)["ig_blocked_since"] == NOW.isoformat()


def test_state_is_json_serialisable(db, cfg):
    req(db, "a", Status.AWAITING_EMAIL, last_user_msg_at=NOW)
    db.flag("a", "you", "x", "y")
    email(db, "a")
    json.dumps(state(db, cfg))


def test_dms_per_day(db, cfg):
    req(db, "a", Status.AWAITING_YES, dm1_sent_at=NOW)
    req(db, "b", Status.AWAITING_YES, dm1_sent_at=NOW - timedelta(days=1))
    req(db, "h", Status.AWAITING_YES, kind="hand", dm1_sent_at=NOW)  # hand DMs aren't the bot's
    days = state(db, cfg)["dms_per_day"]
    assert len(days) == 14 and days[-1] == {"date": "2026-11-04", "count": 1}
    assert days[-2] == {"date": "2026-11-03", "count": 1}


def test_paused_skipped_not_counted_as_comment(db, cfg):
    req(db, "p1", Status.PAUSED_SKIPPED)
    f = dashboard_state(db, cfg, NOW)["funnel"][0]
    assert f["commented"] == 0


# ---- emails Alex added by hand on the dashboard (Send material) count in the funnel

def manual(db, cfg, handle, email_addr, keyword="Tokyo", sent=True):
    reel = next((r for r in cfg.reels if r.keyword == keyword), cfg.reels[0])
    i = db.add_manual_email(name="X", handle=handle, email=email_addr, now=NOW,
                            reel_url=reel.url if reel.keyword == keyword else "https://x/", keyword=keyword)
    if sent:
        db.mark_email(i, "sent", at=NOW)
    return i


def counts(db, cfg):
    (f,) = state(db, cfg)["funnel"]
    return {k: f[k] for k in ("dmed", "hand", "replied", "gave_email", "emailed")}


def test_hand_added_email_counts_for_someone_already_dmed(db, cfg):
    req(db, "a", Status.DONE_BY_OWNER, username="calvinchoo.dev", dm1_sent_at=NOW, last_user_msg_at=NOW)
    manual(db, cfg, "CalvinChoo.dev", "calvin@x.com")
    assert counts(db, cfg) == {"dmed": 1, "hand": 0, "replied": 1, "gave_email": 1, "emailed": 1}


def test_hand_added_email_without_a_conversation_is_a_new_person(db, cfg):
    manual(db, cfg, "", "someone@x.com", sent=False)
    assert counts(db, cfg) == {"dmed": 1, "hand": 1, "replied": 1, "gave_email": 1, "emailed": 0}


def test_hand_added_email_never_counts_a_person_twice(db, cfg):
    req(db, "a", Status.EMAIL_CAPTURED, username="sam", dm1_sent_at=NOW, last_user_msg_at=NOW)
    email(db, "a")  # the bot already captured sam's email
    manual(db, cfg, "sam", "sam@x.com")  # and Alex sent it again by hand
    manual(db, cfg, "", "new@x.com")
    manual(db, cfg, "", "NEW@x.com")  # sent twice to the same address
    c = counts(db, cfg)
    assert (c["dmed"], c["gave_email"]) == (2, 2)


def test_hand_added_email_only_counts_for_its_own_reel(db, cfg):
    manual(db, cfg, "", "other@x.com", keyword="Other")
    assert counts(db, cfg)["gave_email"] == 0


def test_cheeky_comment_is_a_flag_with_a_link_to_the_reel(db, cfg):
    req(db, "x", Status.LEFT_FOR_OWNER, flag_reason="the keyword came with an insult, a joke or an unrelated ask",
        flag_todo="reply to the comment yourself if you want, or leave it")
    req(db, "y", Status.LEFT_FOR_OWNER)
    s = state(db, cfg)
    (f,) = s["flags"]
    assert f["handle"] == "x" and f["kind"] == "you" and "insult" in f["reason"]
    assert f["quote"] == "Tokyo" and f["on_reel"] == cfg.reels[0].url
    assert f["can_carry_on"] is False
    assert s["left_alone"]["count"] == 2


def test_dm_flags_have_no_reel_link(db, cfg):
    req(db, "b", Status.AWAITING_YES)
    db.flag("b", "you", "not about the material", "reply yourself")
    assert state(db, cfg)["flags"][0]["on_reel"] is None
