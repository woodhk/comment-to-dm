"""Tell the owner when the bot needs them: a silent phone notification (ntfy) and an email.

check_alerts runs after every tick. It reads the events logged since the last check, decides
per kind of problem whether it has started or cleared, and sends one message for each change.
One person's bad address or a single timeout never alerts; a real outage always does.
State lives in the kv table, so a restart mid-outage never repeats an alert.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Protocol

import httpx

from igdm.activity import _plain
from igdm.db import Database, _enc
from igdm.engine import IG_BLOCKED_KEY, IG_OK_KEY
from igdm.mailer import Mailer

log = logging.getLogger("igdm.alerts")

STATE_KEY = "alerts_state"
CURSOR_KEY = "alerts_cursor"
STREAK = 3  # this many failures in a row (no success between) is a real problem
LASTING = timedelta(minutes=15)  # ... or failures of one kind going on this long
RATE_WINDOW = timedelta(minutes=60)  # rate limited STREAK times within this is a problem
FLAP = timedelta(minutes=60)  # a kind that keeps coming back alerts at most this often
FIRST_LOOK = timedelta(minutes=15)  # the very first check ignores older events

TITLES = {
    "meta_blocked": "Instagram blocked the bot",
    "gmail_down": "Gmail isn't sending",
    "email": "Emails are failing",
    "dm": "DMs are failing",
    "llm": "AI replies are failing",
    "jev": "The request checker (Jev or OpenAI) can't be reached",
    "rate_limited": "Instagram keeps slowing the bot down",
    "phase_failed": "The bot can't do its checks",
    "ig_down": "The bot can't reach Instagram",
}
FIXED_TITLES = {
    "meta_blocked": "Instagram unblocked the bot",
    "gmail_down": "Gmail is sending again",
    "email": "Emails are going out again",
    "dm": "DMs are going out again",
    "llm": "AI replies are working again",
    "jev": "The request checker is back",
    "rate_limited": "Instagram stopped slowing the bot down",
    "phase_failed": "The bot's checks are working again",
    "ig_down": "The bot can reach Instagram again",
}
FIXED_BODY = "It's working again."
MORE = "Open the dashboard to see more."
META_HELP = "Log in to Facebook and finish the check it asks for."
IMMEDIATE = {"gmail_down"}  # one failure is enough: it's the whole system, not one person

# What counts as "it worked" for each kind: (table, time column, extra condition).
_SUCCESS = {
    "gmail_down": ("emails", "emailed_at", ""),
    "email": ("emails", "emailed_at", ""),
    "dm": ("sent_messages", "sent_at", ""),
    "rate_limited": ("sent_messages", "sent_at", ""),
    # Writer rows are purpose 'reply'; classifier rows (Jev or the OpenAI fallback) are comment,
    # intent, route or email. Older Jev rows logged their intent calls as 'reply'.
    "llm": ("ai_usage", "at", " AND purpose='reply' AND provider!='typesafe'"),
    "jev": ("ai_usage", "at",
            " AND (provider='typesafe' OR purpose IN ('comment','intent','route','email'))"),
}


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> None: ...


class NtfyNotifier:
    """A silent push (priority 2: on the lock screen, no sound) to the owner's private ntfy topic."""

    def __init__(self, topic: str, server: str = "https://ntfy.sh",
                 client: httpx.Client | None = None):
        self._url = f"{server.rstrip('/')}/{topic}"
        self._client = client or httpx.Client(timeout=10)

    def notify(self, title: str, body: str) -> None:
        fixed = title in FIXED_TITLES.values()
        r = self._client.post(self._url, content=body.encode(), headers={
            "Title": title, "Priority": "2", "Tags": "white_check_mark" if fixed else "warning"})
        r.raise_for_status()


class EmailNotifier:
    def __init__(self, mailer: Mailer, to: str):
        self._mailer, self._to = mailer, to

    def notify(self, title: str, body: str) -> None:
        self._mailer.send(self._to, title, body)


class FanOut:
    """Every channel gets its turn; one failing (Gmail is often the broken thing) never stops
    the others."""

    def __init__(self, *notifiers: Notifier):
        self.notifiers = notifiers

    def notify(self, title: str, body: str) -> None:
        for n in self.notifiers:
            try:
                n.notify(title, body)
            except Exception as e:  # noqa: BLE001
                log.warning("alert %r not sent via %s: %s: %s", title, type(n).__name__,
                            type(e).__name__, e)


def _kind(event: str, detail: str) -> str | None:
    if "code=190" in detail:
        return "meta_blocked"
    if event == "email_failed":  # a refused login is the whole system; a blip is just one try
        return "gmail_down" if "gmail login failed" in detail else "email"
    return {"got_it_failed": "dm", "followup_failed": "dm", "dm1_failed": "dm",
            "list_comments_failed": "ig_down", "list_conversations_failed": "ig_down",
            "list_messages_failed": "ig_down", "llm_error": "llm",
            "jev_error": "jev", "rate_limited": "rate_limited",
            "phase_failed": "phase_failed"}.get(event)


def _fresh() -> dict:
    return {"open": False, "silent": False, "streak": 0, "first_fail": None, "last_fail": None,
            "last_alert": None, "detail": "", "recent": []}


def _succeeded(db: Database, kind: str, after: str | None, before: str | None = None) -> bool:
    """Did this kind of thing work after `after` (and before `before`)? Times are ISO UTC."""
    if after is None:
        return False
    table, col, extra = _SUCCESS[kind]
    upper = f" AND {col} < ?" if before is not None else ""
    args = (after, before) if before is not None else (after,)
    return bool(db.rows(f"SELECT 1 FROM {table} WHERE {col} > ?{upper}{extra} LIMIT 1", args))


class _Run:
    def __init__(self, db: Database, now: datetime, notifier: Notifier, state: dict,
                 since: str, startup: bool = False):
        self.db, self.now, self.notifier, self.state, self.since = db, now, notifier, state, since
        self.startup = startup  # no tick ran: silence proves nothing, so nothing clears
        self.sent: list[str] = []

    def st(self, kind: str) -> dict:
        return self.state.setdefault(kind, _fresh())

    def _say(self, title: str, body: str) -> None:
        self.sent.append(title)
        try:
            self.notifier.notify(title, body)
        except Exception as e:  # noqa: BLE001 - alerts never stop the bot
            log.warning("alert %r failed: %s: %s", title, type(e).__name__, e)

    def open(self, kind: str, body: str) -> None:
        st = self.st(kind)
        st["open"] = True
        last = st["last_alert"]
        if last and self.now - datetime.fromisoformat(last) < FLAP:
            st["silent"] = True  # it came straight back: the owner already knows
            return
        st["silent"] = False
        st["last_alert"] = self.now.isoformat()
        self._say(TITLES[kind], body)

    def overdue(self) -> None:
        """A problem that came straight back was kept quiet; if it's still there an hour after
        the last alert, say so after all."""
        for kind in TITLES:
            st = self.st(kind)
            if st["open"] and st["silent"] and \
                    self.now - datetime.fromisoformat(st["last_alert"]) >= FLAP:
                st["silent"] = False
                st["last_alert"] = self.now.isoformat()
                body = f"{META_HELP}\n{MORE}" if kind == "meta_blocked" and \
                    self.db.kv_get(IG_BLOCKED_KEY) else _body(st["detail"])
                self._say(TITLES[kind], body)

    def close(self, kind: str) -> None:
        if self.startup:
            return
        st = self.st(kind)
        silent = st["silent"]
        self.state[kind] = _fresh() | {"last_alert": st["last_alert"]}
        if not silent:
            self._say(FIXED_TITLES[kind], FIXED_BODY)


def _body(detail: str) -> str:
    return f"{_plain(detail)}\n{MORE}"


def _meta(run: _Run, fails: list[tuple[str, str]]) -> None:
    st = run.st("meta_blocked")
    blocked = bool(run.db.kv_get(IG_BLOCKED_KEY))
    if (blocked or fails) and not st["open"]:
        run.open("meta_blocked", f"{META_HELP}\n{MORE}" if blocked else _body(fails[-1][1]))
    elif not blocked and not fails and st["open"]:
        run.close("meta_blocked")  # (a no-op at startup)


def _phase(run: _Run, fails: list[tuple[str, str]]) -> None:
    st = run.st("phase_failed")
    if run.st("meta_blocked")["open"] or run.startup:
        return  # Meta blocked the app: that alert covers it. Startup: no tick ran.
    if not fails:
        if st["open"]:
            run.close("phase_failed")
        else:
            st["streak"] = 0
        return
    st["streak"] += 1  # one per check: a failing tick, however many phases failed in it
    st["detail"] = fails[-1][1]
    if st["streak"] >= STREAK and not st["open"]:
        run.open("phase_failed", _body(st["detail"]))


def _ig(run: _Run, fails: list[tuple[str, str]]) -> None:
    """Instagram unreachable: checks keep failing and nothing at all has worked since. One reel
    failing while the rest works never counts, because something worked in that window."""
    st = run.st("ig_down")
    ok = run.db.kv_get(IG_OK_KEY) or ""
    worked = ok != st.get("ok_seen", "")
    st["ok_seen"] = ok
    if run.st("meta_blocked")["open"] or run.startup:
        return
    if st["open"]:
        if worked:
            run.close("ig_down")
        elif fails:
            st["detail"] = fails[-1][1]
        return
    if worked or not fails:
        st["streak"] = 0
        return
    st["streak"] += 1
    st["detail"] = fails[-1][1]
    if st["streak"] >= STREAK:
        run.open("ig_down", _body(st["detail"]))


def _counted(run: _Run, kind: str, fails: list[tuple[str, str]]) -> None:
    st = run.st(kind)
    for at, detail in fails:
        if not st["open"] and _succeeded(run.db, kind, st["last_fail"] or run.since, at):
            st.update(streak=0, first_fail=None, recent=[])  # it worked in between
        st["streak"] += 1
        st["first_fail"] = st["first_fail"] or at
        st["last_fail"], st["detail"] = at, detail
        st["recent"] = [t for t in st["recent"] + [at]
                        if datetime.fromisoformat(at) - datetime.fromisoformat(t) <= RATE_WINDOW]
    if st["last_fail"] and _succeeded(run.db, kind, st["last_fail"]):
        if st["open"]:
            run.close(kind)
        else:
            st.update(streak=0, first_fail=None, recent=[])
        return
    if st["open"] or not fails:
        return
    if kind == "rate_limited":
        problem = len(st["recent"]) >= STREAK
    elif kind in IMMEDIATE:
        problem = True
    else:
        lasting = (datetime.fromisoformat(st["last_fail"])
                   - datetime.fromisoformat(st["first_fail"])) >= LASTING
        problem = st["streak"] >= STREAK or lasting
    if problem:
        run.open(kind, _body(st["detail"]))


def check_alerts(db: Database, now: datetime, notifier: Notifier, *,
                 startup: bool = False) -> list[str]:
    """Send any alert or all-clear that's due. Returns the titles sent. Never raises.

    startup=True: called after a failed start, when no tick ran, so problems can open but
    nothing is counted as fixed."""
    try:
        return _check(db, now, notifier, startup)
    except Exception:  # noqa: BLE001 - alerts never stop the bot
        log.exception("checking alerts crashed")
        return []


def _check(db: Database, now: datetime, notifier: Notifier, startup: bool) -> list[str]:
    try:
        state = json.loads(db.kv_get(STATE_KEY) or "{}")
        if not isinstance(state, dict):
            raise ValueError("not an object")
    except ValueError:
        log.warning("alert state was unreadable; starting fresh")
        state = {}
    raw_cursor = db.kv_get(CURSOR_KEY)
    since = state.get("_last_check") or _enc(now - FIRST_LOOK)
    if raw_cursor is None:  # first check ever: only recent events, not the whole history
        rows = db.rows("SELECT id, at, event, detail FROM events WHERE at >= ? ORDER BY id",
                       (_enc(now - FIRST_LOOK),))
    else:
        rows = db.rows("SELECT id, at, event, detail FROM events WHERE id > ? ORDER BY id",
                       (int(raw_cursor),))
    fails: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        kind = _kind(r["event"], r["detail"] or "")
        if kind:
            fails.setdefault(kind, []).append((r["at"], r["detail"] or ""))
    run = _Run(db, now, notifier, state, since, startup)
    _meta(run, fails.get("meta_blocked", []))
    _phase(run, fails.get("phase_failed", []))
    _ig(run, fails.get("ig_down", []))
    for kind in ("gmail_down", "email", "dm", "llm", "jev", "rate_limited"):
        _counted(run, kind, fails.get(kind, []))
    run.overdue()
    state["_last_check"] = _enc(now)
    last_id = rows[-1]["id"] if rows else (int(raw_cursor) if raw_cursor else
                                           (db.rows("SELECT MAX(id) m FROM events")[0]["m"] or 0))
    db.kv_set(STATE_KEY, json.dumps(state))
    db.kv_set(CURSOR_KEY, str(last_id))
    return run.sent
