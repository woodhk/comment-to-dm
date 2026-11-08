import smtplib

import pytest

from igdm import mailer
from igdm.mailer import GmailSender, MailMaybeSent, MailRefused


class StubSMTP:
    fail_login: Exception | None = None
    fail_send: Exception | None = None
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        if StubSMTP.fail_login:
            raise StubSMTP.fail_login

    def send_message(self, msg):
        if StubSMTP.fail_send:
            raise StubSMTP.fail_send
        StubSMTP.sent.append(msg)


@pytest.fixture
def stub(monkeypatch):
    StubSMTP.fail_login = StubSMTP.fail_send = None
    StubSMTP.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", StubSMTP)
    return StubSMTP


def sender():
    return GmailSender("owner@example.com", "pw", "Sam Lee", "smtp.gmail.com", 465)


def test_sends_message_with_headers(stub):
    sender().send("s@x.com", "subj", "body")
    (msg,) = stub.sent
    assert msg["To"] == "s@x.com" and msg["Subject"] == "subj"
    assert "owner@example.com" in msg["From"] and "Sam Lee" in msg["From"]
    assert msg.get_content().strip() == "body"


def test_login_failure_is_refused(stub):
    stub.fail_login = smtplib.SMTPAuthenticationError(535, b"bad")
    with pytest.raises(MailRefused):
        sender().send("s@x.com", "s", "b")


def test_only_auth_errors_are_login_refused(stub):
    stub.fail_login = smtplib.SMTPAuthenticationError(535, b"bad")
    with pytest.raises(mailer.MailLoginRefused):
        sender().check_login()
    stub.fail_login = smtplib.SMTPServerDisconnected("gone")
    with pytest.raises(MailRefused) as e:
        sender().send("s@x.com", "s", "b")
    assert not isinstance(e.value, mailer.MailLoginRefused)
    assert stub.sent == []


def test_recipient_refused_is_refused(stub):
    stub.fail_send = smtplib.SMTPRecipientsRefused({"s@x.com": (550, b"no")})
    with pytest.raises(MailRefused):
        sender().send("s@x.com", "s", "b")


def test_disconnect_during_send_is_maybe_sent(stub):
    stub.fail_send = smtplib.SMTPServerDisconnected("gone")
    with pytest.raises(MailMaybeSent):
        sender().send("s@x.com", "s", "b")


def test_timeout_during_send_is_maybe_sent(stub):
    stub.fail_send = TimeoutError("slow")
    with pytest.raises(MailMaybeSent):
        sender().send("s@x.com", "s", "b")


def test_connect_failure_is_refused(monkeypatch):
    def boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", boom)
    with pytest.raises(MailRefused):
        sender().send("s@x.com", "s", "b")


class StartTlsSMTP(StubSMTP):
    calls: list = []
    fail_starttls: Exception | None = None

    def __init__(self, host, port, timeout=None):
        super().__init__(host, port, timeout)
        StartTlsSMTP.calls.append(("connect", host, port))

    def starttls(self, context=None):
        StartTlsSMTP.calls.append(("starttls",))
        if StartTlsSMTP.fail_starttls:
            raise StartTlsSMTP.fail_starttls

    def close(self):
        StartTlsSMTP.calls.append(("close",))

    def login(self, user, pw):
        StartTlsSMTP.calls.append(("login",))
        super().login(user, pw)


@pytest.fixture
def both(monkeypatch):
    StubSMTP.fail_login = StubSMTP.fail_send = None
    StubSMTP.sent = []
    StartTlsSMTP.calls = []
    StartTlsSMTP.fail_starttls = None
    ssl_used = []

    class SSL(StubSMTP):
        def __init__(self, host, port, timeout=None):
            ssl_used.append((host, port))
            super().__init__(host, port, timeout)

        def starttls(self, context=None):
            raise AssertionError("SMTP_SSL must not STARTTLS")

    monkeypatch.setattr(mailer.smtplib, "SMTP", StartTlsSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", SSL)
    return ssl_used


def test_port_587_uses_starttls(both):
    GmailSender("owner@example.com", "pw", "Sam Lee", "smtp.gmail.com", 587).send("s@x.com", "s", "b")
    assert StartTlsSMTP.calls[0] == ("connect", "smtp.gmail.com", 587)
    assert StartTlsSMTP.calls.index(("starttls",)) < StartTlsSMTP.calls.index(("login",))
    assert both == [] and len(StubSMTP.sent) == 1


def test_port_465_still_ssl(both):
    sender().send("s@x.com", "s", "b")
    assert both == [("smtp.gmail.com", 465)] and StartTlsSMTP.calls == []


def test_starttls_failure_is_refused(both):
    StartTlsSMTP.fail_starttls = smtplib.SMTPException("no tls")
    with pytest.raises(MailRefused) as e:
        GmailSender("owner@example.com", "pw", "Sam Lee", "smtp.gmail.com", 587).send("s@x.com", "s", "b")
    assert str(e.value).startswith("could not connect to smtp.gmail.com")
    assert StubSMTP.sent == []
