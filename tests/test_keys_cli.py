import getpass
import os
import re
import smtplib
import stat
from pathlib import Path

import pytest

from igdm import mailer
from igdm.cli import KNOWN_KEYS, main

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # main() loads .env into os.environ: clear before and after so tests don't leak into each other
    for name, _ in KNOWN_KEYS:
        monkeypatch.delenv(name, raising=False)
    yield
    for name, _ in KNOWN_KEYS:
        os.environ.pop(name, None)


def run(tmp_path, *args):
    return main(["--config", str(tmp_path / "config.toml"), *args])


def test_keys_never_prints_values(tmp_path, capsys):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret-123\n")
    assert run(tmp_path, "keys") == 0
    out = capsys.readouterr()
    assert "OPENAI_API_KEY: set" in out.out
    assert "META_APP_ID: missing" in out.out
    assert "sk-secret-123" not in out.out + out.err


def test_keys_works_without_config(tmp_path, capsys):
    assert not (tmp_path / "config.toml").exists()
    assert run(tmp_path, "keys") == 0
    assert "GMAIL_APP_PASSWORD: missing" in capsys.readouterr().out


def test_set_key_hidden_prompt(tmp_path, monkeypatch, capsys):
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return '  "abc123xyz"  '

    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 0
    out = capsys.readouterr()
    assert (tmp_path / ".env").read_text().splitlines() == ["OPENAI_API_KEY=abc123xyz"]
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert out.out == "Saved OPENAI_API_KEY.\n"
    assert "abc123xyz" not in out.out + out.err
    assert prompts == ["Paste OPENAI_API_KEY and press Enter (it stays hidden): "]


def test_set_key_keeps_other_lines(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("# mine\nMETA_APP_ID=111\nGEMINI_API_KEY=g-1\n")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "new-key")
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 0
    assert (tmp_path / ".env").read_text().splitlines() == [
        "# mine", "META_APP_ID=111", "GEMINI_API_KEY=g-1", "OPENAI_API_KEY=new-key"]


def test_set_key_refuses_unknown_and_empty(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("META_APP_ID=111\n")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "value")
    assert run(tmp_path, "set-key", "NOPE") == 1
    out = capsys.readouterr()
    assert "NOPE" in out.out + out.err
    assert "OPENAI_API_KEY" in out.out + out.err
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "   ")
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 1
    out = capsys.readouterr()
    assert "Nothing was pasted, so nothing changed." in out.out + out.err
    assert (tmp_path / ".env").read_text() == "META_APP_ID=111\n"


def test_set_key_refuses_newline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "ab\ncd")
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 1
    out = capsys.readouterr()
    assert "ab" not in (out.out + out.err).replace("Paste", "")
    assert not (tmp_path / ".env").exists()


def test_set_key_stdin(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("igdm-alerts-x7k2\n"))
    assert run(tmp_path, "set-key", "NTFY_TOPIC", "--stdin") == 0
    assert (tmp_path / ".env").read_text() == "NTFY_TOPIC=igdm-alerts-x7k2\n"
    assert capsys.readouterr().out == "Saved NTFY_TOPIC.\n"


def test_known_keys_match_env_example():
    text = (ROOT / ".env.example").read_text()
    in_example = re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.M)
    assert in_example == [n for n, _ in KNOWN_KEYS]
    assert all(purpose for _, purpose in KNOWN_KEYS)


class FakeSMTP:
    logins: list = []
    sent: list = []
    fail_login: Exception | None = None

    def __init__(self, host, port, timeout=None):
        FakeSMTP.hosts.append((host, port))

    hosts: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        FakeSMTP.quit_called = True

    def login(self, user, pw):
        FakeSMTP.logins.append((user, pw))
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login

    def sendmail(self, *a, **k):
        FakeSMTP.sent.append(a)

    send_message = sendmail


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.logins, FakeSMTP.sent, FakeSMTP.hosts = [], [], []
    FakeSMTP.fail_login = None
    FakeSMTP.quit_called = False
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_test_gmail_ok_and_refused(tmp_path, monkeypatch, capsys, smtp):
    (tmp_path / "config.toml").write_text('[email]\nfrom_address = "me@example.com"\n')
    (tmp_path / ".env").write_text("GMAIL_APP_PASSWORD=pw-secret-9\n")
    assert run(tmp_path, "test-gmail") == 0
    out = capsys.readouterr()
    assert "Gmail sign-in works for me@example.com." in out.out
    assert smtp.logins == [("me@example.com", "pw-secret-9")]
    assert smtp.sent == [] and smtp.quit_called
    assert smtp.hosts == [("smtp.gmail.com", 465)]

    smtp.fail_login = smtplib.SMTPAuthenticationError(535, b"bad pw-secret-9")
    assert run(tmp_path, "test-gmail") == 1
    out = capsys.readouterr()
    assert "Gmail refused the sign-in for me@example.com. Check the app password " \
           "and that 2-Step Verification is on." in out.out + out.err
    assert "pw-secret-9" not in out.out + out.err
    assert smtp.sent == []


def test_test_gmail_address_flag_without_config(tmp_path, capsys, smtp):
    (tmp_path / ".env").write_text("GMAIL_APP_PASSWORD=pw\n")
    assert run(tmp_path, "test-gmail", "--address", "a@b.com") == 0
    assert "Gmail sign-in works for a@b.com." in capsys.readouterr().out
    assert smtp.hosts == [("smtp.gmail.com", 465)]


def test_test_gmail_missing_password_or_address(tmp_path, capsys, smtp):
    assert run(tmp_path, "test-gmail", "--address", "a@b.com") == 1
    said = (o := capsys.readouterr()).out + o.err
    assert "GMAIL_APP_PASSWORD is not set. Run `bash scripts/ask_key.sh GMAIL_APP_PASSWORD` first." in said
    assert "igdm set-key" not in said  # set-key alone is never how a creator puts a secret in
    (tmp_path / ".env").write_text("GMAIL_APP_PASSWORD=pw\n")
    assert run(tmp_path, "test-gmail") == 1
    assert "--address" in (o := capsys.readouterr()).out + o.err
    assert smtp.logins == []


def test_test_gmail_connect_failure_is_not_blamed_on_password(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", boom)
    (tmp_path / ".env").write_text("GMAIL_APP_PASSWORD=pw\n")
    assert run(tmp_path, "test-gmail", "--address", "a@b.com") == 1
    o = capsys.readouterr()
    assert "Could not reach Gmail" in o.out + o.err
    assert "refused the sign-in" not in o.out + o.err


def test_set_key_never_echoes_a_pasted_secret_as_name(tmp_path, capsys):
    secret = "sk-proj-abcdef123456"
    assert run(tmp_path, "set-key", secret) == 1
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    assert "isn't a key name" in out.out + out.err
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("bad", ["abc #def", "abc${HOME}", "\"abc", "abc'", "'\"abc\"'"])
def test_set_key_refuses_unsafe_characters(tmp_path, monkeypatch, capsys, bad):
    (tmp_path / ".env").write_text("META_APP_ID=111\n")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": bad)
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 1
    out = capsys.readouterr()
    assert "characters .env can't store safely" in out.out + out.err
    assert (tmp_path / ".env").read_text() == "META_APP_ID=111\n"


def test_test_gmail_other_login_errors_are_not_blamed_on_password(tmp_path, capsys, smtp):
    (tmp_path / ".env").write_text("GMAIL_APP_PASSWORD=pw\n")
    smtp.fail_login = smtplib.SMTPServerDisconnected("gone")
    assert run(tmp_path, "test-gmail", "--address", "a@b.com") == 1
    o = capsys.readouterr()
    assert "Could not reach Gmail" in o.out + o.err
    assert "refused the sign-in" not in o.out + o.err


def test_set_key_strips_spaces_from_gmail_app_password(tmp_path, monkeypatch, capsys):
    # Google shows app passwords in groups of four; they never contain spaces
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": " abcd efgh\tijkl mnop ")
    assert run(tmp_path, "set-key", "GMAIL_APP_PASSWORD") == 0
    assert (tmp_path / ".env").read_text().splitlines() == ["GMAIL_APP_PASSWORD=abcdefghijklmnop"]
    out = capsys.readouterr()
    assert "abcd" not in out.out + out.err


def test_set_key_keeps_inner_spaces_for_other_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "a b")
    assert run(tmp_path, "set-key", "OPENAI_API_KEY") == 0
    assert "OPENAI_API_KEY=a b" in (tmp_path / ".env").read_text()
