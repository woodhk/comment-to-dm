import itertools
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from igdm import alerts
from igdm.alerts import EmailNotifier, FanOut, NtfyNotifier, check_alerts
from igdm.db import Database
from igdm.engine import IG_BLOCKED_KEY

T0 = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


class Rec:
    def __init__(self):
        self.got: list[tuple[str, str]] = []

    def notify(self, title, body):
        self.got.append((title, body))

    @property
    def titles(self):
        return [t for t, _ in self.got]


class Boom:
    def notify(self, title, body):
        raise RuntimeError("channel down")


@pytest.fixture
def db():
    return Database(":memory:")


def fail(db, event, detail, at):
    db.log_event("c1", event, detail, at=at)


def sent_dm(db, at):
    db.record_sent(f"m{next(_ids)}", "igsid-1", "c1", "hi", at)


def sent_email(db, at):
    i = db.add_email(comment_id=f"c{next(_ids)}", handle="h", igsid=None, email="a@b.com",
                     provided_at=at, reel_url="u", keyword="k", send_after=at)
    db.mark_email(i, "sent", at=at)


def run(db, rec, at):
    return check_alerts(db, at, rec)


def test_meta_blocked_alerts_immediately_then_fixed(db):
    rec = Rec()
    db.kv_set(IG_BLOCKED_KEY, T0.isoformat())
    assert run(db, rec, T0) == ["Instagram blocked the bot"]
    assert "Log in to Facebook" in rec.got[0][1]
    assert run(db, rec, T0 + timedelta(minutes=1)) == []
    db.kv_set(IG_BLOCKED_KEY, "")
    assert run(db, rec, T0 + timedelta(minutes=2)) == ["Instagram unblocked the bot"]


def test_dead_token_alerts_as_meta(db):
    rec = Rec()
    fail(db, "phase_failed", "ingest: InstagramError: instagram error status=400 code=190: bad",
         T0)
    assert run(db, rec, T0 + timedelta(seconds=5)) == ["Instagram blocked the bot"]


def test_single_bad_email_does_not_alert(db):
    rec = Rec()
    fail(db, "email_failed", "gmail refused it: SMTPRecipientsRefused", T0)
    assert run(db, rec, T0 + timedelta(seconds=5)) == []


def test_three_email_failures_alert(db):
    rec = Rec()
    for i in range(3):
        fail(db, "email_failed", "gmail refused it: x", T0 + timedelta(minutes=i))
        run(db, rec, T0 + timedelta(minutes=i, seconds=5))
    assert rec.titles == ["Emails are failing"]


def test_email_failures_for_15_minutes_alert(db):
    rec = Rec()
    fail(db, "email_failed", "gmail refused it: x", T0)
    run(db, rec, T0 + timedelta(seconds=5))
    fail(db, "email_failed", "gmail refused it: x", T0 + timedelta(minutes=16))
    run(db, rec, T0 + timedelta(minutes=16, seconds=5))
    assert rec.titles == ["Emails are failing"]


def test_success_resets_streak(db):
    rec = Rec()
    for i, what in enumerate(["fail", "fail", "ok", "fail"]):
        at = T0 + timedelta(minutes=i)
        if what == "fail":
            fail(db, "email_failed", "gmail refused it: x", at)
        else:
            sent_email(db, at)
        run(db, rec, at + timedelta(seconds=5))
    assert rec.titles == []


def test_email_alert_clears_on_next_sent_email(db):
    rec = Rec()
    for i in range(3):
        fail(db, "email_failed", "gmail refused it: x", T0 + timedelta(minutes=i))
    run(db, rec, T0 + timedelta(minutes=3))
    sent_email(db, T0 + timedelta(minutes=4))
    assert run(db, rec, T0 + timedelta(minutes=5)) == ["Emails are going out again"]


def test_gmail_login_failure_alerts_immediately(db):
    rec = Rec()
    fail(db, "email_failed", "gmail login failed: SMTPAuthenticationError: 535", T0)
    assert run(db, rec, T0 + timedelta(seconds=5)) == ["Gmail isn't sending"]


def test_gmail_unreachable_three_times_alerts(db):
    rec = Rec()
    for i in range(3):
        fail(db, "email_failed", "could not connect to smtp.gmail.com: TimeoutError",
             T0 + timedelta(minutes=i))
    assert run(db, rec, T0 + timedelta(minutes=3)) == ["Emails are failing"]


def test_dm_failures_alert_and_clear(db):
    rec = Rec()
    for i in range(3):
        fail(db, "got_it_failed", "gotit: instagram error status=400 code=10: x",
             T0 + timedelta(minutes=i))
    assert run(db, rec, T0 + timedelta(minutes=3)) == ["DMs are failing"]
    sent_dm(db, T0 + timedelta(minutes=4))
    assert run(db, rec, T0 + timedelta(minutes=5)) == ["DMs are going out again"]


def test_ai_and_jev_failures(db):
    rec = Rec()
    for i in range(3):
        fail(db, "llm_error", "APIError: out of credit", T0 + timedelta(minutes=i))
        fail(db, "jev_error", "JevError: 500", T0 + timedelta(minutes=i))
    assert sorted(run(db, rec, T0 + timedelta(minutes=3))) == [
        "AI replies are failing", "The request checker (Jev or OpenAI) can't be reached"]
    db.log_usage("openai", "gpt-6-luna", "reply", at=T0 + timedelta(minutes=4))
    assert run(db, rec, T0 + timedelta(minutes=5)) == ["AI replies are working again"]
    db.log_usage("typesafe", "jev-latest", "comment", at=T0 + timedelta(minutes=6))
    assert run(db, rec, T0 + timedelta(minutes=7)) == ["The request checker is back"]


def test_rate_limited_three_in_an_hour(db):
    rec = Rec()
    for m in (0, 25, 50):
        fail(db, "rate_limited", "x", T0 + timedelta(minutes=m))
        run(db, rec, T0 + timedelta(minutes=m, seconds=5))
    assert rec.titles == ["Instagram keeps slowing the bot down"]


def test_rate_limited_spread_out_does_not_alert(db):
    rec = Rec()
    for m in (0, 61, 122):
        fail(db, "rate_limited", "x", T0 + timedelta(minutes=m))
        run(db, rec, T0 + timedelta(minutes=m, seconds=5))
    assert rec.titles == []


def test_phase_failed_three_ticks_alerts(db):
    rec = Rec()
    for i in range(3):
        fail(db, "phase_failed", "ingest: ConnectError: Network is unreachable",
             T0 + timedelta(minutes=i))
        run(db, rec, T0 + timedelta(minutes=i, seconds=5))
    assert rec.titles == ["The bot can't do its checks"]
    assert "No internet connection" in rec.got[0][1]
    assert run(db, rec, T0 + timedelta(minutes=4)) == ["The bot's checks are working again"]


def test_phase_failed_clean_tick_resets(db):
    rec = Rec()
    for i, bad in enumerate([True, True, False, True]):
        if bad:
            fail(db, "phase_failed", "ingest: x", T0 + timedelta(minutes=i))
        run(db, rec, T0 + timedelta(minutes=i, seconds=5))
    assert rec.titles == []


def test_flap_guard(db):
    rec = Rec()
    db.kv_set(IG_BLOCKED_KEY, T0.isoformat())
    run(db, rec, T0)
    db.kv_set(IG_BLOCKED_KEY, "")
    run(db, rec, T0 + timedelta(minutes=5))
    db.kv_set(IG_BLOCKED_KEY, (T0 + timedelta(minutes=10)).isoformat())
    run(db, rec, T0 + timedelta(minutes=10))
    db.kv_set(IG_BLOCKED_KEY, "")
    run(db, rec, T0 + timedelta(minutes=12))
    assert rec.titles == ["Instagram blocked the bot", "Instagram unblocked the bot"]


def test_state_survives_new_instance(tmp_path):
    path = tmp_path / "d.sqlite"
    rec = Rec()
    db = Database(path)
    fail(db, "email_failed", "gmail login failed: x", T0)
    run(db, rec, T0 + timedelta(seconds=5))
    db2 = Database(path)
    fail(db2, "email_failed", "gmail login failed: x", T0 + timedelta(minutes=1))
    run(db2, rec, T0 + timedelta(minutes=1, seconds=5))
    assert rec.titles == ["Gmail isn't sending"]


def test_old_events_before_first_check_are_ignored(db):
    rec = Rec()
    fail(db, "email_failed", "gmail login failed: x", T0 - timedelta(hours=3))
    assert run(db, rec, T0) == []


def test_one_channel_failing_still_sends_other():
    rec = Rec()
    FanOut(Boom(), rec).notify("t", "b")
    assert rec.got == [("t", "b")]


def test_ntfy_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    NtfyNotifier("igdm-abc", client=client).notify("Gmail isn't sending", "body text")
    (r,) = seen
    assert str(r.url) == "https://ntfy.sh/igdm-abc" and r.method == "POST"
    assert r.headers["Priority"] == "2" and r.headers["Title"] == "Gmail isn't sending"
    assert r.content == b"body text"


def test_email_notifier_sends_title_as_subject():
    class M:
        def __init__(self):
            self.sent = []

        def send(self, to, subject, body):
            self.sent.append((to, subject, body))

    m = M()
    EmailNotifier(m, "me@x.com").notify("DMs are failing", "b")
    assert m.sent == [("me@x.com", "DMs are failing", "b")]


def test_copy_follows_the_voice_rules():
    texts = [*alerts.TITLES.values(), *alerts.FIXED_TITLES.values(), alerts.FIXED_BODY,
             alerts.MORE, alerts.META_HELP]
    for t in texts:
        assert "—" not in t and "–" not in t
    for t in [*alerts.TITLES.values(), *alerts.FIXED_TITLES.values()]:
        assert ": " not in t
    assert set(alerts.TITLES) == set(alerts.FIXED_TITLES)


def test_check_alerts_never_raises(db):
    db.kv_set("alerts_state", "{not json")
    db.kv_set(IG_BLOCKED_KEY, T0.isoformat())
    assert run(db, Boom(), T0) == ["Instagram blocked the bot"]
    db.kv_set(IG_BLOCKED_KEY, "")
    run(db, Boom(), T0 + timedelta(minutes=1))  # no exception either way


def test_flap_guard_still_alerts_if_it_stays_broken(db):
    rec = Rec()
    db.kv_set(IG_BLOCKED_KEY, T0.isoformat())
    run(db, rec, T0)
    db.kv_set(IG_BLOCKED_KEY, "")
    run(db, rec, T0 + timedelta(minutes=5))
    db.kv_set(IG_BLOCKED_KEY, (T0 + timedelta(minutes=10)).isoformat())
    run(db, rec, T0 + timedelta(minutes=10))
    assert run(db, rec, T0 + timedelta(minutes=61)) == ["Instagram blocked the bot"]
    assert run(db, rec, T0 + timedelta(minutes=62)) == []


def test_meta_block_suppresses_phase_alert(db):
    rec = Rec()
    db.kv_set(IG_BLOCKED_KEY, T0.isoformat())
    for i in range(4):
        fail(db, "phase_failed", "dm: InstagramError: API access blocked", T0 + timedelta(minutes=i))
        run(db, rec, T0 + timedelta(minutes=i, seconds=5))
    assert rec.titles == ["Instagram blocked the bot"]


# ---- review fixes

def _tick_ctx(ig, t):
    import random

    from fakes import FakeClassifier, repo_config

    from igdm.engine import Context
    return Context(config=repo_config(), db=Database(":memory:"), ig=ig, jev=FakeClassifier(),
                   now=lambda: t[0], sleep=lambda s: None, rng=random.Random(1), own_id="me1",
                   own_username="alex", media_ids={"tokyo": "m1"},
                   comment_poll_interval=timedelta(0))


def test_instagram_unreachable_alerts_end_to_end():
    """Review #1: every Instagram call failing (network down) must alert, driven through tick()."""
    from fakes import FakeInstagram

    from igdm.engine import tick
    from igdm.instagram import InstagramError

    class Offline(FakeInstagram):
        def list_comments(self, media_id):
            raise InstagramError(0, None, "ConnectError: [Errno 101] Network is unreachable")

        def list_conversations(self, own_id, since):
            raise InstagramError(0, None, "ConnectError: [Errno 101] Network is unreachable")

    t = [T0]
    ctx, rec = _tick_ctx(Offline(), t), Rec()
    for i in range(4):
        t[0] = T0 + timedelta(minutes=i)
        tick(ctx)
        check_alerts(ctx.db, t[0], rec)
    assert rec.titles == ["The bot can't reach Instagram"]
    assert "No internet connection" in rec.got[0][1]
    ctx.ig = FakeInstagram()  # the network is back
    t[0] = T0 + timedelta(minutes=5)
    tick(ctx)
    assert check_alerts(ctx.db, t[0], rec) == ["The bot can reach Instagram again"]


def test_one_broken_reel_does_not_alert_as_unreachable():
    """A single reel failing every tick while everything else works isn't an outage."""
    from fakes import FakeInstagram

    from igdm.engine import tick
    from igdm.instagram import InstagramError

    class OneBadReel(FakeInstagram):
        def list_comments(self, media_id):
            raise InstagramError(400, 100, "Unsupported get request")

    t = [T0]
    ctx, rec = _tick_ctx(OneBadReel(), t), Rec()
    for i in range(5):
        t[0] = T0 + timedelta(minutes=i)
        tick(ctx)
        check_alerts(ctx.db, t[0], rec)
    assert rec.titles == []


def test_failed_startup_check_never_sends_a_false_fixed(db):
    """Review #3: a restart that fails on the same dead token logs nothing new; that's not 'fixed'."""
    rec = Rec()
    fail(db, "phase_failed", "ingest: instagram error status=400 code=190: dead", T0)
    assert run(db, rec, T0 + timedelta(seconds=5)) == ["Instagram blocked the bot"]
    assert check_alerts(db, T0 + timedelta(minutes=5), rec, startup=True) == []
    assert alerts.json.loads(db.kv_get("alerts_state"))["meta_blocked"]["open"] is True


def test_first_dm_refusals_alert(db):
    """Review #7."""
    rec = Rec()
    for i in range(3):
        fail(db, "dm1_failed", "instagram error status=400 code=10: no permission",
             T0 + timedelta(minutes=i))
    assert run(db, rec, T0 + timedelta(minutes=3)) == ["DMs are failing"]


def test_one_gmail_connection_blip_does_not_alert(db):
    rec = Rec()
    fail(db, "email_failed", "could not connect to smtp.gmail.com: TimeoutError", T0)
    assert run(db, rec, T0 + timedelta(seconds=5)) == []


def test_openai_classifier_rows_clear_the_checker_alert(db):
    rec = Rec()
    for i in range(3):
        fail(db, "jev_error", "JevError: openai returned HTTP 500", T0 + timedelta(minutes=i))
    assert run(db, rec, T0 + timedelta(minutes=3)) == [
        "The request checker (Jev or OpenAI) can't be reached"]
    db.log_usage("openai", "gpt-6-luna", "comment", at=T0 + timedelta(minutes=4))
    assert run(db, rec, T0 + timedelta(minutes=5)) == ["The request checker is back"]


@pytest.mark.parametrize("purpose", ["comment", "intent", "route", "email"])
def test_openai_classifier_rows_do_not_clear_the_llm_alert(db, purpose):
    rec = Rec()
    for i in range(3):
        fail(db, "llm_error", "APIError: out of credit", T0 + timedelta(minutes=i))
    assert run(db, rec, T0 + timedelta(minutes=3)) == ["AI replies are failing"]
    db.log_usage("openai", "gpt-6-luna", purpose, at=T0 + timedelta(minutes=4))
    assert run(db, rec, T0 + timedelta(minutes=5)) == []
    db.log_usage("openai", "gpt-6-luna", "reply", at=T0 + timedelta(minutes=6))
    assert run(db, rec, T0 + timedelta(minutes=7)) == ["AI replies are working again"]
