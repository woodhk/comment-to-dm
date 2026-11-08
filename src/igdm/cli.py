"""The `igdm` command line."""

from __future__ import annotations

import argparse
import csv
import fcntl
import getpass
import logging
import os
import random
import re
import signal
import sys
import threading
import time
import tomllib
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
from dotenv import dotenv_values, load_dotenv

from igdm import token_store
from igdm.config import Config, ReelConfig, load_config
from igdm.db import FOR_OWNER, Database, Status
from igdm.dryrun import DryRunInstagram
from igdm.engine import COMMENT_POLL_INTERVAL, Context, idle, tick
from igdm.instagram import FB_GRAPH, GraphInstagram, InstagramError
from igdm.jev import Classifier, JevClassifier, JevError
from igdm.mailer import DryRunMailer, GmailSender, MailLoginRefused, MailRefused, Mailer
from igdm.messages import render
from igdm.openai_classifier import OpenAIClassifier
from igdm.upgrade import upgrade_file

log = logging.getLogger("igdm")
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "config.toml"
STARTUP_RETRY_FIRST = timedelta(minutes=5)
STARTUP_RETRY_MAX = timedelta(minutes=60)
HANDLED_BY_OWNER_DAYS = 7
EMAIL_COLUMNS = ["instagram_handle", "email", "date", "video", "ig_user_id", "emailed_at"]
FB_ENV = ("IG_PAGE_TOKEN", "FB_PAGE_ID", "IG_ACCOUNT_ID")  # written by `igdm connect`
CONNECT_ENV = ("META_APP_ID", "META_APP_SECRET", "FB_USER_TOKEN")
SECRET_ENV = ("IG_ACCESS_TOKEN", "TYPESAFE_API_KEY", "IG_PAGE_TOKEN", "META_APP_SECRET",
              "FB_USER_TOKEN", "GMAIL_APP_PASSWORD", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "NTFY_TOPIC", "HEALTHCHECKS_URL")
# Every key `.env.example` lists, in its order, with what it is for (shown by `igdm keys`).
KNOWN_KEYS: tuple[tuple[str, str], ...] = (
    ("META_APP_ID", "Your Meta app's ID, from developers.facebook.com/apps (used by `igdm connect`)."),
    ("META_APP_SECRET", "Your Meta app's secret, from the same page (used by `igdm connect`)."),
    ("FB_USER_TOKEN", "A short-lived Facebook token that `igdm connect` swaps for a lasting one."),
    ("IG_PAGE_TOKEN", "The never-expiring Page token that `igdm connect` saves for you."),
    ("FB_PAGE_ID", "The ID of your Facebook Page, saved by `igdm connect`."),
    ("IG_ACCOUNT_ID", "The ID of your Instagram account, saved by `igdm connect`."),
    ("IG_ACCESS_TOKEN", "Only for the older Instagram Login setup (api = \"instagram_login\")."),
    ("TYPESAFE_API_KEY", "Optional Jev key from typesafe.ai for spotting requests; OpenAI is used without it."),
    ("GMAIL_APP_PASSWORD", "The Gmail app password the bot uses to send your emails."),
    ("OPENAI_API_KEY", "Your OpenAI key, for spotting requests and writing AI replies."),
    ("GEMINI_API_KEY", "Your Gemini key, if you pick Gemini for AI replies."),
    ("NTFY_TOPIC", "A private topic name for phone alerts through the ntfy app (cloud server only)."),
    ("HEALTHCHECKS_URL", "Your healthchecks.io ping URL, which alerts you if the bot stops (cloud server only)."),
    ("ALERT_EMAIL", "Where alert emails go (it defaults to your Gmail address)."),
)
GMAIL_DEFAULT_HOST = "smtp.gmail.com"
GMAIL_DEFAULT_PORT = 465
_KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class CliError(Exception):
    pass


class Redactor:
    """Replaces known secrets with *** in log records and printed output."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, *secrets: str | None) -> None:
        self._secrets.update(s for s in secrets if s)

    def redact(self, text: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        msg = self.redact(record.getMessage())
        if record.exc_info:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        record.msg, record.args = msg, None
        return True


REDACTOR = Redactor()


def _say(text: str, file=None) -> None:
    print(REDACTOR.redact(str(text)), file=file or sys.stdout)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_env(*keys: str) -> dict[str, str]:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise CliError("missing in .env: " + ", ".join(missing))
    REDACTOR.add(*(os.environ[k] for k in keys))
    return {k: os.environ[k] for k in keys}


def _http_transport():
    """The httpx transport for Graph clients (None = real network; tests swap in a mock)."""
    return None


def _instagram(cfg: Config, root: Path) -> tuple[GraphInstagram, Path | None]:
    """The live Graph client for cfg.api, plus the token file (instagram_login only)."""
    if cfg.api == "facebook_login":
        missing = [k for k in FB_ENV if not os.environ.get(k)]
        if missing:
            raise CliError("missing in .env: " + ", ".join(missing)
                           + ". Run `uv run igdm connect` to fill them in.")
        REDACTOR.add(os.environ["IG_PAGE_TOKEN"])
        return GraphInstagram(
            os.environ["IG_PAGE_TOKEN"], base_url=FB_GRAPH, ig_id=os.environ["IG_ACCOUNT_ID"],
            page_id=os.environ["FB_PAGE_ID"], messaging_node=cfg.messaging_node,
            transport=_http_transport(),
        ), None
    env = _require_env("IG_ACCESS_TOKEN")
    token_path = root / "data" / "token.json"
    live_token = token_store.load_token(env["IG_ACCESS_TOKEN"], token_path)
    REDACTOR.add(live_token)
    return GraphInstagram(live_token, transport=_http_transport()), token_path


def update_env_file(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value lines in a .env file: update in place or append; keep every other line.

    Written atomically with mode 0600.
    """
    path = Path(path)
    lines = path.read_text().splitlines() if path.exists() else []
    todo = dict(values)
    out = []
    for line in lines:
        m = _ENV_LINE.match(line)
        if m and m.group(1) in values:
            if m.group(1) in todo:  # first occurrence is updated, later duplicates dropped
                out.append(f"{m.group(1)}={todo.pop(m.group(1))}")
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in todo.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _setup_logging(root: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(root / "logs" / "igdm.log", maxBytes=1_000_000, backupCount=5),
        logging.StreamHandler(sys.stderr),
    ]
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for old in [h for h in logger.handlers if getattr(h, "_igdm", False)]:
        logger.removeHandler(old)  # idempotent: main() may run more than once in a process
        old.close()
    for h in handlers:
        h._igdm = True  # type: ignore[attr-defined]
        h.setFormatter(fmt)
        h.addFilter(REDACTOR.filter)
        logger.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may carry tokens


def _open_db(cfg: Config) -> Database:
    return Database(cfg.db_path)


def _reel_for(cfg: Config, reel_url: str, keyword: str):
    for r in cfg.reels:
        if r.url == reel_url or r.keyword == keyword:
            return r
    return None


def _mailer(cfg: Config, dry_run: bool) -> Mailer | None:
    ec = cfg.email
    if not ec.enabled:
        return None
    if dry_run:
        return DryRunMailer()
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        log.warning("email sending is off: GMAIL_APP_PASSWORD missing in .env")
        return None
    REDACTOR.add(pw)
    return GmailSender(ec.from_address, pw, ec.from_name, ec.smtp_host, ec.smtp_port)


def _notifier(cfg: Config, dry_run: bool):
    """Where alerts go: ntfy (NTFY_TOPIC) and/or email (ALERT_EMAIL, default the sender).
    None when neither is set up, and always in a dry run."""
    from igdm.alerts import EmailNotifier, FanOut, NtfyNotifier
    if dry_run:
        return None
    parts = []
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        parts.append(NtfyNotifier(topic))
    mailer = _mailer(cfg, False)
    if mailer is not None:
        parts.append(EmailNotifier(mailer, os.environ.get("ALERT_EMAIL") or cfg.email.from_address))
    return FanOut(*parts) if parts else None


def _ping(url: str | None) -> None:
    """Tell healthchecks.io the bot is alive. If these stop, it alerts the owner itself."""
    if not url:
        return
    try:
        httpx.get(url, timeout=10)
    except Exception as e:  # noqa: BLE001 - a missed ping is only a missed ping
        log.warning("healthchecks ping failed: %s", type(e).__name__)


def _check_alerts(db, now, notifier, *, startup: bool = False) -> None:
    from igdm.alerts import check_alerts
    if notifier is not None:
        sent = check_alerts(db, now, notifier, startup=startup)
        if sent:
            log.info("alerts sent: %s", sent)


def _writer(cfg: Config):
    """The AI reply writer for [llm], or None (bank-only) when it isn't set up."""
    if cfg.llm is None:
        return None
    from igdm.llm import GeminiWriter, OpenAIWriter
    key_name = "OPENAI_API_KEY" if cfg.llm.provider == "openai" else "GEMINI_API_KEY"
    key = os.environ.get(key_name)
    if not key:
        log.warning("AI-written replies are off: %s missing in .env", key_name)
        return None
    REDACTOR.add(key)
    cls = OpenAIWriter if cfg.llm.provider == "openai" else GeminiWriter
    return cls(key, cfg.llm.model, owner=cfg.owner, timeout=cfg.llm.timeout)


def _classifier(cfg: Config) -> Classifier:
    """Jev when there is a TypeSafe key, else OpenAI with the cheap fallback model."""
    if os.environ.get("TYPESAFE_API_KEY"):
        key = _require_env("TYPESAFE_API_KEY")["TYPESAFE_API_KEY"]
        return JevClassifier(key, cfg.jev_model, owner_name=cfg.owner.name)
    if os.environ.get("OPENAI_API_KEY"):
        key = _require_env("OPENAI_API_KEY")["OPENAI_API_KEY"]
        return OpenAIClassifier(key, cfg.jev_fallback_model, owner_name=cfg.owner.name)
    raise CliError("nothing to spot requests with: put TYPESAFE_API_KEY (Jev) or "
                   "OPENAI_API_KEY in .env")


def _build(cfg: Config, root: Path, dry_run: bool, *,
           comment_poll_interval: timedelta = COMMENT_POLL_INTERVAL,
           ) -> tuple[Context, Path | None, GraphInstagram]:
    real, token_path = _instagram(cfg, root)
    classifier = _classifier(cfg)
    if dry_run:
        from dataclasses import replace
        cfg = replace(cfg, db_path=root / "data" / "dryrun.sqlite")
        ig = DryRunInstagram(real)
        sleep = lambda s: None  # noqa: E731
    else:
        ig = real
        sleep = time.sleep
    own_id, own_username = ig.me()
    media_ids: dict[str, str] = {}
    unresolved = []
    for r in cfg.reels:
        mid = ig.find_media_id(r.shortcode)
        if mid is None and r.paused:  # e.g. an archived post: paused reels never need it
            log.warning("paused reel %r did not resolve to a media id; skipping it", r.key)
        elif mid is None:
            unresolved.append(r.key)
        else:
            media_ids[r.key] = mid
    if unresolved:
        raise CliError("reel(s) did not resolve to a media id: " + ", ".join(unresolved))
    from igdm.metering import MeteredClassifier, MeteredWriter
    db = _open_db(cfg)
    writer = _writer(cfg)
    ctx = Context(
        config=cfg, db=db, ig=ig, jev=MeteredClassifier(classifier, db), now=_utcnow, sleep=sleep,
        rng=random.Random(), own_id=own_id, own_username=own_username, media_ids=media_ids,
        comment_poll_interval=comment_poll_interval, mailer=_mailer(cfg, dry_run),
        writer=MeteredWriter(writer, db) if writer else None,
    )
    return ctx, token_path, real


def _acquire_lock(root: Path):
    """Exclusive lock so only one live igdm sends at a time. None if another process holds it.

    The returned file keeps the lock until it is closed (or the process exits).
    """
    path = root / "data" / "igdm.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


ALREADY_RUNNING = "already running: another igdm is sending (data/igdm.lock is held); nothing done"


def _note_blocked(cfg: Config | None, err: BaseException) -> None:
    """Startup failed because Meta blocked the app: tell the dashboard."""
    from igdm.engine import note_access
    from igdm.instagram import is_access_blocked
    if cfg is None or not is_access_blocked(err):
        return
    try:
        note_access(_open_db(cfg), err, _utcnow())
    except Exception:  # noqa: BLE001 - the banner is best effort
        log.exception("could not record the Instagram block")


def build_with_retry(cfg: Config, root: Path, dry_run: bool, stop):
    """_build, retried with backoff (5 min doubling to 60 min) until it works or we're stopped.

    Returns None if stopped first. Keeps launchd from restarting a broken start every few seconds.
    """
    delay = STARTUP_RETRY_FIRST
    while not stop.is_set():
        try:
            return _build(cfg, root, dry_run)
        except Exception as e:  # noqa: BLE001 - any startup failure is retried
            _note_blocked(cfg, e)
            try:  # a Meta block at startup is reported by name, not only by the missing pings
                _check_alerts(_open_db(cfg), _utcnow(), _notifier(cfg, dry_run), startup=True)
            except Exception:  # noqa: BLE001
                log.exception("couldn't check alerts after the failed start")
            minutes = int(delay.total_seconds() // 60)
            log.error("STARTUP FAILED (%s: %s). igdm is NOT running; retrying in %d min. "
                      "Run `uv run igdm check` to see what is wrong.", type(e).__name__, e, minutes)
            if stop.wait(delay.total_seconds()):
                break
            delay = min(delay * 2, STARTUP_RETRY_MAX)
    return None


def check_samples(reel: ReelConfig) -> list[tuple[str, bool]]:
    """Comments for `igdm check`, built from the reel's own keyword: (text, should be a request)."""
    return [
        (reel.keyword, True),
        (f"{reel.keyword.lower()} please 🙏", True),
        ("nice video but the music is too loud", False),
    ]


# ---------------------------------------------------------------- commands

def cmd_check(cfg: Config, root: Path, args) -> int:
    ig, _ = _instagram(cfg, root)
    checker = _classifier(cfg)
    if cfg.api == "facebook_login":
        _say(f"api: facebook_login (Page {os.environ['FB_PAGE_ID']}, "
             f"DMs via {cfg.messaging_node})")
    else:
        _say("api: instagram_login")
    own_id, username = ig.me()
    _say(f"account: @{username} (id {own_id})")
    if getattr(checker, "provider", "jev") == "openai":
        _say(f"requests checked by: openai ({checker.model}), no Jev key")
    else:
        _say(f"requests checked by: jev ({checker.model})")
    failed = False
    first_reel = None
    for r in cfg.reels:
        mid = ig.find_media_id(r.shortcode)
        if mid is None:
            _say(f"reel {r.key}: ERROR could not resolve {r.url}")
            failed = True
            continue
        first_reel = first_reel or r
        _say(f"reel {r.key}: media id {mid}, {len(ig.list_comments(mid))} comments")
    if first_reel:
        for text, should_be_request in check_samples(first_reel):
            try:
                score = checker.comment_wants_material(text, first_reel)
            except JevError as e:
                _say(f"sample {text!r}: ERROR {e}")
                failed = True
                continue
            as_expected = (score >= cfg.comment_threshold) == should_be_request
            _say(f"sample {text!r}: {score:.3f} (should be {'high' if should_be_request else 'low'})"
                 + ("" if as_expected else " <- not as expected"))
            failed = failed or not as_expected
    convs = ig.list_conversations(own_id, _utcnow() - timedelta(days=7))
    _say(f"conversations in the last 7 days: {len(convs)}")
    return 1 if failed else 0


def cmd_once(cfg: Config, root: Path, args) -> int:
    if not args.dry_run and _moved(root):
        return 1
    lock = None
    if not args.dry_run:  # dry runs send nothing and use their own database
        lock = _acquire_lock(root)
        if lock is None:
            _say(ALREADY_RUNNING)
            return 0
    try:
        # a manual pass always lists comments (the agent's `run` lists them on every tick)
        ctx, _, _ = _build(cfg, root, args.dry_run, comment_poll_interval=timedelta(0))
        result = tick(ctx)
    finally:
        if lock:
            lock.close()
    _say(result)
    return 1 if any(isinstance(v, str) and v.startswith("error:") for v in result.values()) else 0


def refresh_if_due(real: GraphInstagram, token_path: Path, env_token: str, now: datetime) -> bool:
    """Refresh the token when due; save it and update the live client. Never raises."""
    if not token_store.needs_refresh(token_path, now, env_token=env_token):
        return False
    try:
        new = real.refresh_token()
    except Exception as e:  # noqa: BLE001
        token_store.record_attempt(token_path, now, env_token)
        log.error("token refresh failed (will retry in 6 h): %s", e)
        return False
    REDACTOR.add(new)
    token_store.save_token(token_path, new, now, env_token)
    real.set_token(new)
    log.info("access token refreshed")
    return True


MOVED_MARKER = "MOVED_TO_SERVER"  # in data/: this machine handed the bot to the server


def _moved(root: Path) -> bool:
    """True (and says why) when this Mac handed the bot to the server: its database is stale,
    so acting on it would DM and email people the server already handled."""
    moved = root / "data" / MOVED_MARKER
    if not moved.exists():
        return False
    when = moved.read_text(encoding="utf-8").strip() or "an earlier day"
    _say(f"This Mac handed the bot to the server on {when}. Not starting, so people don't "
         "get two DMs. To move back, run scripts/rollback.sh.", sys.stderr)
    return True


def cmd_run(cfg: Config, root: Path, args) -> int:
    if _moved(root):
        return 1
    lock = None
    if not args.dry_run:
        lock = _acquire_lock(root)
        if lock is None:
            _say(ALREADY_RUNNING)
            return 0
    try:
        return _run_loop(cfg, root, args)
    finally:
        if lock:
            lock.close()


def _run_loop(cfg: Config, root: Path, args) -> int:
    stop = threading.Event()

    def _stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    built = build_with_retry(cfg, root, args.dry_run, stop)
    if built is None:
        log.info("igdm stopped before it started")
        return 0
    ctx, token_path, real = built
    from igdm.reload import ConfigWatcher, apply_config_changes
    watcher = ConfigWatcher(args.config.resolve(), ctx.config, applied=False)
    env_token = os.environ.get("IG_ACCESS_TOKEN", "")
    notifier = _notifier(cfg, args.dry_run)
    ping_url = None if args.dry_run else os.environ.get("HEALTHCHECKS_URL")
    log.info("igdm running (dry_run=%s, poll=%ss, alerts=%s, healthchecks=%s)", args.dry_run,
             cfg.poll_seconds, "on" if notifier else "off", "on" if ping_url else "off")
    while not stop.is_set():
        try:
            apply_config_changes(ctx, watcher)  # new / edited / paused reels, between ticks only
        except Exception:  # noqa: BLE001 - a reload problem never stops a tick
            log.exception("config reload crashed")
        try:
            if not args.dry_run and cfg.api == "instagram_login":  # Page tokens never expire
                refresh_if_due(real, token_path, env_token, ctx.now())
            log.info("tick: %s", tick(ctx))
        except Exception:  # noqa: BLE001 - keep the daemon alive
            log.exception("tick crashed")
        try:
            _check_alerts(ctx.db, ctx.now(), notifier)
        except Exception:  # noqa: BLE001
            log.exception("alert check crashed")
        _ping(ping_url)
        if stop.is_set():
            break
        try:  # until the next tick: quick checks for chatting people and due first DMs
            idle(ctx, ctx.config.poll_seconds, stop.wait,
                 before=lambda: apply_config_changes(ctx, watcher))
        except Exception:  # noqa: BLE001
            log.exception("idle crashed")
            stop.wait(ctx.config.poll_seconds)
    log.info("igdm stopped")
    return 0


def _dashboard_hosts(raw: str | None) -> tuple[str, ...]:
    """DASHBOARD_HOSTS: comma-separated extra names the dashboard answers to (Tailscale)."""
    return tuple(h.strip() for h in (raw or "").split(",") if h.strip())


def cmd_test_alert(cfg: Config, root: Path, args) -> int:
    n = _notifier(cfg, False)
    if n is None:
        _say("no alert channels set up (add NTFY_TOPIC to .env, or turn on [email] with "
             "GMAIL_APP_PASSWORD)")
        return 1
    ok = True
    for ch in n.notifiers:
        name = "ntfy" if type(ch).__name__ == "NtfyNotifier" else "email"
        try:
            ch.notify("Test alert", "If you can see this, alerts work.")
            _say(f"{name}: sent")
        except Exception as e:  # noqa: BLE001
            ok = False
            _say(f"{name}: FAILED ({type(e).__name__}: {e})")  # redacted: the topic is a secret
    return 0 if ok else 1


def cmd_status(cfg: Config, root: Path, args) -> int:
    db = _open_db(cfg)
    counts = db.status_counts()
    if counts:
        for k, v in sorted(counts.items()):
            print(f"{k}: {v}")
    else:
        print("no requests yet")
    rows = db.with_status(*sorted(FOR_OWNER))
    if rows:
        print("\nfor you:")
    for r in rows:
        print(_status_line(cfg, r))
    handled = db.updated_since(Status.HANDLED_BY_OWNER,
                               _utcnow() - timedelta(days=HANDLED_BY_OWNER_DAYS))
    if handled:
        print(f"\nbot stopped because you replied by hand — check if that's right "
              f"(last {HANDLED_BY_OWNER_DAYS} days):")
    for r in handled:
        print(_status_line(cfg, r))
    return 0


def _status_line(cfg: Config, r) -> str:
    reel = next((x for x in cfg.reels if x.key == r.reel_key), None)
    where = reel.url if reel else r.reel_key
    return f'[{r.status.value}] @{r.username}: "{r.text}" ({where}) {r.note or ""}'.rstrip()


def _video(row) -> str:
    return f"{row.reel_url} ({row.reel_keyword})"


def cmd_emails(cfg: Config, root: Path, args) -> int:
    rows = _open_db(cfg).emails()
    out = open(args.csv, "w", newline="", encoding="utf-8") if args.csv else sys.stdout
    try:
        w = csv.writer(out)
        w.writerow(EMAIL_COLUMNS)
        for e in rows:
            w.writerow([e.instagram_handle, e.email, e.provided_at.isoformat(), _video(e),
                        e.ig_user_id or "", e.emailed_at.isoformat() if e.emailed_at else ""])
    finally:
        if args.csv:
            out.close()
    if args.csv:
        print(f"wrote {len(rows)} rows to {args.csv}")
    return 0


def cmd_pending(cfg: Config, root: Path, args) -> int:
    rows = _open_db(cfg).emails(pending_only=True)
    if not rows:
        print("nothing pending")
    for e in rows:
        reel = _reel_for(cfg, e.reel_url, e.reel_keyword)
        keyword = reel.keyword if reel else e.reel_keyword
        material = reel.material_url if reel else ""
        kw = dict(name=e.instagram_handle, keyword=keyword, material_url=material)
        print(f"[{e.id}] To: {e.email}")
        subject = reel.email_subject if reel else cfg.messages.email_subject
        body = reel.email_body if reel else cfg.messages.email_body
        print(f"Subject: {render(subject, **kw)}")
        print()
        print(render(body, **kw))
        print("\n" + "-" * 60 + "\n")
    return 0


def cmd_mark_sent(cfg: Config, root: Path, args) -> int:
    n = _open_db(cfg).mark_emailed(None if args.all else args.ids, _utcnow())
    print(f"marked {n} email(s) as sent")
    return 0


def cmd_dashboard(cfg: Config, root: Path, args) -> int:
    from igdm.dashboard import serve
    if _moved(root):
        return 1
    clients: dict[str, GraphInstagram] = {}

    def find_media(shortcode: str):
        """Only the Reels tab's link check uses Instagram, so connect on first use."""
        if "ig" not in clients:
            try:
                clients["ig"] = _instagram(cfg, root)[0]
            except Exception as e:  # noqa: BLE001 - shown beside the link field, never a crash
                raise InstagramError(0, None, f"Instagram isn't set up for the dashboard ({e})") from e
        return clients["ig"].find_media(shortcode)

    hosts = _dashboard_hosts(os.environ.get("DASHBOARD_HOSTS"))
    server = serve(cfg, port=args.port, config_path=args.config.resolve(), find_media=find_media,
                   extra_hosts=hosts)
    where = f"http://127.0.0.1:{args.port}" + (f" and https://{hosts[0]}" if hosts else "")
    log.info("dashboard on %s", where)
    print(f"dashboard running on {where} (ctrl+c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_add_cost(cfg: Config, root: Path, args) -> int:
    """Record an AI cost the bot didn't make itself (e.g. a one-off translation run)."""
    _open_db(cfg).log_usage(
        args.provider, args.model, "one-off", input_tokens=args.input_tokens,
        output_tokens=args.output_tokens, calls=0, usd_override=args.usd, note=args.note)
    print(f"recorded {args.model} cost" + (f" (${args.usd:.2f})" if args.usd is not None else ""))
    return 0


def cmd_refresh_token(cfg: Config, root: Path, args) -> int:
    if cfg.api == "facebook_login":
        print("not needed: api = \"facebook_login\" uses a Page token that does not expire")
        return 0
    env = _require_env("IG_ACCESS_TOKEN")
    path = root / "data" / "token.json"
    live_token = token_store.load_token(env["IG_ACCESS_TOKEN"], path)
    REDACTOR.add(live_token)
    ig = GraphInstagram(live_token)
    new = ig.refresh_token()
    REDACTOR.add(new)
    token_store.save_token(path, new, _utcnow(), env["IG_ACCESS_TOKEN"])
    print("token refreshed and saved to data/token.json")
    return 0


def _linked_ig(page: dict, transport) -> str | None:
    """The Page's Instagram account id, asking the Page itself if /me/accounts left it out."""
    if "instagram_business_account" in page:
        return (page.get("instagram_business_account") or {}).get("id")
    if not page.get("access_token"):
        return None
    try:
        d = GraphInstagram(page["access_token"], base_url=FB_GRAPH, transport=transport).get(
            page["id"], {"fields": "instagram_business_account"})
    except InstagramError as e:
        log.warning("could not check Page %s for a linked Instagram account: %s", page["id"], e)
        return None
    return (d.get("instagram_business_account") or {}).get("id")


def cmd_connect(cfg: Config, root: Path, args) -> int:
    """FB_USER_TOKEN -> long-lived user token -> Page token (never expires) -> .env."""
    env = _require_env(*CONNECT_ENV)
    transport = _http_transport()
    d = GraphInstagram(env["FB_USER_TOKEN"], base_url=FB_GRAPH, transport=transport).get(
        "oauth/access_token", {
            "grant_type": "fb_exchange_token", "client_id": env["META_APP_ID"],
            "client_secret": env["META_APP_SECRET"], "fb_exchange_token": env["FB_USER_TOKEN"],
        })
    long_token = d.get("access_token")
    if not long_token:
        raise CliError("Facebook did not return a long-lived token; generate a new FB_USER_TOKEN")
    REDACTOR.add(long_token)
    pages = GraphInstagram(long_token, base_url=FB_GRAPH, transport=transport).paged(
        "me/accounts", {"fields": "id,name,access_token,instagram_business_account", "limit": 100})
    REDACTOR.add(*(p.get("access_token") for p in pages))
    linked = [(p, str(ig)) for p in pages if (ig := _linked_ig(p, transport))]
    if not linked:
        names = ", ".join(p.get("name", p["id"]) for p in pages) or "none"
        raise CliError(f"no Facebook Page with a linked Instagram account (Pages found: {names}). "
                       "Link Instagram to a Page in Business Suite, Settings, Accounts, then run "
                       "`uv run igdm connect` again.")
    if args.page:
        want = args.page.casefold()
        match = [x for x in linked if want in (x[0]["id"], str(x[0].get("name", "")).casefold())]
        if not match:
            raise CliError(f"no Page named or with id {args.page!r} has a linked Instagram account")
        page, ig_id = match[0]
    elif len(linked) > 1:
        _say("Pages with a linked Instagram account:")
        for p, ig in linked:
            _say(f"  {p.get('name', '?')} (Page id {p['id']}, Instagram id {ig})")
        raise CliError("several Pages have a linked Instagram account; "
                       "run again with --page NAME_OR_ID")
    else:
        page, ig_id = linked[0]
    page_token = page.get("access_token")
    if not page_token:
        raise CliError(f"no Page token for {page.get('name', page['id'])}; check the "
                       "pages_show_list permission on FB_USER_TOKEN")
    _, username = GraphInstagram(page_token, base_url=FB_GRAPH, ig_id=ig_id, page_id=page["id"],
                                 transport=transport).me()
    update_env_file(root / ".env", {"IG_PAGE_TOKEN": page_token, "FB_PAGE_ID": page["id"],
                                    "IG_ACCOUNT_ID": ig_id})
    _say(f"Page: {page.get('name', '?')} (id {page['id']})")
    _say(f"Instagram: @{username} (id {ig_id})")
    _say("saved IG_PAGE_TOKEN, FB_PAGE_ID and IG_ACCOUNT_ID to .env "
         "(the Page token does not expire). Next: uv run igdm check")
    if cfg.api != "facebook_login":
        _say('note: config.toml has api = "instagram_login"; set api = "facebook_login" to use these')
    return 0


def cmd_keys(root: Path) -> int:
    """List which keys are set in .env. Never prints a value."""
    present = {k for k, v in dotenv_values(root / ".env").items() if v}
    for name, purpose in KNOWN_KEYS:
        _say(f"{name}: {'set' if name in present else 'missing'}")
        _say(f"  {purpose}")
    return 0


def _clean_pasted(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def cmd_set_key(root: Path, args) -> int:
    """Save one key to .env from a hidden prompt, so the value never enters the chat."""
    known = [name for name, _ in KNOWN_KEYS]
    if args.name not in known:
        if _KEY_NAME.match(args.name):
            _say(f"{args.name} is not a key this bot uses. The keys are: {', '.join(known)}.",
                 sys.stderr)
        else:  # it may be a pasted secret: never echo it
            REDACTOR.add(args.name)
            _say(f"That isn't a key name. The keys are: {', '.join(known)}.", sys.stderr)
        return 1
    if args.stdin:  # only for values Claude made up itself, such as an ntfy topic
        raw = sys.stdin.readline()
    else:
        raw = getpass.getpass(f"Paste {args.name} and press Enter (it stays hidden): ")
    REDACTOR.add(raw.strip(), _clean_pasted(raw))
    value = _clean_pasted(raw)
    if args.name == "GMAIL_APP_PASSWORD":  # shown in groups of four; it never has spaces
        value = "".join(value.split())
    if not value:
        _say("Nothing was pasted, so nothing changed.", sys.stderr)
        return 1
    if "\n" in value or "\r" in value:
        _say("That had more than one line, so nothing changed. Paste just the key.", sys.stderr)
        return 1
    if " #" in value or "${" in value or value[0] in "\"'" or value[-1] in "\"'":
        _say("That value has characters .env can't store safely. Check you copied only the key.",
             sys.stderr)
        return 1
    update_env_file(root / ".env", {args.name: value})
    _say(f"Saved {args.name}.")
    return 0


def cmd_test_gmail(config_path: Path, args) -> int:
    """Sign in to Gmail with the app password and sign out. Sends nothing."""
    address, host, port = args.address, GMAIL_DEFAULT_HOST, GMAIL_DEFAULT_PORT
    if config_path.exists():
        email = tomllib.loads(config_path.read_text()).get("email", {})
        address = address or email.get("from_address")
        host = email.get("smtp_host", host)
        port = int(email.get("smtp_port", port))
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        _say("GMAIL_APP_PASSWORD is not set. Run `bash scripts/ask_key.sh GMAIL_APP_PASSWORD` first.",
             sys.stderr)
        return 1
    REDACTOR.add(password)
    if not address:
        _say("No Gmail address to sign in with. Put it under [email] from_address in "
             "config.toml, or pass --address you@gmail.com.", sys.stderr)
        return 1
    try:
        GmailSender(address, password, "", host, port).check_login()
    except MailLoginRefused:
        _say(f"Gmail refused the sign-in for {address}. Check the app password and that "
             "2-Step Verification is on.", sys.stderr)
        return 1
    except MailRefused:
        _say(f"Could not reach Gmail ({host}). Check the internet connection and try again.",
             sys.stderr)
        return 1
    _say(f"Gmail sign-in works for {address}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="igdm", description="Instagram comment to DM automation")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to config.toml")
    # also accept --config after the subcommand (`igdm status --config X`)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS, help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser(parents=[common], name="check", help="verify the keys, reels and request checker (sends nothing)")
    for name, h in (("once", "run one tick"), ("run", "run the daemon loop")):
        s = sub.add_parser(name, help=h, parents=[common])
        s.add_argument("--dry-run", action="store_true", help="log sends instead of making them")
    sub.add_parser(parents=[common], name="status", help="counts and items for you")
    sub.add_parser(parents=[common], name="test-alert",
                   help="send a test alert to the phone (ntfy) and email")
    s = sub.add_parser(parents=[common], name="emails", help="list or export captured emails")
    s.add_argument("--csv", type=Path)
    sub.add_parser(parents=[common], name="pending", help="show emails still to send, rendered")
    s = sub.add_parser(parents=[common], name="mark-sent", help="mark emails as sent")
    s.add_argument("ids", nargs="*", type=int)
    s.add_argument("--all", action="store_true")
    s = sub.add_parser(parents=[common], name="dashboard",
                       help="serve the local dashboard on http://127.0.0.1:PORT")
    s.add_argument("--port", type=int, default=8787)
    s = sub.add_parser(parents=[common], name="add-cost",
                       help="record a one-off AI cost for the dashboard's spend total")
    s.add_argument("--model", required=True)
    s.add_argument("--provider", default="manual")
    s.add_argument("--usd", type=float, help="the cost in USD, if you know it")
    s.add_argument("--input-tokens", type=int, default=0)
    s.add_argument("--output-tokens", type=int, default=0)
    s.add_argument("--note", default="")
    sub.add_parser(parents=[common], name="refresh-token",
                   help="refresh the long-lived token now (instagram_login only)")
    s = sub.add_parser(parents=[common], name="connect",
                       help="swap FB_USER_TOKEN for a never-expiring Page token; save it to .env")
    s.add_argument("--page", metavar="NAME_OR_ID",
                   help="which Facebook Page to use, if several have a linked Instagram account")
    sub.add_parser(parents=[common], name="upgrade",
                   help="bring config.toml up to the settings version this bot needs")
    sub.add_parser(parents=[common], name="keys",
                   help="show which keys are set in .env (never prints a value)")
    s = sub.add_parser(parents=[common], name="set-key",
                       help="save one key to .env from a hidden prompt")
    s.add_argument("name", metavar="NAME", help="the key, for example OPENAI_API_KEY")
    s.add_argument("--stdin", action="store_true",
                   help="read the value from one line of stdin (only for values you made up)")
    s = sub.add_parser(parents=[common], name="test-gmail",
                       help="check the Gmail sign-in works (sends nothing)")
    s.add_argument("--address", help="the Gmail address (default: [email] from_address)")
    return p


def cmd_upgrade(config_path: Path) -> int:
    start, end, backup = upgrade_file(config_path, datetime.now())
    if backup is None:
        _say(f"settings are already up to date (version {end})")
    else:
        _say(f"settings upgraded from version {start} to {end} (old copy saved as {backup.name})")
    return 0


COMMANDS = {
    "check": cmd_check, "once": cmd_once, "run": cmd_run, "status": cmd_status,
    "emails": cmd_emails, "pending": cmd_pending, "mark-sent": cmd_mark_sent,
    "refresh-token": cmd_refresh_token, "connect": cmd_connect, "add-cost": cmd_add_cost,
    "dashboard": cmd_dashboard, "test-alert": cmd_test_alert,
}


LOGGING_COMMANDS = {"once", "check", "run", "refresh-token", "connect", "dashboard", "test-alert"}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.cmd == "mark-sent" and not (args.all or args.ids):
        parser.error("mark-sent needs one or more IDs, or --all")
    config_path = args.config.resolve()
    root = config_path.parent
    load_dotenv(root / ".env")
    REDACTOR.add(*(os.environ.get(k) for k in SECRET_ENV))
    if args.cmd in LOGGING_COMMANDS:
        _setup_logging(root)  # every command that holds secrets logs through the Redactor
    try:
        if args.cmd == "upgrade":  # before load_config: an old file won't load
            return cmd_upgrade(config_path)
        if args.cmd == "keys":  # these three work before config.toml exists
            return cmd_keys(root)
        if args.cmd == "set-key":
            return cmd_set_key(root, args)
        if args.cmd == "test-gmail":
            return cmd_test_gmail(config_path, args)
        cfg = load_config(config_path)
        return COMMANDS[args.cmd](cfg, root, args)
    except CliError as e:
        _say(f"error: {e}", sys.stderr)
        return 2
    except (InstagramError, JevError) as e:
        _say(f"error: {e}", sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - never leak a raw traceback (it may hold secrets)
        _say(f"error: {type(e).__name__}: {e}", sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
