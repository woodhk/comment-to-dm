import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

from igdm.config import ReelConfig, load_config

from igdm.cli import main
from igdm.db import Database, Request, Status

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
URL = "https://www.instagram.com/reel/TESTREEL01/"


@pytest.fixture
def cfg(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text().replace(
        'db_path = "data/igdm.sqlite"', f'db_path = "{tmp_path}/test.sqlite"')
    p = tmp_path / "config.toml"
    p.write_text(text)
    db = Database(tmp_path / "test.sqlite")
    for i, h in enumerate(["sarah", "bob"]):
        db.insert_request(Request(
            comment_id=f"c{i}", reel_key="tokyo", media_id="m1", username=h,
            commenter_id=f"u{i}", text="Tokyo", commented_at=T0, status=Status.EMAIL_CAPTURED))
        db.add_email(comment_id=f"c{i}", handle=h, igsid=f"u{i}", email=f"{h}@x.com",
                     provided_at=T0, reel_url=URL, keyword="Tokyo")
    return p, db


def test_cli_pending_renders_template(cfg, capsys):
    p, _ = cfg
    assert main(["--config", str(p), "pending"]) == 0
    out = capsys.readouterr().out
    assert "sarah@x.com" in out
    assert "Subject: your Tokyo guide" in out
    assert load_config(p).reels[0].material_url in out
    assert "Hi sarah," in out


def test_cli_mark_sent_all(cfg, capsys):
    p, db = cfg
    assert main(["--config", str(p), "mark-sent", "--all"]) == 0
    assert db.emails(pending_only=True) == []
    capsys.readouterr()
    main(["--config", str(p), "pending"])
    assert "sarah@x.com" not in capsys.readouterr().out


def test_cli_mark_sent_ids(cfg):
    p, db = cfg
    first = db.emails()[0].id
    assert main(["--config", str(p), "mark-sent", str(first)]) == 0
    assert [e.instagram_handle for e in db.emails(pending_only=True)] == ["bob"]


def test_cli_mark_sent_requires_target(cfg):
    p, _ = cfg
    with pytest.raises(SystemExit):
        main(["--config", str(p), "mark-sent"])


def test_cli_emails_csv_columns(cfg, tmp_path):
    p, _ = cfg
    out = tmp_path / "e.csv"
    assert main(["--config", str(p), "emails", "--csv", str(out)]) == 0
    rows = list(csv.reader(io.StringIO(out.read_text())))
    assert rows[0] == ["instagram_handle", "email", "date", "video", "ig_user_id", "emailed_at"]
    assert rows[1][0] == "sarah" and rows[1][1] == "sarah@x.com"
    assert rows[1][3] == f"{URL} (Tokyo)"
    assert rows[1][4] == "u0"


def test_cli_status(cfg, capsys):
    p, db = cfg
    db.insert_request(Request(
        comment_id="c9", reel_key="tokyo", media_id="m1", username="zed", commenter_id="u9",
        text="Tokyo pls", commented_at=T0, status=Status.NEEDS_OWNER, note="refused"))
    assert main(["--config", str(p), "status"]) == 0
    out = capsys.readouterr().out
    assert "email_captured" in out and "\nfor you:" in out
    assert f'[needs_owner] @zed: "Tokyo pls" ({URL}) refused' in out


def _use_api(p, api):
    p.write_text(p.read_text().replace('api = "facebook_login"', f'api = "{api}"'))


def test_missing_key_exits_nonzero(cfg, capsys, monkeypatch):
    p, _ = cfg
    _use_api(p, "instagram_login")  # default api is now facebook_login
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["--config", str(p), "once"]) != 0
    assert "IG_ACCESS_TOKEN" in capsys.readouterr().err


def test_config_accepted_after_subcommand(cfg, capsys):
    p, _ = cfg
    assert main(["status", "--config", str(p)]) == 0
    assert "email_captured" in capsys.readouterr().out


# ---- fix round 1: refresh wiring, redaction, once exit code
import json
import logging
from datetime import timedelta

import httpx

from igdm import cli
from igdm.instagram import GraphInstagram, InstagramError
from igdm.token_store import save_token

NOW = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)


def test_refresh_updates_live_client_and_file(tmp_path):
    seen = []

    def h(req):
        seen.append(req)
        if "refresh_access_token" in req.url.path:
            return httpx.Response(200, json={"access_token": "NEWTOK"})
        return httpx.Response(200, json={"user_id": "1", "username": "alex"})

    g = GraphInstagram("OLD", transport=httpx.MockTransport(h))
    p = tmp_path / "token.json"
    assert cli.refresh_if_due(g, p, "OLD", NOW) is True
    g.me()
    assert seen[-1].headers["Authorization"] == "Bearer NEWTOK"
    assert json.loads(p.read_text())["token"] == "NEWTOK"
    assert cli.refresh_if_due(g, p, "OLD", NOW + timedelta(days=1)) is False


def test_refresh_failure_backs_off(tmp_path):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "token too young", "code": 100}})

    g = GraphInstagram("OLD", transport=httpx.MockTransport(h))
    p = tmp_path / "token.json"
    assert cli.refresh_if_due(g, p, "OLD", NOW) is False
    assert cli.refresh_if_due(g, p, "OLD", NOW + timedelta(hours=1)) is False
    assert len(calls) == 1
    cli.refresh_if_due(g, p, "OLD", NOW + timedelta(hours=7))
    assert len(calls) == 2


def test_log_redaction_includes_exception_text():
    r = cli.Redactor()
    r.add("SECRET-TOK", "jevkey123")
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("%(message)s"))
    h.addFilter(r.filter)
    lg = logging.getLogger("redact-test")
    lg.propagate = False
    lg.addHandler(h)
    try:
        raise RuntimeError("bad url ?access_token=SECRET-TOK")
    except RuntimeError:
        lg.exception("failed with %s", "jevkey123")
    out = stream.getvalue()
    assert "SECRET-TOK" not in out and "jevkey123" not in out and "***" in out


def test_once_output_and_exit_code_redacted(cfg, capsys, monkeypatch):
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOK")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")
    monkeypatch.setattr(cli, "_build", lambda *a, **k: (object(), None, None))
    monkeypatch.setattr(cli, "tick", lambda ctx: {"ingest": "error: RuntimeError: SECRET-TOK", "dm": 0})
    assert main(["--config", str(p), "once"]) == 1
    out = capsys.readouterr().out
    assert "SECRET-TOK" not in out and "error: RuntimeError: ***" in out
    monkeypatch.setattr(cli, "tick", lambda ctx: {"ingest": 0, "dm": 1})
    assert main(["--config", str(p), "once"]) == 0


def test_main_error_output_redacted(cfg, capsys, monkeypatch):
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOK")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")

    def boom(*a, **k):
        raise ValueError("oops SECRET-TOK")

    monkeypatch.setattr(cli, "_build", boom)
    assert main(["--config", str(p), "once"]) == 1
    err = capsys.readouterr().err
    assert "SECRET-TOK" not in err and "oops ***" in err


# ---- fix round 2: redaction on every secret-holding command
def test_once_warning_log_is_redacted_on_real_handlers(cfg, capsys, monkeypatch):
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOK")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")

    def fake_tick(ctx):
        logging.getLogger("igdm.engine").warning("api said bad token SECRET-TOK / jevkey123")
        try:
            raise RuntimeError("inner SECRET-TOK")
        except RuntimeError:
            logging.getLogger("igdm.jev").exception("phase blew up")
        return {"ingest": 0}

    monkeypatch.setattr(cli, "_build", lambda *a, **k: (object(), None, None))
    monkeypatch.setattr(cli, "tick", fake_tick)
    assert main(["--config", str(p), "once"]) == 0
    cap = capsys.readouterr()
    assert "SECRET-TOK" not in cap.err and "jevkey123" not in cap.err
    assert "api said bad token ***" in cap.err
    assert "inner ***" in cap.err
    logfile = (p.parent / "logs" / "igdm.log").read_text()
    assert "SECRET-TOK" not in logfile and "jevkey123" not in logfile
    for h in logging.getLogger().handlers:
        h.flush()


def test_uncaught_exception_is_redacted(cfg, capsys, monkeypatch):
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "SECRET-TOK")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")

    def boom(*a, **k):
        raise KeyError("SECRET-TOK")

    monkeypatch.setattr(cli, "_build", boom)
    assert main(["--config", str(p), "once"]) == 1
    err = capsys.readouterr().err
    assert "SECRET-TOK" not in err and "error: KeyError" in err


# ---- final review: polling, lock, startup retry, status
def test_once_always_lists_comments(cfg, monkeypatch):
    """A manual `once` polls comments even if the agent polled them a minute ago."""
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    seen = {}

    def fake_build(cfg, root, dry_run, **kw):
        seen.update(kw)
        return object(), None, None

    monkeypatch.setattr(cli, "_build", fake_build)
    monkeypatch.setattr(cli, "tick", lambda ctx: {"ingest": 0})
    assert main(["--config", str(p), "once"]) == 0
    assert seen.get("comment_poll_interval") == timedelta(0)


def _hold_lock(root):
    import fcntl
    (root / "data").mkdir(exist_ok=True)
    f = open(root / "data" / "igdm.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def _no_build(*a, **k):
    raise AssertionError("must not build while another igdm holds the lock")


@pytest.mark.parametrize("cmd", ["once", "run"])
def test_live_command_exits_when_already_running(cfg, capsys, monkeypatch, cmd):
    p, _ = cfg
    monkeypatch.setattr(cli, "_build", _no_build)
    monkeypatch.setattr(cli, "build_with_retry", _no_build, raising=False)
    held = _hold_lock(p.parent)
    try:
        assert main(["--config", str(p), cmd]) == 0
    finally:
        held.close()
    assert "already running" in capsys.readouterr().out


def test_once_takes_and_releases_the_lock(cfg, monkeypatch):
    import fcntl
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(cli, "_build", lambda *a, **k: (object(), None, None))
    held_during = []

    def fake_tick(ctx):
        f = open(p.parent / "data" / "igdm.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held_during.append(False)
        except BlockingIOError:
            held_during.append(True)
        finally:
            f.close()
        return {"ingest": 0}

    monkeypatch.setattr(cli, "tick", fake_tick)
    assert main(["--config", str(p), "once"]) == 0
    assert held_during == [True]
    _hold_lock(p.parent).close()  # released afterwards


def test_dry_run_ignores_the_lock(cfg, monkeypatch):
    p, _ = cfg
    monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(cli, "_build", lambda *a, **k: (object(), None, None))
    monkeypatch.setattr(cli, "tick", lambda ctx: {"ingest": 0})
    held = _hold_lock(p.parent)
    try:
        assert main(["--config", str(p), "once", "--dry-run"]) == 0
    finally:
        held.close()


class _Stop:
    def __init__(self, stop_after=None):
        self.waits = []
        self.stop_after = stop_after

    def is_set(self):
        return False

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stop_after is not None and len(self.waits) >= self.stop_after


def test_run_retries_startup_with_backoff(cfg, monkeypatch, caplog):
    p, _ = cfg
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) <= 7:
            raise InstagramError(0, None, "ConnectError: offline")
        return ("ctx", "path", "real")

    monkeypatch.setattr(cli, "_build", flaky)
    stop = _Stop()
    with caplog.at_level(logging.ERROR, logger="igdm"):
        assert cli.build_with_retry(None, p.parent, False, stop) == ("ctx", "path", "real")
    assert stop.waits == [300, 600, 1200, 2400, 3600, 3600, 3600]
    assert "STARTUP FAILED" in caplog.text and "offline" in caplog.text


def test_run_startup_retry_stops_on_signal(cfg, monkeypatch):
    p, _ = cfg

    def broken(*a, **k):
        raise CliErr("reel(s) did not resolve")

    from igdm.cli import CliError as CliErr
    monkeypatch.setattr(cli, "_build", broken)
    stop = _Stop(stop_after=2)
    assert cli.build_with_retry(None, p.parent, False, stop) is None
    assert stop.waits == [300, 600]


def test_status_lists_recent_handled_by_owner(cfg, capsys):
    p, db = cfg
    for cid, user in (("h1", "recent"), ("h2", "old")):
        db.insert_request(Request(
            comment_id=cid, reel_key="tokyo", media_id="m1", username=user, commenter_id=None,
            text="Tokyo", commented_at=T0, status=Status.HANDLED_BY_OWNER,
            note="you replied by hand: hi"))
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    db._c.execute("UPDATE requests SET updated_at=? WHERE comment_id='h2'", (old,))
    assert main(["--config", str(p), "status"]) == 0
    out = capsys.readouterr().out
    assert "bot stopped because you replied by hand" in out
    assert "check if that's right" in out
    assert '@recent: "Tokyo"' in out and "you replied by hand: hi" in out
    assert "@old" not in out


def test_run_stopped_during_startup_retry_exits_cleanly(cfg, monkeypatch):
    p, _ = cfg
    monkeypatch.setattr(cli, "build_with_retry", lambda *a, **k: None)
    assert main(["--config", str(p), "run"]) == 0


# ---- platform change: Instagram API with Facebook Login
import os
import stat
import signal as _signal

from igdm.instagram import FB_GRAPH

IG_ID, PAGE_ID = "1784", "555"
SECRETS = ("APPSECRET", "SHORTUSER", "LONGUSER", "PAGETOK", "PAGETOK2")


@pytest.fixture(autouse=True)
def _restore_environ():
    """main() loads <tmp>/.env into os.environ, and REDACTOR is process-wide: reset both,
    so one test's one-letter fake secrets don't redact another test's output."""
    saved = dict(os.environ)
    cli.REDACTOR._secrets.clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    cli.REDACTOR._secrets.clear()


def _router(routes, seen):
    """routes: {(METHOD, host-less path): json or callable(req)}; unknown routes fail."""
    def h(req):
        seen.append(req)
        key = (req.method, req.url.path)
        assert key in routes, f"unexpected request {key}"
        r = routes[key]
        return r(req) if callable(r) else httpx.Response(200, json=r)
    return h


def _transport(monkeypatch, routes):
    seen = []
    monkeypatch.setattr(cli, "_http_transport", lambda: httpx.MockTransport(_router(routes, seen)))
    return seen


class _FakeJev:
    model = "fake-model"

    def __init__(self, *a, **k):
        pass

    def comment_wants_material(self, text, reel):
        return 0.1 if "music" in text else 0.9


def _fb_env(monkeypatch):
    monkeypatch.setenv("IG_PAGE_TOKEN", "PAGETOK")
    monkeypatch.setenv("FB_PAGE_ID", PAGE_ID)
    monkeypatch.setenv("IG_ACCOUNT_ID", IG_ID)
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")


FB_ROUTES = {
    ("GET", f"/v26.0/{IG_ID}"): {"id": IG_ID, "username": "alex"},
    ("GET", f"/v26.0/{IG_ID}/media"): {"data": [
        {"id": "m1", "permalink": "https://www.instagram.com/reel/TESTREEL01/"}]},
    ("GET", "/v26.0/m1/comments"): {"data": []},
    ("GET", f"/v26.0/{PAGE_ID}/conversations"): {"data": []},
}


@pytest.mark.parametrize("cmd", ["check", "once"])
def test_fb_missing_env_says_run_connect(cfg, capsys, monkeypatch, cmd):
    p, _ = cfg
    for k in ("IG_PAGE_TOKEN", "FB_PAGE_ID", "IG_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("IG_PAGE_TOKEN", "PAGETOK")
    assert main(["--config", str(p), cmd]) == 2
    err = capsys.readouterr().err
    assert "FB_PAGE_ID" in err and "IG_ACCOUNT_ID" in err and "IG_PAGE_TOKEN" not in err
    assert "igdm connect" in err


def test_fb_check_uses_page_token_on_graph_facebook(cfg, capsys, monkeypatch):
    p, _ = cfg
    _fb_env(monkeypatch)
    monkeypatch.setattr(cli, "JevClassifier", _FakeJev)
    seen = _transport(monkeypatch, FB_ROUTES)
    assert main(["--config", str(p), "check"]) == 0
    out = capsys.readouterr().out
    assert f"account: @alex (id {IG_ID})" in out
    assert "reel tokyo: media id m1, 0 comments" in out
    assert "facebook_login" in out
    assert all(r.url.host == "graph.facebook.com" for r in seen)
    assert all(r.headers["Authorization"] == "Bearer PAGETOK" for r in seen)
    assert not any(r.url.path.endswith("/me") for r in seen)
    assert not (p.parent / "data" / "token.json").exists()


def test_instagram_login_check_still_works(cfg, capsys, monkeypatch):
    p, _ = cfg
    _use_api(p, "instagram_login")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "IGTOK")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")
    monkeypatch.setattr(cli, "JevClassifier", _FakeJev)
    seen = _transport(monkeypatch, {
        ("GET", "/v26.0/me"): {"user_id": "8", "username": "alex"},
        ("GET", "/v26.0/me/media"): FB_ROUTES[("GET", f"/v26.0/{IG_ID}/media")],
        ("GET", "/v26.0/m1/comments"): {"data": []},
        ("GET", "/v26.0/8/conversations"): {"data": []},
    })
    assert main(["--config", str(p), "check"]) == 0
    assert "account: @alex (id 8)" in capsys.readouterr().out
    assert all(r.url.host == "graph.instagram.com" for r in seen)
    assert all(r.headers["Authorization"] == "Bearer IGTOK" for r in seen)


def test_fb_build_context_and_no_token_file(cfg, monkeypatch):
    p, _ = cfg
    _fb_env(monkeypatch)
    _transport(monkeypatch, FB_ROUTES)
    from igdm.config import load_config
    conf = load_config(p)
    ctx, token_path, real = cli._build(conf, p.parent, False)
    assert (ctx.own_id, ctx.own_username) == (IG_ID, "alex")
    assert ctx.media_ids == {"tokyo": "m1"}
    assert token_path is None and real.facebook_login


@pytest.mark.parametrize("node,path", [("page", PAGE_ID), ("ig", IG_ID)])
def test_fb_client_uses_configured_messaging_node(cfg, monkeypatch, node, path):
    p, _ = cfg
    p.write_text(p.read_text().replace('messaging_node = "page"', f'messaging_node = "{node}"'))
    _fb_env(monkeypatch)
    seen = _transport(monkeypatch, {("POST", f"/v26.0/{path}/messages"): {"message_id": "x"}})
    from igdm.config import load_config
    real, _ = cli._instagram(load_config(p), p.parent)
    assert real.send_message(IG_ID, "u1", "hi") == "x"
    assert seen[0].url.path == f"/v26.0/{path}/messages"


def _run_one_tick(cfg_path, monkeypatch, api):
    """Run the daemon loop for exactly one tick; return how often refresh_if_due was called."""
    refreshed = []
    monkeypatch.setattr(cli, "build_with_retry", lambda *a, **k: (
        type("Ctx", (), {"now": staticmethod(lambda: NOW),
                         "config": __import__("igdm.config").config.load_config(cfg_path)})(),
        None if api == "facebook_login" else cfg_path.parent / "data" / "token.json",
        object()))
    monkeypatch.setattr(cli, "refresh_if_due", lambda *a, **k: refreshed.append(1))

    def one_tick(ctx):
        _signal.raise_signal(_signal.SIGTERM)  # stops the loop after this tick
        return {"ingest": 0}

    monkeypatch.setattr(cli, "tick", one_tick)
    old = (_signal.getsignal(_signal.SIGINT), _signal.getsignal(_signal.SIGTERM))
    try:
        assert main(["--config", str(cfg_path), "run"]) == 0
    finally:
        _signal.signal(_signal.SIGINT, old[0])
        _signal.signal(_signal.SIGTERM, old[1])
    return len(refreshed)


def test_fb_run_never_refreshes_token(cfg, monkeypatch):
    p, _ = cfg
    assert _run_one_tick(p, monkeypatch, "facebook_login") == 0


def test_instagram_login_run_still_refreshes_token(cfg, monkeypatch):
    p, _ = cfg
    _use_api(p, "instagram_login")
    monkeypatch.setenv("IG_ACCESS_TOKEN", "IGTOK")
    assert _run_one_tick(p, monkeypatch, "instagram_login") == 1


def test_fb_refresh_token_command_explains_not_needed(cfg, capsys, monkeypatch):
    p, _ = cfg
    _transport(monkeypatch, {})  # any request would fail the test
    assert main(["--config", str(p), "refresh-token"]) == 0
    out = capsys.readouterr().out
    assert "not needed" in out and "igdm connect" not in out
    assert not (p.parent / "data" / "token.json").exists()


# ---- igdm connect
ENV_BEFORE = (
    "# my keys\n"
    "TYPESAFE_API_KEY=jevkey123\n"
    "META_APP_ID=appid1\n"
    "META_APP_SECRET=APPSECRET\n"
    "FB_USER_TOKEN=SHORTUSER\n"
    "IG_PAGE_TOKEN=stale-old-token\n"
    "\n"
    "OTHER=keep me\n"
)


@pytest.fixture
def env_file(cfg, monkeypatch):
    p, _ = cfg
    f = p.parent / ".env"
    f.write_text(ENV_BEFORE)
    os.chmod(f, 0o600)
    for k in ("IG_PAGE_TOKEN", "FB_PAGE_ID", "IG_ACCOUNT_ID", "META_APP_ID", "META_APP_SECRET",
              "FB_USER_TOKEN", "OTHER"):
        monkeypatch.delenv(k, raising=False)
    return p, f


def _page(pid, name, token, ig=None):
    d = {"id": pid, "name": name, "access_token": token}
    if ig:
        d["instagram_business_account"] = {"id": ig}
    return d


def _connect_routes(pages, *, ig_lookup=None):
    def exchange(req):
        q = req.url.params
        assert q["grant_type"] == "fb_exchange_token"
        assert q["client_id"] == "appid1"
        assert q["client_secret"] == "APPSECRET"
        assert q["fb_exchange_token"] == "SHORTUSER"
        return httpx.Response(200, json={"access_token": "LONGUSER", "token_type": "bearer"})

    def accounts(req):
        assert req.headers["Authorization"] == "Bearer LONGUSER"
        assert req.url.params["fields"] == "id,name,access_token,instagram_business_account"
        return httpx.Response(200, json={"data": pages})

    def username(req):
        assert req.headers["Authorization"].startswith("Bearer PAGETOK")
        assert req.url.params["fields"] == "username"
        return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[1], "username": "alex"})

    routes = {
        ("GET", "/v26.0/oauth/access_token"): exchange,
        ("GET", "/v26.0/me/accounts"): accounts,
        ("GET", f"/v26.0/{IG_ID}"): username,
        ("GET", "/v26.0/1999"): username,
        ("GET", "/v26.0/444"): {"id": "444"},  # a Page with no Instagram linked
    }
    routes.update(ig_lookup or {})
    return routes


def _env_dict(f):
    return dict(l.split("=", 1) for l in f.read_text().splitlines() if "=" in l and not l.startswith("#"))


def _assert_no_secrets(text):
    for s in SECRETS:
        assert s not in text, f"{s} leaked"


def test_connect_single_page_writes_env(env_file, capsys, monkeypatch):
    p, f = env_file
    seen = _transport(monkeypatch, _connect_routes([
        _page("444", "No IG page", "PAGETOK2"),
        _page(PAGE_ID, "Sam Lee", "PAGETOK", ig=IG_ID),
    ]))
    assert main(["--config", str(p), "connect"]) == 0
    cap = capsys.readouterr()
    _assert_no_secrets(cap.out + cap.err)
    assert "Sam Lee" in cap.out and PAGE_ID in cap.out and IG_ID in cap.out and "@alex" in cap.out
    env = _env_dict(f)
    assert env["IG_PAGE_TOKEN"] == "PAGETOK"
    assert env["FB_PAGE_ID"] == PAGE_ID and env["IG_ACCOUNT_ID"] == IG_ID
    assert env["OTHER"] == "keep me" and env["TYPESAFE_API_KEY"] == "jevkey123"
    assert env["META_APP_SECRET"] == "APPSECRET" and env["FB_USER_TOKEN"] == "SHORTUSER"
    lines = f.read_text().splitlines()
    assert lines[0] == "# my keys" and "" in lines  # comments and blank lines kept
    assert lines.index("IG_PAGE_TOKEN=PAGETOK") == 5  # updated in place
    assert sum(l.startswith("IG_PAGE_TOKEN=") for l in lines) == 1
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert all(r.url.host == "graph.facebook.com" for r in seen)
    assert (p.parent / "logs" / "igdm.log").exists()
    _assert_no_secrets((p.parent / "logs" / "igdm.log").read_text())


def test_connect_several_pages_needs_choice(env_file, capsys, monkeypatch):
    p, f = env_file
    pages = [_page(PAGE_ID, "Sam Lee", "PAGETOK", ig=IG_ID),
             _page("777", "Side Project", "PAGETOK2", ig="1999")]
    _transport(monkeypatch, _connect_routes(pages))
    assert main(["--config", str(p), "connect"]) == 2
    cap = capsys.readouterr()
    _assert_no_secrets(cap.out + cap.err)
    assert "Sam Lee" in cap.out + cap.err and "Side Project" in cap.out + cap.err
    assert "--page" in cap.err
    assert f.read_text() == ENV_BEFORE  # untouched

    assert main(["--config", str(p), "connect", "--page", "Side Project"]) == 0
    env = _env_dict(f)
    assert (env["FB_PAGE_ID"], env["IG_ACCOUNT_ID"], env["IG_PAGE_TOKEN"]) == ("777", "1999", "PAGETOK2")

    assert main(["--config", str(p), "connect", "--page", PAGE_ID]) == 0
    env = _env_dict(f)
    assert (env["FB_PAGE_ID"], env["IG_ACCOUNT_ID"], env["IG_PAGE_TOKEN"]) == (PAGE_ID, IG_ID, "PAGETOK")
    _assert_no_secrets(capsys.readouterr().out)


def test_connect_unknown_page_choice(env_file, capsys, monkeypatch):
    p, f = env_file
    _transport(monkeypatch, _connect_routes([_page(PAGE_ID, "Sam Lee", "PAGETOK", ig=IG_ID)]))
    assert main(["--config", str(p), "connect", "--page", "Nope"]) == 2
    assert "Nope" in capsys.readouterr().err
    assert f.read_text() == ENV_BEFORE


def test_connect_no_linked_instagram(env_file, capsys, monkeypatch):
    p, f = env_file
    _transport(monkeypatch, _connect_routes([_page("444", "No IG page", "PAGETOK2")], ig_lookup={
        ("GET", "/v26.0/444"): {"id": "444"}}))
    assert main(["--config", str(p), "connect"]) == 2
    err = capsys.readouterr().err
    assert "No IG page" in err and "linked" in err
    _assert_no_secrets(err)
    assert f.read_text() == ENV_BEFORE


def test_connect_looks_up_ig_account_when_accounts_list_omits_it(env_file, capsys, monkeypatch):
    p, f = env_file
    seen = _transport(monkeypatch, _connect_routes([_page(PAGE_ID, "Sam Lee", "PAGETOK")], ig_lookup={
        ("GET", f"/v26.0/{PAGE_ID}"): {"id": PAGE_ID, "instagram_business_account": {"id": IG_ID}}}))
    assert main(["--config", str(p), "connect"]) == 0
    look = next(r for r in seen if r.url.path == f"/v26.0/{PAGE_ID}")
    assert look.url.params["fields"] == "instagram_business_account"
    assert _env_dict(f)["IG_ACCOUNT_ID"] == IG_ID


def test_connect_missing_env(env_file, capsys, monkeypatch):
    p, f = env_file
    f.write_text("OTHER=1\n")
    _transport(monkeypatch, {})
    assert main(["--config", str(p), "connect"]) == 2
    err = capsys.readouterr().err
    assert "META_APP_ID" in err and "META_APP_SECRET" in err and "FB_USER_TOKEN" in err


def test_connect_error_output_redacted(env_file, capsys, monkeypatch):
    p, f = env_file
    routes = _connect_routes([])
    routes[("GET", "/v26.0/me/accounts")] = lambda req: httpx.Response(
        400, json={"error": {"code": 190, "message": "bad token LONGUSER for app APPSECRET"}})
    _transport(monkeypatch, routes)
    assert main(["--config", str(p), "connect"]) == 1
    cap = capsys.readouterr()
    _assert_no_secrets(cap.out + cap.err)
    assert "bad token ***" in cap.err
    assert f.read_text() == ENV_BEFORE


def test_connect_creates_env_line_when_absent_and_keeps_0600(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\nB=2")  # no trailing newline
    os.chmod(f, 0o644)
    cli.update_env_file(f, {"IG_PAGE_TOKEN": "t", "FB_PAGE_ID": "5", "IG_ACCOUNT_ID": "6"})
    assert f.read_text() == "A=1\nB=2\nIG_PAGE_TOKEN=t\nFB_PAGE_ID=5\nIG_ACCOUNT_ID=6\n"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    cli.update_env_file(f, {"FB_PAGE_ID": "9"})
    assert f.read_text() == "A=1\nB=2\nIG_PAGE_TOKEN=t\nFB_PAGE_ID=9\nIG_ACCOUNT_ID=6\n"
    new = tmp_path / "sub" / ".env"
    cli.update_env_file(new, {"X": "y"})
    assert new.read_text() == "X=y\n" and stat.S_IMODE(os.stat(new).st_mode) == 0o600


def test_connect_help_mentions_page(capsys):
    with pytest.raises(SystemExit) as e:
        main(["connect", "--help"])
    assert e.value.code == 0
    assert "--page" in capsys.readouterr().out


def test_cli_pending_uses_reel_template(cfg, capsys):
    p, _ = cfg
    p.write_text(p.read_text().replace(
        'included with the guide."\nemail_subject = "your {keyword} guide"',
        'since the guide is mostly photos."\nemail_subject = "own {keyword} subject"'))
    assert main(["--config", str(p), "pending"]) == 0
    assert "Subject: own Tokyo subject" in capsys.readouterr().out


def test_cli_add_cost(cfg, capsys):
    p, db = cfg
    assert main(["--config", str(p), "add-cost", "--model", "gpt-6-luna", "--note", "translation",
                 "--input-tokens", "450000", "--output-tokens", "450000"]) == 0
    assert main(["--config", str(p), "add-cost", "--model", "gpt-6-luna", "--usd", "0.4"]) == 0
    rows = db.usage_rows()
    assert rows[0]["input_tokens"] == 450000 and rows[0]["note"] == "translation"
    assert rows[1]["usd_override"] == 0.4 and rows[1]["purpose"] == "one-off"


def test_startup_block_sets_marker(cfg, monkeypatch):
    p, db = cfg
    from igdm.engine import IG_BLOCKED_KEY

    def blocked(*a, **k):
        raise InstagramError(400, 200, "API access blocked.")

    monkeypatch.setattr(cli, "_build", blocked)
    stop = _Stop(stop_after=1)
    assert cli.build_with_retry(load_config(p), p.parent, False, stop) is None
    assert db.kv_get(IG_BLOCKED_KEY)


def test_build_skips_unresolved_paused_reel(cfg, monkeypatch):
    p, _ = cfg
    p.write_text(p.read_text() + '''

[[reels]]
key = "old"
url = "https://www.instagram.com/reel/ARCHIVED1/"
keyword = "Old"
offer = "old slides"
material_url = "https://x.example/old"
handle_comments_after = "2026-10-01T00:00:00"
paused = true
''')
    _fb_env(monkeypatch)
    _transport(monkeypatch, FB_ROUTES)
    from igdm.config import load_config
    ctx, _, _ = cli._build(load_config(p), p.parent, False)
    assert ctx.media_ids == {"tokyo": "m1"}


def test_run_loop_reloads_config_on_first_tick(cfg, monkeypatch):
    p, _ = cfg
    import igdm.reload as reload_mod
    seen = {}

    class Watcher(reload_mod.ConfigWatcher):
        def __init__(self, path, cfg, **kw):
            seen.update(kw)
            super().__init__(path, cfg, **kw)
    monkeypatch.setattr(reload_mod, "ConfigWatcher", Watcher)
    monkeypatch.setattr(reload_mod, "apply_config_changes", lambda ctx, w: False)
    _run_one_tick(p, monkeypatch, "facebook_login")
    assert seen == {"applied": False}


def test_run_loop_idles_between_ticks_with_quick_checks(cfg, monkeypatch):
    p, _ = cfg
    import igdm.reload as reload_mod
    monkeypatch.setattr(reload_mod, "apply_config_changes", lambda ctx, w: False)
    idled = []

    def fake_idle(ctx, seconds, wait, before=None):
        idled.append(seconds)
        assert callable(before)  # config.toml is reloaded before each quick check
        _signal.raise_signal(_signal.SIGTERM)  # stop after the first idle
        assert wait(0) is True  # the loop's stop flag is what idle waits on

    monkeypatch.setattr(cli, "idle", fake_idle)
    monkeypatch.setattr(cli, "build_with_retry", lambda *a, **k: (
        type("Ctx", (), {"now": staticmethod(lambda: NOW),
                         "config": __import__("igdm.config").config.load_config(p)})(),
        None, object()))
    monkeypatch.setattr(cli, "tick", lambda ctx: {"ingest": 0})
    old = (_signal.getsignal(_signal.SIGINT), _signal.getsignal(_signal.SIGTERM))
    try:
        assert main(["--config", str(p), "run"]) == 0
    finally:
        _signal.signal(_signal.SIGINT, old[0])
        _signal.signal(_signal.SIGTERM, old[1])
    assert idled == [60]


def test_dashboard_hosts_parsing():
    from igdm.cli import _dashboard_hosts
    assert _dashboard_hosts(None) == ()
    assert _dashboard_hosts("") == ()
    assert _dashboard_hosts(" igdm.tail1.ts.net , ,b.ts.net ") == ("igdm.tail1.ts.net", "b.ts.net")


# ---- alerts and the healthchecks.io ping

def _no_alert_env(monkeypatch):
    for k in ("NTFY_TOPIC", "HEALTHCHECKS_URL", "ALERT_EMAIL", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def test_ping_swallows_errors():
    from igdm.cli import _ping
    assert _ping("http://127.0.0.1:9/nothing-listens-here") is None


def test_ping_noop_without_url(monkeypatch):
    import httpx

    from igdm.cli import _ping
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("no request without a url"))
    assert _ping(None) is None and _ping("") is None


def test_notifier_none_without_config(monkeypatch):
    from dataclasses import replace

    from fakes import repo_config

    from igdm.cli import _notifier
    _no_alert_env(monkeypatch)
    cfg = repo_config()
    assert _notifier(replace(cfg, email=replace(cfg.email, enabled=False)), False) is None
    monkeypatch.setenv("NTFY_TOPIC", "t")
    assert _notifier(cfg, True) is None  # never in a dry run


def test_notifier_ntfy_only(monkeypatch):
    from dataclasses import replace

    from fakes import repo_config

    from igdm.alerts import FanOut, NtfyNotifier
    from igdm.cli import _notifier
    _no_alert_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = repo_config()
    n = _notifier(replace(cfg, email=replace(cfg.email, enabled=False)), False)
    assert isinstance(n, FanOut) and [type(x) for x in n.notifiers] == [NtfyNotifier]


def test_notifier_ntfy_and_email(monkeypatch):
    from fakes import repo_config

    from igdm.alerts import EmailNotifier, NtfyNotifier
    from igdm.cli import _notifier
    _no_alert_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    n = _notifier(repo_config(), False)
    assert [type(x) for x in n.notifiers] == [NtfyNotifier, EmailNotifier]
    assert n.notifiers[1]._to == "owner@example.com"  # ALERT_EMAIL defaults to the sender


def test_secret_env_has_new_keys():
    from igdm.cli import SECRET_ENV
    assert "NTFY_TOPIC" in SECRET_ENV and "HEALTHCHECKS_URL" in SECRET_ENV


def test_test_alert_without_channels(cfg, capsys, monkeypatch):
    _no_alert_env(monkeypatch)
    p, _ = cfg
    assert main(["--config", str(p), "test-alert"]) == 1
    assert "no alert channels set up" in capsys.readouterr().out


def test_test_alert_sends(cfg, capsys, monkeypatch):
    from igdm import alerts
    _no_alert_env(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "igdm-test-topic")  # redacted from output, so not "t"
    got = []
    monkeypatch.setattr(alerts.NtfyNotifier, "notify", lambda self, t, b: got.append((t, b)))
    p, _ = cfg
    assert main(["--config", str(p), "test-alert"]) == 0
    assert got == [("Test alert", "If you can see this, alerts work.")]
    assert "ntfy" in capsys.readouterr().out


def test_run_refuses_when_moved(cfg, capsys):
    p, _ = cfg
    (p.parent / "data").mkdir(exist_ok=True)
    (p.parent / "data" / "MOVED_TO_SERVER").write_text("2026-11-06 to igdm\n")
    assert main(["--config", str(p), "run", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "handed the bot to the server on 2026-11-06 to igdm" in err
    assert "scripts/rollback.sh" in err


@pytest.mark.parametrize("cmd", [["once"], ["dashboard", "--port", "0"]])
def test_live_commands_refuse_when_moved(cfg, capsys, cmd):
    """Review #6: after the move, a live `igdm once` (or the Mac dashboard) would act on the
    stale database and DM people the server already handled."""
    p, _ = cfg
    (p.parent / "data").mkdir(exist_ok=True)
    (p.parent / "data" / "MOVED_TO_SERVER").write_text("2026-11-06 to igdm\n")
    assert main(["--config", str(p), *cmd]) == 1
    assert "handed the bot to the server" in capsys.readouterr().err


# ---- OpenAI fallback for spotting requests

def test_fallback_model_default(cfg):
    p, _ = cfg
    assert load_config(p).jev_fallback_model == "gpt-6-luna"
    p.write_text(p.read_text().replace('model = "jev-latest"',
                                       'model = "jev-latest"\nfallback_model = "gpt-6-mini"'))
    assert load_config(p).jev_fallback_model == "gpt-6-mini"


def test_classifier_choice(cfg, capsys, monkeypatch):
    from igdm.jev import JevClassifier
    from igdm.openai_classifier import OpenAIClassifier
    p, _ = cfg
    c = load_config(p)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(cli.CliError) as e:
        cli._classifier(c)
    assert "TYPESAFE_API_KEY" in str(e.value) and "OPENAI_API_KEY" in str(e.value)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    got = cli._classifier(c)
    assert isinstance(got, OpenAIClassifier) and got.model == "gpt-6-luna"

    monkeypatch.setenv("TYPESAFE_API_KEY", "jevkey123")
    assert isinstance(cli._classifier(c), JevClassifier)

    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    _fb_env(monkeypatch)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    _transport(monkeypatch, FB_ROUTES)
    assert main(["--config", str(p), "check"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: nothing to spot requests with")
    assert "TYPESAFE_API_KEY" in err and "OPENAI_API_KEY" in err and "Traceback" not in err


def test_check_works_with_openai_key_only(cfg, capsys, monkeypatch):
    p, _ = cfg
    _fb_env(monkeypatch)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setattr(cli, "OpenAIClassifier", _FakeJev)
    _transport(monkeypatch, FB_ROUTES)
    assert main(["--config", str(p), "check"]) == 0
    out = capsys.readouterr().out
    assert "0.900" in out and "sk-secret" not in out


# ---- igdm check: samples come from the owner's own reel

def test_check_samples_follow_reel_keyword():
    reel = ReelConfig("r", "u", "sc", "GUIDE", "the guide", "m", T0)
    assert cli.check_samples(reel) == [
        ("GUIDE", True),
        ("guide please \U0001F64F", True),
        ("nice video but the music is too loud", False),
    ]


class _ScriptedChecker:
    model = "scripted-model"

    def __init__(self, scores):
        self.scores = list(scores)

    def comment_wants_material(self, text, reel):
        return self.scores.pop(0)


def _check_with_scores(cfg, monkeypatch, scores):
    p, _ = cfg
    _fb_env(monkeypatch)
    monkeypatch.setattr(cli, "_classifier", lambda c: _ScriptedChecker(scores))
    _transport(monkeypatch, FB_ROUTES)
    return main(["--config", str(p), "check"])


def test_check_prints_checker_and_expectations(cfg, capsys, monkeypatch):
    assert _check_with_scores(cfg, monkeypatch, [0.95, 0.95, 0.05]) == 0
    out = capsys.readouterr().out
    assert "requests checked by: jev (scripted-model)" in out
    assert "sample 'Tokyo': 0.950 (should be high)" in out
    assert out.count("(should be high)") == 2 and out.count("(should be low)") == 1
    assert "not as expected" not in out


def test_check_fails_when_a_sample_scores_wrong(cfg, capsys, monkeypatch):
    assert _check_with_scores(cfg, monkeypatch, [0.95, 0.95, 0.9]) == 1
    out = capsys.readouterr().out
    assert "(should be low) <- not as expected" in out
    assert out.count("<- not as expected") == 1


def test_check_names_the_openai_fallback(cfg, capsys, monkeypatch):
    p, _ = cfg
    _fb_env(monkeypatch)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setattr(cli, "OpenAIClassifier", _FakeOpenAI)
    _transport(monkeypatch, FB_ROUTES)
    assert main(["--config", str(p), "check"]) == 0
    assert "requests checked by: openai (gpt-6-luna), no Jev key" in capsys.readouterr().out


class _FakeOpenAI(_FakeJev):
    provider = "openai"

    def __init__(self, key, model, **k):
        self.model = model
