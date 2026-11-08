import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeClassifier, FakeInstagram, FakeMailer, repo_config

from igdm import outbox
from igdm.db import Database
from igdm.engine import Context
from igdm.mailer import MailMaybeSent, MailRefused

SGT = ZoneInfo("Asia/Singapore")
T = datetime(2026, 11, 3, 9, 0, tzinfo=SGT).astimezone(timezone.utc)


class Env:
    def __init__(self):
        self.cfg = repo_config()
        self.reel = self.cfg.reels[0]
        self.db = Database(":memory:")
        self.mail = FakeMailer()
        self.t = T
        self.ctx = Context(
            config=self.cfg, db=self.db, ig=FakeInstagram(), jev=FakeClassifier(),
            now=lambda: self.t, sleep=lambda s: None, rng=random.Random(1), own_id="me1",
            own_username="alex", media_ids={}, mailer=self.mail,
        )

    def add(self, cid="c1", email="s@x.com", name="Sam", after=timedelta(minutes=2), **kw):
        return self.db.add_email(
            comment_id=cid, handle="sarah", igsid="u1", email=email, provided_at=self.t,
            reel_url=self.reel.url, keyword=self.reel.keyword, name=name,
            send_after=self.t + after if after is not None else None, **kw)

    def row(self, i):
        return self.db.email_by_id(i)


@pytest.fixture
def env():
    return Env()


def test_sends_due_email_with_reel_template(env):
    i = env.add()
    env.t += timedelta(minutes=2)
    assert outbox.send_due_emails(env.ctx) == 1
    (to, subject, body) = env.mail.sent[0]
    assert to == "s@x.com"
    assert subject == env.reel.email_subject.format(name="Sam", keyword="Tokyo", material_url="")
    assert body.startswith("Hi Sam,") and env.reel.material_url in body
    assert env.row(i).send_state == "sent" and env.row(i).emailed_at == env.t


def test_handle_used_when_no_name(env):
    env.add(name=None)
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    assert env.mail.sent[0][2].startswith("Hi sarah,")


def test_not_due_not_sent(env):
    env.add()
    env.t += timedelta(minutes=1)
    assert outbox.send_due_emails(env.ctx) == 0 and env.mail.sent == []


def test_manual_rows_never_auto_sent(env):
    env.add(after=None)
    env.t += timedelta(days=1)
    assert outbox.send_due_emails(env.ctx) == 0 and env.mail.sent == []


def test_sent_once_only(env):
    env.add()
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    env.t += timedelta(minutes=5)
    outbox.send_due_emails(env.ctx)
    assert len(env.mail.sent) == 1


def test_refused_retries_once_then_failed(env):
    i = env.add()
    env.mail.fail["s@x.com"] = MailRefused("550 no such user")
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    assert env.row(i).send_state == "queued" and env.row(i).attempts == 1
    env.t += timedelta(minutes=1)
    outbox.send_due_emails(env.ctx)
    r = env.row(i)
    assert r.send_state == "failed" and r.attempts == 2 and "550" in r.last_error
    del env.mail.fail["s@x.com"]
    env.t += timedelta(minutes=1)
    assert outbox.send_due_emails(env.ctx) == 0 and env.mail.sent == []


def test_maybe_sent_is_unknown_and_never_retried(env):
    i = env.add()
    env.mail.fail["s@x.com"] = MailMaybeSent("disconnected mid-send")
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    assert env.row(i).send_state == "unknown"
    del env.mail.fail["s@x.com"]
    env.t += timedelta(minutes=5)
    outbox.send_due_emails(env.ctx)
    assert env.mail.sent == []


def test_unexpected_exception_is_unknown(env):
    i = env.add()
    env.mail.fail["s@x.com"] = RuntimeError("weird")
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    assert env.row(i).send_state == "unknown"


def test_crash_mid_send_becomes_unknown(env):
    i = env.add()
    env.db.mark_email(i, "sending")
    env.t += timedelta(minutes=2)
    outbox.recover(env.ctx)
    assert env.row(i).send_state == "unknown"
    assert outbox.send_due_emails(env.ctx) == 0 and env.mail.sent == []


def test_no_mailer_does_nothing(env):
    env.add()
    env.ctx.mailer = None
    env.t += timedelta(minutes=2)
    assert outbox.send_due_emails(env.ctx) == 0


def test_sends_in_quiet_hours(env):
    env.t = datetime(2026, 11, 4, 3, 0, tzinfo=SGT).astimezone(timezone.utc)
    env.add()
    env.t += timedelta(minutes=2)
    assert outbox.send_due_emails(env.ctx) == 1


def test_unconfigured_reel_falls_back_to_messages(env):
    i = env.db.add_email(comment_id="c2", handle="bob", igsid="u2", email="b@x.com",
                         provided_at=env.t, reel_url="https://gone", keyword="Gone",
                         send_after=env.t)
    outbox.send_due_emails(env.ctx)
    (to, subject, body) = env.mail.sent[0]
    assert subject == env.cfg.messages.email_subject.format(name="bob", keyword="Gone",
                                                            material_url="")
    assert env.row(i).send_state == "sent"


def test_failed_and_unknown_emails_are_logged_for_the_feed(env):
    env.add(cid="c1", email="a@x.com")
    env.add(cid="c2", email="b@x.com")
    env.mail.fail["a@x.com"] = MailRefused("550 no such user")
    env.mail.fail["b@x.com"] = MailMaybeSent("timed out")
    env.t += timedelta(minutes=2)
    outbox.send_due_emails(env.ctx)
    assert [(e, d) for _, e, d in env.db.events("c1")] == [("email_failed", "550 no such user")]
    assert [e for _, e, _ in env.db.events("c2")] == ["email_unknown"]
