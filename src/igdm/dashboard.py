"""The owner's local dashboard: one page on 127.0.0.1, reading the bot's SQLite database.

It never sends anything on Instagram or Gmail. Its buttons only change rows; the bot acts on
them next tick (got-it DM, email send). The Reels tab writes [[reels]] blocks in config.toml
(the bot reloads it between ticks) and asks Instagram once per new link whether it is one of
the owner's posts. Requests from any other Host or Origin are refused, so another website open in
the browser can't read the page or press its buttons.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import threading
import time
from typing import Callable

import re

from igdm.activity import activity
from igdm.config import Config, load_config
from igdm.db import FOR_OWNER, Database
from igdm.instagram import Media
from igdm.messages import find_email
from igdm.milestones import milestones_state
from igdm.outbox import render_email
from igdm.reelfile import CONFIG_LOCK, add_reel, set_paused, update_reel, write_config
from igdm.reelform import check_reel
from igdm.stats import dashboard_state, has_leads, reels_state

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("dashboard.html")
MAX_BODY = 64_000  # a reel form carries the whole email body
NO_CONFIG = "the dashboard was started without a config file"
HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
MAX_NAME = 60
MISS_TTL = 120.0  # seconds before a link that wasn't found is asked about again


class _Live:
    """The config the dashboard works from, reloaded when config.toml changes on disk.

    A file that doesn't load keeps the last good config (the bot reports the error).
    """

    def __init__(self, cfg: Config, path: Path | None):
        self.cfg, self.path = cfg, path
        self._state = self._stat()
        self._lock = threading.Lock()

    def _stat(self):
        if self.path is None:
            return None
        st = self.path.stat()
        return (st.st_mtime_ns, st.st_size)

    def get(self) -> Config:
        if self.path is None:
            return self.cfg
        with self._lock:
            state = self._stat()
            if state != self._state:
                self._state = state
                try:
                    self.cfg = load_config(self.path)
                except Exception as e:  # noqa: BLE001 - keep the last good config
                    log.warning("dashboard: config.toml didn't load, keeping the last good one: %s", e)
            return self.cfg


class _Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _handler(live: _Live, find_media: Callable[[str], Media | None] | None,
             extra_hosts: tuple[str, ...] = ()):
    found: dict[str, Media] = {}  # links Instagram already confirmed, for the server's life
    missed: dict[str, tuple[float, Exception | None]] = {}  # misses, so typing can't flood Meta

    def lookup(shortcode: str) -> Media | None:
        if shortcode in found:
            return found[shortcode]
        miss = missed.get(shortcode)
        if miss and time.monotonic() - miss[0] < MISS_TTL:
            if miss[1] is not None:
                raise miss[1]
            return None
        try:
            m = find_media(shortcode) if find_media else None
        except Exception as e:
            missed[shortcode] = (time.monotonic(), e)
            raise
        if m is None:
            missed[shortcode] = (time.monotonic(), None)
            return None
        found[shortcode] = m
        return m

    class Handler(BaseHTTPRequestHandler):
        server_version = "igdm-dashboard"

        def log_message(self, fmt, *args):  # noqa: D401 - quiet; errors still logged below
            log.debug("dashboard: " + fmt, *args)

        # -- safety
        def _local_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _check_origin(self) -> None:
            # Exact names only: localhost on this port, or the Tailscale name(s) in
            # DASHBOARD_HOSTS, which `tailscale serve` reaches over https.
            local = self._local_hosts()
            hosts = local | set(extra_hosts) | {f"{h}:443" for h in extra_hosts}
            if self.headers.get("Host", "") not in hosts:
                raise _Refused(403, "wrong host")
            origins = {f"http://{h}" for h in local} | {f"https://{h}" for h in extra_hosts}
            origin = self.headers.get("Origin")
            if origin is not None and origin not in origins:
                raise _Refused(403, "wrong origin")

        # -- responses
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise _Refused(413, "too big")
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                d = json.loads(raw)
            except ValueError as e:
                raise _Refused(400, "body must be JSON") from e
            return d if isinstance(d, dict) else {}

        def _wrap(self, fn) -> None:
            try:
                self._check_origin()
                fn()
            except _Refused as e:
                self._json(e.status, {"error": str(e)})
            except Exception:  # noqa: BLE001 - never crash the server
                log.exception("dashboard request failed")
                self._json(500, {"error": "something went wrong, check logs/igdm.log"})

        # -- routes
        def do_GET(self) -> None:  # noqa: N802
            self._wrap(self._get)

        def do_POST(self) -> None:  # noqa: N802
            self._wrap(self._post)

        def _get(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                cfg = live.get()
                self._json(200, dashboard_state(Database(cfg.db_path), cfg, _now()))
            elif path == "/api/activity":
                cfg = live.get()
                q = parse_qs(urlsplit(self.path).query)
                try:
                    limit = min(max(int(q.get("limit", ["50"])[0]), 1), 200)
                    before = q.get("before", [None])[0]
                    if before and datetime.fromisoformat(before).tzinfo is None:
                        raise ValueError("before needs a time zone")
                except ValueError:
                    raise _Refused(400, "bad limit or before") from None
                self._json(200, activity(Database(cfg.db_path), cfg, _now(),
                                         before=before, limit=limit))
            elif path == "/api/milestones":
                cfg = live.get()
                self._json(200, milestones_state(Database(cfg.db_path), cfg, _now()))
            elif path.startswith("/api/emails/"):
                self._email_status(path.rsplit("/", 1)[1])
            elif path == "/api/reels":
                self._need_config()
                cfg = live.get()
                self._json(200, reels_state(Database(cfg.db_path), cfg))
            else:
                raise _Refused(404, "not found")

        def _post(self) -> None:
            parts = [unquote(p) for p in urlsplit(self.path).path.strip("/").split("/")]
            if parts[:2] == ["api", "reels"]:
                self._need_config()
                self._reels_post(parts[2:])
                return
            if len(parts) == 3 and parts[:2] == ["api", "emails"] and parts[2] in ("manual", "preview"):
                self._manual(parts[2])
                return
            if len(parts) != 4 or parts[0] != "api":
                raise _Refused(404, "not found")
            _, kind, key, action = parts
            db = Database(live.get().db_path)
            if kind == "flags":
                self._flag_action(db, key, action)
            elif kind == "emails":
                self._email_action(db, key, action)
            else:
                raise _Refused(404, "not found")
            self._json(200, {"ok": True})

        def _flag_action(self, db: Database, cid: str, action: str) -> None:
            if not db.has_comment(cid):
                raise _Refused(404, "no such conversation")
            req = db.get(cid)
            if action == "carry-on":
                if not db.carry_on(cid, _now()):
                    raise _Refused(409, "this one can't be handed back to the bot")
                return
            if req.status not in FOR_OWNER:
                raise _Refused(409, "this isn't flagged any more")
            if action == "done":
                db.mark_done(cid, _now())
            elif action == "email":
                email = find_email(str(self._body().get("email", "")).strip())
                if not email:
                    raise _Refused(400, "that doesn't look like an email address")
                cfg = live.get()
                reel = next((r for r in cfg.reels if r.key == req.reel_key), None)
                if reel is None:
                    raise _Refused(409, "that reel isn't in config.toml any more")
                db.resolve_with_email(cid, email, now=_now(), delay_minutes=cfg.email.delay_minutes,
                                      reel_url=reel.url, keyword=reel.keyword)
            else:
                raise _Refused(404, "unknown action")

        def _email_action(self, db: Database, key: str, action: str) -> None:
            try:
                email_id = int(key)
            except ValueError as e:
                raise _Refused(404, "no such email") from e
            if db.email_by_id(email_id) is None:
                raise _Refused(404, "no such email")
            if action == "send-now":
                if not db.send_now(email_id, _now()):
                    raise _Refused(409, "already sent or already queued")
            elif action == "mark-sent":
                db.mark_emailed([email_id], _now())
            else:
                raise _Refused(404, "unknown action")

        # -- send material by hand (someone who DM'd instead of commenting)
        def _manual(self, kind: str) -> None:
            cfg = live.get()
            body = self._body()
            reel = next((r for r in cfg.reels if r.key == str(body.get("reel", ""))), None)
            name = str(body.get("name", "")).strip()
            if kind == "preview":
                if reel is None:
                    raise _Refused(400, "pick one of your reels")
                subject, text = render_email(cfg, reel, name=name or "[their name]",
                                             keyword=reel.keyword)
                self._json(200, {"subject": subject, "body": text, "to_reel": reel.keyword})
                return
            errors = {}
            if reel is None:
                errors["reel"] = "pick one of your reels"
            if not name:
                errors["name"] = "add their name, it goes in the greeting"
            elif len(name) > MAX_NAME:
                errors["name"] = f"keep it under {MAX_NAME} characters"
            handle = str(body.get("handle", "")).strip().lstrip("@")
            if handle and not HANDLE_RE.fullmatch(handle):
                errors["handle"] = "a handle is letters, numbers, dots and underscores"
            raw = str(body.get("email", "")).strip()
            email = find_email(raw)
            if not email or any(c.isspace() for c in raw):
                errors["email"] = "that doesn't look like an email address"
            if errors:
                self._json(400, {"errors": errors})
                return
            db = Database(cfg.db_path)
            dup = None if body.get("force") else db.material_already_sent(email, reel.url, reel.keyword)
            if dup is not None:
                at = dup.emailed_at or dup.provided_at
                self._json(409, {"duplicate": {"email": dup.email, "state": dup.send_state,
                                               "at": at.isoformat() if at else None}})
                return
            eid = db.add_manual_email(name=name, handle=handle, email=email, reel_url=reel.url,
                                      keyword=reel.keyword, now=_now())
            log.info("dashboard: queued the %s material for %s (added by hand)", reel.keyword, email)
            self._json(200, {"id": eid})

        def _email_status(self, key: str) -> None:
            try:
                e = Database(live.get().db_path).email_by_id(int(key))
            except ValueError:
                e = None
            if e is None:
                raise _Refused(404, "no such email")
            self._json(200, {"id": e.id, "state": e.send_state,
                             "sent_at": e.emailed_at.isoformat() if e.emailed_at else None,
                             "error": e.last_error})

        # -- reels (config.toml)
        def _need_config(self) -> None:
            if live.path is None:
                raise _Refused(409, NO_CONFIG)

        def _local(self, dt) -> str | None:
            cfg = live.get()
            return dt.astimezone(cfg.tz).replace(tzinfo=None).isoformat(timespec="minutes") if dt else None

        def _reels_post(self, rest: list[str]) -> None:
            if rest == ["check"]:
                body = self._body()
                editing = body.get("editing") or None
                cfg = live.get()
                db = Database(cfg.db_path)
                r = check_reel(body, cfg, editing=editing,
                               has_leads=bool(editing) and has_leads(db, editing),
                               find_media=lookup, now=_now())
                self._json(200, {"errors": r.errors, "key": r.key, "posted_at": self._local(r.posted_at),
                                 "same_material": r.same_material})
            elif rest == ["new"]:
                self._save(None)
            elif len(rest) == 2 and rest[1] == "save":
                self._save(rest[0])
            elif len(rest) == 2 and rest[1] in ("pause", "resume"):
                self._pause(rest[0], rest[1] == "pause")
            else:
                raise _Refused(404, "not found")

        def _known(self, cfg: Config, key: str) -> None:
            if not any(r.key == key for r in cfg.reels):
                raise _Refused(404, "no such reel")

        def _save(self, editing: str | None) -> None:
            form = self._body()
            with CONFIG_LOCK:
                cfg = live.get()
                if editing is not None:
                    self._known(cfg, editing)
                db = Database(cfg.db_path)
                r = check_reel(form, cfg, editing=editing,
                               has_leads=editing is not None and has_leads(db, editing),
                               find_media=lookup, now=_now())
                if r.errors:
                    self._json(400, {"errors": r.errors})
                    return
                text = live.path.read_text(encoding="utf-8")
                if editing is None:
                    new = add_reel(text, r.fields)
                else:
                    new = update_reel(text, editing, {k: v for k, v in r.fields.items() if k != "key"})
                write_config(live.path, new)
                live.get()
            log.info("dashboard: reel %r %s", r.key, "added" if editing is None else "saved")
            self._json(200, {"ok": True, "key": r.key})

        def _pause(self, key: str, paused: bool) -> None:
            with CONFIG_LOCK:
                self._known(live.get(), key)
                text = live.path.read_text(encoding="utf-8")
                new = set_paused(text, key, paused)
                if new != text:
                    write_config(live.path, new)
                live.get()
            log.info("dashboard: reel %r %s", key, "paused" if paused else "resumed")
            self._json(200, {"ok": True})

    return Handler


def serve(cfg: Config, port: int = 8787, host: str = "127.0.0.1", *,
          config_path: Path | None = None,
          find_media: Callable[[str], Media | None] | None = None,
          extra_hosts: tuple[str, ...] = ()) -> ThreadingHTTPServer:
    """A server bound to localhost only. Call serve_forever() on it.

    With config_path the Reels tab can read and write that config.toml; find_media checks a
    new reel's link (None skips the check). extra_hosts are exact Host names (the Tailscale
    address) also allowed in, over https; the server itself still only listens on 127.0.0.1.
    """
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("the dashboard only runs on 127.0.0.1")
    Database(cfg.db_path)  # create / migrate the schema before the first request
    live = _Live(cfg, Path(config_path) if config_path else None)
    return ThreadingHTTPServer(("127.0.0.1", port), _handler(live, find_media, tuple(extra_hosts)))
