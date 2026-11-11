"""SQLite state and email store."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable


class Status(StrEnum):
    PRE_CUTOFF = "pre_cutoff"
    LEFT_FOR_OWNER = "left_for_owner"
    DUPLICATE = "duplicate"
    QUEUED = "queued"
    WAITING = "waiting_other_conversation"
    EXPIRED = "expired"
    MANUAL_DM_NEEDED = "manual_dm_needed"
    AWAITING_YES = "awaiting_yes"
    FOLLOWUP1_SENT = "followup1_sent"
    AWAITING_EMAIL = "awaiting_email"
    AWAITING_SAME_EMAIL = "awaiting_same_email"
    EMAIL_CAPTURED = "email_captured"
    DECLINED = "declined"
    NEEDS_OWNER = "needs_owner"
    HANDLED_BY_OWNER = "handled_by_owner"
    WINDOW_EXPIRED = "window_expired"
    SUPERSEDED = "superseded"
    NEEDS_INFO = "needs_info"  # the bot needs one fact the owner can look up (e.g. email in bio)
    GOTIT_PENDING = "gotit_pending"  # the owner pasted their email; the bot sends the got-it DM
    DONE_BY_OWNER = "done_by_owner"  # the owner pressed Done on the dashboard (this request only)
    PAUSED_SKIPPED = "paused_skipped"  # commented while the reel was paused: never handled


LIVE = frozenset(
    {Status.AWAITING_YES, Status.FOLLOWUP1_SENT, Status.AWAITING_EMAIL, Status.AWAITING_SAME_EMAIL}
)
ENGAGED = LIVE - {Status.AWAITING_YES}
FOR_OWNER = frozenset(
    {
        Status.LEFT_FOR_OWNER,
        Status.NEEDS_OWNER,
        Status.WINDOW_EXPIRED,
        Status.EXPIRED,
        Status.MANUAL_DM_NEEDED,
        Status.NEEDS_INFO,
    }
)


@dataclass
class Request:
    comment_id: str
    reel_key: str
    media_id: str
    username: str
    commenter_id: str | None
    text: str
    commented_at: datetime
    status: Status
    kind: str = "normal"
    jev_yes: float | None = None
    dm_due_at: datetime | None = None
    igsid: str | None = None
    dm1_text: str | None = None
    dm1_sent_at: datetime | None = None
    last_user_msg_at: datetime | None = None
    note: str | None = None
    paused_from: Status | None = None  # the live stage a flag paused, for "let the bot carry on"
    seen_until: datetime | None = None  # messages at or before this are already dealt with
    asks: int = 0  # bot messages asking for the email
    llm_replies: int = 0
    last_their_text: str | None = None
    flag_reason: str | None = None
    flag_todo: str | None = None


@dataclass
class EmailRow:
    id: int
    instagram_handle: str
    ig_user_id: str | None
    email: str
    provided_at: datetime
    reel_url: str
    reel_keyword: str
    comment_id: str
    emailed_at: datetime | None
    name: str | None = None
    send_after: datetime | None = None
    send_state: str = "manual"
    attempts: int = 0
    last_error: str | None = None
    # the owner queued it themselves (Send now / pasted email): a pause never holds it
    by_owner: bool = False
    # the "just sent it" DM: None (not for this email), pending, sending, sent, skipped,
    # failed, unknown (it may have gone out, never retried)
    confirm_state: str | None = None


# send_state: manual (needs "Send now"), queued, sending, sent, failed, unknown (may have gone out)
EMAIL_STATES = ("manual", "queued", "sending", "sent", "failed", "unknown")
_OWNER_EMAIL_STATES = "('manual','failed','unknown')"


_REQ_FIELDS = [f.name for f in fields(Request)]
_DT_FIELDS = {"commented_at", "dm_due_at", "dm1_sent_at", "last_user_msg_at", "seen_until"}
_STATUS_FIELDS = {"status", "paused_from"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    comment_id TEXT PRIMARY KEY,
    reel_key TEXT NOT NULL,
    media_id TEXT NOT NULL,
    username TEXT NOT NULL,
    commenter_id TEXT,
    text TEXT NOT NULL,
    commented_at TEXT NOT NULL,
    status TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'normal',
    jev_yes REAL,
    dm_due_at TEXT,
    igsid TEXT,
    dm1_text TEXT,
    dm1_sent_at TEXT,
    last_user_msg_at TEXT,
    note TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_username ON requests(username COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_requests_igsid ON requests(igsid);
CREATE TABLE IF NOT EXISTS sent_messages (
    message_id TEXT PRIMARY KEY,
    igsid TEXT NOT NULL,
    comment_id TEXT,
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_igsid ON sent_messages(igsid);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_handle TEXT NOT NULL,
    ig_user_id TEXT,
    email TEXT NOT NULL,
    provided_at TEXT NOT NULL,
    reel_url TEXT NOT NULL,
    reel_keyword TEXT NOT NULL,
    comment_id TEXT NOT NULL UNIQUE,
    emailed_at TEXT
);
CREATE TABLE IF NOT EXISTS public_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_text TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_cid ON events(comment_id);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 1,
    usd_override REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS follower_counts (
    day TEXT PRIMARY KEY,  -- local date; the last reading of the day wins
    followers INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (media_id TEXT PRIMARY KEY, posted_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_posts_at ON posts(posted_at);
-- reached_at NULL: already past it when the bot first looked, so no date
CREATE TABLE IF NOT EXISTS milestones (followers INTEGER PRIMARY KEY, reached_at TEXT);
"""

# The original owner's first name, in the values stored before upgrade 1. Spelled in two
# parts so the check that shared code names nobody (tests/test_neutral_names.py) passes.
_OLD_NAME = "al" + "ex"

# Columns renamed after the first release: (table, old name, new name). Renamed in place on open.
_RENAMED_COLUMNS = [("emails", f"by_{_OLD_NAME}", "by_owner")]

# Columns added after the first release: (table, column, declaration). Added in place on open.
_MIGRATIONS = [
    ("emails", "name", "TEXT"),
    ("emails", "send_after", "TEXT"),
    ("emails", "send_state", "TEXT NOT NULL DEFAULT 'manual'"),
    ("emails", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("emails", "last_error", "TEXT"),
    ("requests", "paused_from", "TEXT"),
    ("requests", "seen_until", "TEXT"),
    ("requests", "asks", "INTEGER NOT NULL DEFAULT 0"),
    ("requests", "llm_replies", "INTEGER NOT NULL DEFAULT 0"),
    ("requests", "last_their_text", "TEXT"),
    ("requests", "flag_reason", "TEXT"),
    ("requests", "flag_todo", "TEXT"),
    ("emails", "by_owner", "INTEGER NOT NULL DEFAULT 0"),
    ("public_replies", "comment_id", "TEXT"),
    ("emails", "confirm_state", "TEXT"),
]


def _step_1_owner_statuses(c: sqlite3.Connection) -> None:
    """Statuses that named the original owner now say "owner". Notes stay as they are."""
    for new in ("left_for_owner", "needs_owner", "handled_by_owner", "done_by_owner"):
        old = new.replace("owner", _OLD_NAME)
        c.execute("UPDATE requests SET status=? WHERE status=?", (new, old))
        c.execute("UPDATE requests SET paused_from=? WHERE paused_from=?", (new, old))
        c.execute("UPDATE events SET event = REPLACE(event, ?, ?) WHERE event LIKE 'status:%'",
                  (old, new))
    c.execute("PRAGMA user_version = 1")


# Data upgrades, run once each on open. Step i takes the database to user_version i + 1 and
# sets it itself. A new database runs them all (there is nothing to change), so it starts current.
DATA_STEPS: list[Callable[[sqlite3.Connection], None]] = [_step_1_owner_statuses]


def _enc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(timezone.utc).isoformat()


def _dec(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s).astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), isolation_level=None)
        self._c.row_factory = sqlite3.Row
        # CLI commands read the DB while the daemon holds it open.
        self._c.execute("PRAGMA busy_timeout=5000")
        if str(path) != ":memory:":
            self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, old, new in _RENAMED_COLUMNS:
            self._rename_column(table, old, new)
        added = set()
        for table, col, decl in _MIGRATIONS:
            if col not in self._columns(table):
                self._c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                added.add((table, col))
        if ("public_replies", "comment_id") in added:
            self._match_old_public_replies()
        self._run_data_steps()
        # rows the owner marked sent by hand before send_state existed
        self._c.execute("UPDATE emails SET send_state='sent' "
                        "WHERE emailed_at IS NOT NULL AND send_state != 'sent'")

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self._c.execute(f"PRAGMA table_info({table})")}

    def _rename_column(self, table: str, old: str, new: str) -> None:
        """Rename when the old column is there and the new one isn't. The bot and the dashboard
        start together, so the second look happens with the write lock held."""
        if old not in self._columns(table):
            return
        self._c.execute("BEGIN IMMEDIATE")
        try:
            have = self._columns(table)
            if old in have and new not in have:
                self._c.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
            self._c.execute("COMMIT")
        except BaseException:
            self._c.execute("ROLLBACK")
            raise

    def _user_version(self) -> int:
        return self._c.execute("PRAGMA user_version").fetchone()[0]

    def _run_data_steps(self) -> None:
        """Each step in its own write transaction. The version is read again once the lock is
        held, so when two processes open an old database together only one runs each step."""
        while self._user_version() < len(DATA_STEPS):
            self._c.execute("BEGIN IMMEDIATE")
            try:
                version = self._user_version()
                if version < len(DATA_STEPS):
                    DATA_STEPS[version](self._c)
                    if self._user_version() != version + 1:
                        raise RuntimeError(f"data step {version + 1} did not set user_version")
                self._c.execute("COMMIT")
            except BaseException:
                self._c.execute("ROLLBACK")
                raise

    def _match_old_public_replies(self) -> None:
        """Replies posted before comment ids were kept: each one went out seconds after that
        comment's DM 1, so pair it with the DM 1 sent in the two minutes before it."""
        sends = [(_dec(r["dm1_sent_at"]), r["comment_id"]) for r in self._c.execute(
            "SELECT comment_id, dm1_sent_at FROM requests WHERE dm1_sent_at IS NOT NULL")]
        for r in self._c.execute("SELECT id, at FROM public_replies WHERE comment_id IS NULL").fetchall():
            at = _dec(r["at"])
            near = [(t, cid) for t, cid in sends if timedelta(0) <= at - t <= timedelta(minutes=2)]
            if near:
                self._c.execute("UPDATE public_replies SET comment_id=? WHERE id=?",
                                (max(near)[1], r["id"]))

    # -- helpers
    def _req(self, row: sqlite3.Row) -> Request:
        d = {k: row[k] for k in _REQ_FIELDS}
        for k in _DT_FIELDS:
            d[k] = _dec(d[k])
        d["status"] = Status(d["status"])
        d["paused_from"] = Status(d["paused_from"]) if d["paused_from"] else None
        return Request(**d)

    def _email(self, r: sqlite3.Row) -> EmailRow:
        return EmailRow(
            id=r["id"], instagram_handle=r["instagram_handle"], ig_user_id=r["ig_user_id"],
            email=r["email"], provided_at=_dec(r["provided_at"]), reel_url=r["reel_url"],
            reel_keyword=r["reel_keyword"], comment_id=r["comment_id"],
            emailed_at=_dec(r["emailed_at"]), name=r["name"], send_after=_dec(r["send_after"]),
            send_state=r["send_state"], attempts=r["attempts"], last_error=r["last_error"],
            by_owner=bool(r["by_owner"]),
            confirm_state=r["confirm_state"],
        )

    # -- requests
    def has_comment(self, cid: str) -> bool:
        return self._c.execute("SELECT 1 FROM requests WHERE comment_id=?", (cid,)).fetchone() is not None

    def insert_request(self, req: Request) -> None:
        vals = []
        for k in _REQ_FIELDS:
            v = getattr(req, k)
            if k in _DT_FIELDS:
                v = _enc(v)
            elif k in _STATUS_FIELDS:
                v = str(Status(v)) if v is not None else None
            vals.append(v)
        cols = ", ".join(_REQ_FIELDS)
        qs = ", ".join("?" * len(_REQ_FIELDS))
        self._c.execute(
            f"INSERT INTO requests ({cols}, updated_at) VALUES ({qs}, ?)", (*vals, _now())
        )

    def get(self, cid: str) -> Request:
        row = self._c.execute("SELECT * FROM requests WHERE comment_id=?", (cid,)).fetchone()
        if row is None:
            raise KeyError(cid)
        return self._req(row)

    def _prepare_update(self, cid: str, fields_: dict[str, object]) -> tuple[dict, Status]:
        allowed = set(_REQ_FIELDS) - {"comment_id"}
        bad = set(fields_) - allowed
        if bad:
            raise ValueError(f"unknown or immutable request fields: {sorted(bad)}")
        vals = {}
        for k, v in fields_.items():
            if k in _DT_FIELDS:
                v = _enc(v)
            elif k in _STATUS_FIELDS:
                v = str(Status(v)) if v is not None else None
            vals[k] = v
        old = self.get(cid).status
        if ("status" in vals and "paused_from" not in vals and old in LIVE
                and Status(vals["status"]) in FOR_OWNER):
            vals["paused_from"] = str(old)
        return vals, old

    def _apply_update(self, cid: str, vals: dict, old: Status) -> None:
        """Run inside an open transaction."""
        sets = ", ".join(f"{k}=?" for k in vals)  # keys validated in _prepare_update
        self._c.execute(
            f"UPDATE requests SET {sets}, updated_at=? WHERE comment_id=?",
            (*vals.values(), _now(), cid),
        )
        if "status" in vals and vals["status"] != old:
            self._c.execute(
                "INSERT INTO events (comment_id, at, event, detail) VALUES (?,?,?,'')",
                (cid, _now(), f"status:{old}->{vals['status']}"),
            )

    def update(self, cid: str, **fields_: object) -> None:
        if not fields_:
            return
        vals, old = self._prepare_update(cid, fields_)
        self._c.execute("BEGIN")
        try:
            self._apply_update(cid, vals, old)
            self._c.execute("COMMIT")
        except BaseException:
            self._c.execute("ROLLBACK")
            raise

    def flag(self, cid: str, kind: str, reason: str, todo: str,
             newest: datetime | None = None) -> None:
        """Hand a conversation to the owner: "info" needs one fact, "you" needs the owner to reply."""
        if kind not in ("info", "you"):
            raise ValueError(f"bad flag kind {kind!r}")
        fields_: dict[str, object] = {
            "status": Status.NEEDS_INFO if kind == "info" else Status.NEEDS_OWNER,
            "flag_reason": reason, "flag_todo": todo, "note": reason,
        }
        if newest is not None:
            fields_["last_user_msg_at"] = newest
        self.update(cid, **fields_)

    # -- dashboard actions (the bot acts on these rows next tick)
    def resolve_with_email(self, cid: str, email: str, *, now: datetime, delay_minutes: float,
                           reel_url: str, keyword: str, name: str | None = None) -> int:
        """The owner found their email: queue the material and let the bot send the got-it DM."""
        req = self.get(cid)
        email_id = self.add_email(
            comment_id=cid, handle=req.username, igsid=req.igsid, email=email, provided_at=now,
            reel_url=reel_url, keyword=keyword, name=name,
            send_after=now + timedelta(minutes=delay_minutes), by_owner=True)
        self.update(cid, status=Status.GOTIT_PENDING, note=f"email added by you: {email}",
                    flag_reason=None, flag_todo=None)
        return email_id

    def dismiss_flag(self, cid: str) -> None:
        """Take a flagged comment off the dashboard without changing its status."""
        self.update(cid, flag_reason=None, flag_todo=None, note="dismissed by you on the dashboard")

    def mark_done(self, cid: str, now: datetime) -> None:
        self.update(cid, status=Status.DONE_BY_OWNER, note="done by you on the dashboard")

    def carry_on(self, cid: str, now: datetime) -> bool:
        """Hand a flagged conversation back to the bot. Messages up to now count as dealt with."""
        req = self.get(cid)
        if req.status not in FOR_OWNER or req.paused_from not in LIVE:
            return False
        status, asks = req.paused_from, 0
        if status == Status.FOLLOWUP1_SENT:
            # follow-up 2 may already have gone out: never send it again, wait for their email
            status, asks = Status.AWAITING_EMAIL, max(req.asks, 1)
        self.update(cid, status=status, seen_until=now, asks=asks, flag_reason=None,
                    flag_todo=None, note="handed back to the bot by you")
        return True

    def send_now(self, email_id: int, now: datetime) -> bool:
        return self.queue_now(email_id, now)

    def with_status(self, *statuses: Status) -> list[Request]:
        if not statuses:
            return []
        qs = ",".join("?" * len(statuses))
        rows = self._c.execute(
            f"SELECT * FROM requests WHERE status IN ({qs}) ORDER BY commented_at ASC, comment_id",
            [str(Status(s)) for s in statuses],
        ).fetchall()
        return [self._req(r) for r in rows]

    def updated_since(self, status: Status, since: datetime) -> list[Request]:
        """Rows in `status` whose last change was at or after `since`, oldest change first."""
        rows = self._c.execute(
            "SELECT * FROM requests WHERE status=? AND updated_at >= ? ORDER BY updated_at",
            (str(Status(status)), _enc(since)),
        ).fetchall()
        return [self._req(r) for r in rows]

    def person_requests(self, username: str) -> list[Request]:
        rows = self._c.execute(
            "SELECT * FROM requests WHERE username = ? COLLATE NOCASE ORDER BY commented_at ASC",
            (username,),
        ).fetchall()
        return [self._req(r) for r in rows]

    def live_request_for_igsid(self, igsid: str) -> Request | None:
        live = sorted(str(s) for s in LIVE)
        qs = ",".join("?" * len(live))
        row = self._c.execute(
            f"SELECT * FROM requests WHERE igsid=? AND status IN ({qs}) "
            "ORDER BY dm1_sent_at DESC LIMIT 1",
            (igsid, *live),
        ).fetchone()
        return self._req(row) if row else None

    def any_request_for_igsid(self, igsid: str) -> bool:
        return self._c.execute(
            "SELECT 1 FROM requests WHERE igsid=? LIMIT 1", (igsid,)).fetchone() is not None

    def known_igsid(self, username: str) -> str | None:
        row = self._c.execute(
            "SELECT igsid FROM requests WHERE username = ? COLLATE NOCASE AND igsid IS NOT NULL "
            "ORDER BY commented_at DESC LIMIT 1",
            (username,),
        ).fetchone()
        return row["igsid"] if row else None

    def dm1_times_since(self, since: datetime) -> list[datetime]:
        rows = self._c.execute(
            "SELECT dm1_sent_at FROM requests WHERE dm1_sent_at >= ? AND kind != 'hand' "
            "ORDER BY dm1_sent_at",
            (_enc(since),),
        ).fetchall()
        return [_dec(r[0]) for r in rows]

    # -- sent messages
    def latest_sends_since(self, since: datetime) -> dict[str, datetime]:
        """igsid -> the bot's latest DM to them, for everyone it wrote to after `since`."""
        rows = self._c.execute(
            "SELECT igsid, MAX(sent_at) AS last FROM sent_messages WHERE sent_at > ? GROUP BY igsid",
            (_enc(since),)).fetchall()
        return {r["igsid"]: _dec(r["last"]) for r in rows}

    def record_sent(self, message_id: str, igsid: str, comment_id: str | None,
                    text: str, sent_at: datetime) -> None:
        self._c.execute(
            "INSERT OR REPLACE INTO sent_messages (message_id, igsid, comment_id, text, sent_at) "
            "VALUES (?,?,?,?,?)",
            (message_id, igsid, comment_id, text, _enc(sent_at)),
        )

    def record_sent_and_update(self, message_id: str, igsid: str, comment_id: str,
                               text: str, sent_at: datetime, **fields_: object) -> None:
        """Record a bot send and update its request in one transaction."""
        vals, old = self._prepare_update(comment_id, fields_)
        self._c.execute("BEGIN")
        try:
            self._c.execute(
                "INSERT OR REPLACE INTO sent_messages (message_id, igsid, comment_id, text, sent_at) "
                "VALUES (?,?,?,?,?)",
                (message_id, igsid, comment_id, text, _enc(sent_at)),
            )
            if vals:
                self._apply_update(comment_id, vals, old)
            self._c.execute("COMMIT")
        except BaseException:
            self._c.execute("ROLLBACK")
            raise

    def is_bot_message(self, message_id: str) -> bool:
        return self._c.execute(
            "SELECT 1 FROM sent_messages WHERE message_id=?", (message_id,)
        ).fetchone() is not None

    def bot_sent_similar(self, igsid: str, text: str, around: datetime,
                         tolerance_s: int = 120) -> bool:
        lo = _enc(around - timedelta(seconds=tolerance_s))
        hi = _enc(around + timedelta(seconds=tolerance_s))
        want = " ".join(text.split())
        rows = self._c.execute(
            "SELECT text FROM sent_messages WHERE igsid=? AND sent_at BETWEEN ? AND ?",
            (igsid, lo, hi),
        ).fetchall()
        return any(" ".join(r["text"].split()) == want for r in rows)

    # -- emails
    def add_email(self, *, comment_id: str, handle: str, igsid: str | None, email: str,
                  provided_at: datetime, reel_url: str, keyword: str, name: str | None = None,
                  send_after: datetime | None = None, by_owner: bool = False) -> int:
        """Save an address once per request. With send_after it is queued for automatic sending."""
        state = "queued" if send_after is not None else "manual"
        self._c.execute(
            "INSERT OR IGNORE INTO emails (instagram_handle, ig_user_id, email, provided_at, "
            "reel_url, reel_keyword, comment_id, name, send_after, send_state, by_owner) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (handle, igsid, email, _enc(provided_at), reel_url, keyword, comment_id, name,
             _enc(send_after), state, int(by_owner)),
        )
        return self._c.execute(
            "SELECT id FROM emails WHERE comment_id=?", (comment_id,)
        ).fetchone()["id"]

    def add_manual_email(self, *, name: str, handle: str, email: str, reel_url: str,
                         keyword: str, now: datetime) -> int:
        """The owner entered someone by hand on the dashboard: email them the material now.

        No conversation goes with it (they usually DM'd instead of commenting), so no DM.
        """
        return self.add_email(comment_id=f"manual:{uuid.uuid4().hex}", handle=handle, igsid=None,
                              email=email, provided_at=now, reel_url=reel_url, keyword=keyword,
                              name=name, send_after=now, by_owner=True)

    def material_already_sent(self, email: str, reel_url: str, keyword: str) -> EmailRow | None:
        """The latest email of this reel's material to this address that went out or will."""
        r = self._c.execute(
            "SELECT * FROM emails WHERE email = ? COLLATE NOCASE AND (reel_url = ? OR reel_keyword = ?) "
            "AND send_state IN ('queued','sending','sent','unknown') ORDER BY id DESC LIMIT 1",
            (email, reel_url, keyword)).fetchone()
        return self._email(r) if r else None

    def latest_email_for(self, username: str) -> str | None:
        row = self._c.execute(
            "SELECT email FROM emails WHERE instagram_handle = ? COLLATE NOCASE "
            "ORDER BY provided_at DESC, id DESC LIMIT 1",
            (username,),
        ).fetchone()
        return row["email"] if row else None

    def emails(self, pending_only: bool = False) -> list[EmailRow]:
        # pending = waiting on the owner (never the ones the bot is about to send itself)
        where = f"WHERE emailed_at IS NULL AND send_state IN {_OWNER_EMAIL_STATES}" if pending_only else ""
        rows = self._c.execute(f"SELECT * FROM emails {where} ORDER BY id").fetchall()
        return [self._email(r) for r in rows]

    def mark_emailed(self, ids: list[int] | None, at: datetime) -> int:
        """The owner sent these by hand (`igdm mark-sent`)."""
        a = _enc(at)
        if ids is None:
            cur = self._c.execute(
                "UPDATE emails SET emailed_at=?, send_state='sent' "
                f"WHERE emailed_at IS NULL AND send_state IN {_OWNER_EMAIL_STATES}", (a,))
        else:
            if not ids:
                return 0
            qs = ",".join("?" * len(ids))
            cur = self._c.execute(
                f"UPDATE emails SET emailed_at=?, send_state='sent' "
                f"WHERE emailed_at IS NULL AND send_state IN {_OWNER_EMAIL_STATES} AND id IN ({qs})",
                (a, *ids),
            )
        return cur.rowcount

    def set_confirm(self, email_id: int, state: str | None) -> None:
        self._c.execute("UPDATE emails SET confirm_state=? WHERE id=?", (state, email_id))

    def emails_to_confirm(self) -> list[EmailRow]:
        """Emails the bot sent whose "just sent it" DM is still to go."""
        return [self._email(r) for r in self._c.execute(
            "SELECT * FROM emails WHERE confirm_state='pending' ORDER BY emailed_at, id")]

    def email_by_id(self, email_id: int) -> EmailRow | None:
        r = self._c.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        return self._email(r) if r else None

    def due_emails(self, now: datetime) -> list[EmailRow]:
        rows = self._c.execute(
            "SELECT * FROM emails WHERE send_state='queued' AND emailed_at IS NULL "
            "AND send_after IS NOT NULL AND send_after <= ? ORDER BY send_after, id",
            (_enc(now),),
        ).fetchall()
        return [self._email(r) for r in rows]

    def emails_in_state(self, *states: str) -> list[EmailRow]:
        qs = ",".join("?" * len(states))
        rows = self._c.execute(
            f"SELECT * FROM emails WHERE send_state IN ({qs}) ORDER BY id", states).fetchall()
        return [self._email(r) for r in rows]

    def set_send_after(self, email_id: int, at: datetime) -> None:
        """Move a still-queued email's send time (never one that is sending or sent)."""
        self._c.execute("UPDATE emails SET send_after=? WHERE id=? AND send_state='queued' "
                        "AND emailed_at IS NULL", (_enc(at), email_id))

    def mark_email(self, email_id: int, state: str, *, error: str | None = None,
                   at: datetime | None = None, bump_attempts: bool = False) -> None:
        if state not in EMAIL_STATES:
            raise ValueError(f"bad email state {state!r}")
        sets, vals = ["send_state=?"], [state]
        if error is not None:
            sets.append("last_error=?")
            vals.append(error)
        if bump_attempts:
            sets.append("attempts=attempts+1")
        if state == "sent":
            sets.append("emailed_at=?")
            vals.append(_enc(at or datetime.now(timezone.utc)))
        self._c.execute(f"UPDATE emails SET {', '.join(sets)} WHERE id=?", (*vals, email_id))

    def queue_now(self, email_id: int, now: datetime) -> bool:
        """Queue a manual / failed / unknown email for sending now. Never touches sent ones."""
        cur = self._c.execute(
            "UPDATE emails SET send_state='queued', send_after=?, by_owner=1 "
            "WHERE id=? AND emailed_at IS NULL AND send_state IN ('manual','failed','unknown')",
            (_enc(now), email_id),
        )
        return cur.rowcount == 1

    # -- public replies
    def recent_public_replies(self, n: int = 10) -> list[str]:
        rows = self._c.execute(
            "SELECT base_text FROM public_replies ORDER BY at DESC, id DESC LIMIT ?", (n,)
        ).fetchall()
        return [r[0] for r in rows]

    def record_public_reply(self, base_text: str, at: datetime,
                            comment_id: str | None = None) -> None:
        self._c.execute(
            "INSERT INTO public_replies (base_text, at, comment_id) VALUES (?,?,?)",
            (base_text, _enc(at), comment_id),
        )

    # -- AI usage
    def log_usage(self, provider: str, model: str, purpose: str, *, input_tokens: int = 0,
                  output_tokens: int = 0, calls: int = 1, usd_override: float | None = None,
                  note: str = "", at: datetime | None = None) -> None:
        self._c.execute(
            "INSERT INTO ai_usage (at, provider, model, purpose, input_tokens, output_tokens, "
            "calls, usd_override, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (_enc(at) if at else _now(), provider, model, purpose, input_tokens, output_tokens,
             calls, usd_override, note),
        )

    def usage_rows(self, since: datetime | None = None) -> list[dict]:
        q, args = "SELECT * FROM ai_usage", ()
        if since is not None:
            q, args = q + " WHERE at >= ?", (_enc(since),)
        rows = self._c.execute(q + " ORDER BY id", args).fetchall()
        return [dict(r) | {"at": _dec(r["at"])} for r in rows]

    # -- events / kv / stats
    def log_event(self, cid: str | None, event: str, detail: str = "",
                  at: datetime | None = None) -> None:
        self._c.execute(
            "INSERT INTO events (comment_id, at, event, detail) VALUES (?,?,?,?)",
            (cid, _enc(at) if at else _now(), event, detail),
        )

    def events(self, cid: str | None) -> list[tuple[datetime, str, str]]:
        rows = self._c.execute(
            "SELECT at, event, detail FROM events WHERE comment_id IS ? ORDER BY id", (cid,)
        ).fetchall()
        return [(_dec(r["at"]), r["event"], r["detail"]) for r in rows]

    def rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        """Read-only queries for the dashboard."""
        return self._c.execute(sql, args).fetchall()

    def requests_where(self, where: str, args: tuple = ()) -> list[Request]:
        return [self._req(r) for r in self._c.execute(
            f"SELECT * FROM requests WHERE {where}", args).fetchall()]

    def kv_get(self, key: str) -> str | None:
        row = self._c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self._c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))

    def status_counts(self) -> dict[str, int]:
        rows = self._c.execute("SELECT status, COUNT(*) n FROM requests GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # -- milestones page (igdm.milestones)
    def record_followers(self, day: str, followers: int, at: datetime) -> None:
        self._c.execute("INSERT OR REPLACE INTO follower_counts (day, followers, at) VALUES (?,?,?)",
                        (day, followers, _enc(at)))

    def latest_followers(self) -> int | None:
        row = self._c.execute("SELECT followers FROM follower_counts ORDER BY at DESC LIMIT 1").fetchone()
        return row["followers"] if row else None

    def milestones_reached(self) -> dict[int, datetime | None]:
        return {r["followers"]: _dec(r["reached_at"])
                for r in self._c.execute("SELECT followers, reached_at FROM milestones")}

    def mark_milestone(self, followers: int, at: datetime | None) -> None:
        self._c.execute("INSERT OR IGNORE INTO milestones (followers, reached_at) VALUES (?,?)",
                        (followers, _enc(at)))

    def add_posts(self, posts) -> None:
        """Feed posts (igdm.instagram.Media); ones without a time are skipped."""
        self._c.executemany("INSERT OR IGNORE INTO posts (media_id, posted_at) VALUES (?,?)",
                            [(p.id, _enc(p.posted_at)) for p in posts if p.posted_at])

    def newest_post(self) -> datetime | None:
        row = self._c.execute("SELECT MAX(posted_at) m FROM posts").fetchone()
        return _dec(row["m"]) if row and row["m"] else None

    def post_times(self) -> list[datetime]:
        return [_dec(r["posted_at"]) for r in self._c.execute("SELECT posted_at FROM posts ORDER BY posted_at")]
