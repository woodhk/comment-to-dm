"""The engine: comment intake, paced DM 1, and event-driven reply handling."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from igdm.activity import HEARTBEAT_KEY
from igdm.config import Config, ReelConfig
from igdm.db import ENGAGED, LIVE, Database, Request, Status
from igdm.instagram import (
    Comment, Conversation, InstagramAPI, InstagramError, MaybeSent, Message, RateLimited,
    is_access_blocked,
)
from igdm.jev import Classifier, JevError
from igdm.mailer import Mailer
from igdm.messages import (
    base_of, find_email, first_name, pick_got_it, pick_public_reply, render, render_dm1,
    suspect_domain,
)
from igdm.milestones import sync_milestones
from igdm.pacing import (
    SEEN_PAUSE, dm_due_at, in_quiet_hours, is_expired, typing_seconds, uniform_delay,
    under_hourly_cap, window_open,
)

log = logging.getLogger(__name__)

DEFAULT_OFFER = "the material"  # {offer} for a reel without its own dm_offer
NEXT_DM1_KEY = "next_dm1_not_before"
IN_FLIGHT_KEY = "dm1_in_flight"  # "dm:<cid>" or "public:<cid>" while a send is in progress
RATE_LIMIT_BACKOFF = timedelta(minutes=15)
# Per reel. Just under the 60 s tick, so every full tick lists comments (and the quick
# checks between ticks never do).
COMMENT_POLL_INTERVAL = timedelta(seconds=50)
COMMENT_POLLED_PREFIX = "comments_polled:"  # + reel key -> ISO UTC of the last listing
COMMENT_BACKOFF_KEY = "comment_backoff_until"  # ISO UTC; no comment listing before this
UNEXPECTED_PAUSE = timedelta(hours=1)  # no DM 1 after a send that may have gone out unseen
UNANSWERED_DM1_BLOCK = timedelta(hours=24)  # an unanswered DM 1 younger than this blocks a new one
OWNER_HANDLING = frozenset({Status.NEEDS_OWNER, Status.HANDLED_BY_OWNER})
OWNER_HANDLING_NOTE = "you're already handling this person"
RECENT_PUBLIC_REPLIES = 10
IG_BLOCKED_KEY = "ig_access_blocked_since"  # ISO UTC while Meta blocks the app, "" when fine
IG_OK_KEY = "ig_ok_at"  # ISO UTC of the latest successful Instagram check (alerts read it)
PAUSED_AT_PREFIX = "reel_paused_at:"  # + reel key -> ISO UTC when the pause began, "" when live
PAUSED_NOTE = "came in while the reel was paused"


def note_access(db: Database, err: BaseException | None, now: datetime) -> None:
    """Remember when Meta started blocking the app (err) or that it works again (None)."""
    if err is None:
        db.kv_set(IG_OK_KEY, now.isoformat())
        if db.kv_get(IG_BLOCKED_KEY):
            db.kv_set(IG_BLOCKED_KEY, "")
            log.info("Instagram access works again")
    elif is_access_blocked(err) and not db.kv_get(IG_BLOCKED_KEY):
        db.kv_set(IG_BLOCKED_KEY, now.isoformat())
        log.error("Instagram access is BLOCKED by Meta; check Facebook for a verification prompt")


@dataclass
class Context:
    config: Config
    db: Database
    ig: InstagramAPI
    jev: Classifier
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    rng: random.Random
    own_id: str
    own_username: str
    media_ids: dict[str, str]  # reel key -> media id
    comment_poll_interval: timedelta = COMMENT_POLL_INTERVAL
    mailer: Mailer | None = None
    writer: Any = None  # igdm.llm.Writer, or None when AI-written replies are off
    # quick checks: conversation id -> the update time already read and dealt with
    quick_seen: dict[str, datetime] = field(default_factory=dict)


def _reel(ctx: Context, key: str) -> ReelConfig | None:
    return next((r for r in ctx.config.reels if r.key == key), None)


def material_id(url: str) -> str:
    """A material link without its options (?usp=sharing vs ?usp=drive_link) or trailing /."""
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}" if parts.netloc else ""


def same_material(a: ReelConfig, b: ReelConfig) -> bool:
    """True for the same reel, or two reels that give away the same material."""
    if a.key == b.key:
        return True
    ma = material_id(a.material_url)
    return bool(ma) and ma == material_id(b.material_url)


def is_paused(ctx: Context, key: str) -> bool:
    """True only when the reel is configured and paused (a missing reel is not "paused")."""
    r = _reel(ctx, key)
    return r is not None and r.paused


def _lower_kv_time(db: Database, key: str, to: datetime) -> None:
    raw = db.kv_get(key)
    if raw and datetime.fromisoformat(raw) > to:
        db.kv_set(key, to.isoformat())


def sync_pauses(ctx: Context) -> int:
    """Note when each reel's pause began; on resume, look back to it for replies and hand DMs.

    Driven only by config.toml, so pausing by hand works like the dashboard buttons.
    Returns how many reels changed state.
    """
    from igdm.handdm import HAND_SCAN_KEY
    db, now, changed = ctx.db, ctx.now(), 0
    for reel in ctx.config.reels:
        key = PAUSED_AT_PREFIX + reel.key
        since = db.kv_get(key)
        if reel.paused and not since:
            db.kv_set(key, now.isoformat())
            log.info("reel %r paused", reel.key)
            changed += 1
        elif not reel.paused and since:
            at = datetime.fromisoformat(since)
            _lower_kv_time(db, WATERMARK_KEY, at)
            _lower_kv_time(db, HAND_SCAN_KEY, at)
            db.kv_set(key, "")
            log.info("reel %r resumed (paused since %s)", reel.key, since)
            changed += 1
    return changed


# ---------------------------------------------------------------- intake

def _is_own(ctx: Context, c: Comment) -> bool:
    if c.username and c.username.casefold() == ctx.own_username.casefold():
        return True
    return c.from_id is not None and c.from_id == ctx.own_id


def _intake_one(ctx: Context, reel: ReelConfig, media_id: str, c: Comment) -> bool:
    """Classify and store one new comment. Returns False if it wasn't stored."""
    db = ctx.db
    req = Request(
        comment_id=c.id, reel_key=reel.key, media_id=media_id, username=c.username,
        commenter_id=c.from_id, text=c.text, commented_at=c.timestamp, status=Status.QUEUED,
    )
    if c.timestamp < reel.handle_comments_after:
        req.status = Status.PRE_CUTOFF
        db.insert_request(req)
        return True

    try:
        req.jev_yes = ctx.jev.comment_wants_material(c.text, reel)
    except JevError as e:
        log.warning("jev failed on comment %s (retry next tick): %s", c.id, e)
        db.log_event(c.id, "jev_error", str(e))
        return False

    now = ctx.now()
    p = ctx.config.pacing
    if req.jev_yes < ctx.config.comment_threshold:
        req.status = Status.LEFT_FOR_OWNER
    elif any(  # one offer per material: the same reel, or another reel with the same link
        o.comment_id != c.id
        and o.status not in (Status.LEFT_FOR_OWNER, Status.PAUSED_SKIPPED)
        and (other := _reel(ctx, o.reel_key)) is not None and same_material(reel, other)
        for o in db.person_requests(c.username)
    ):
        req.status = Status.DUPLICATE
    elif is_expired(c.timestamp, now, p.private_reply_max_age_hours):
        req.status = Status.EXPIRED
    else:
        req.status = Status.QUEUED
        req.dm_due_at = dm_due_at(c.timestamp, p.dm_delay_seconds, ctx.rng)
        req.kind = "repeat" if db.latest_email_for(c.username) else "normal"
    db.insert_request(req)
    log.info("comment %s by @%s -> %s", c.id, c.username, req.status)
    return True


def _kv_time(ctx: Context, key: str) -> datetime | None:
    raw = ctx.db.kv_get(key)
    return datetime.fromisoformat(raw) if raw else None


def ingest_comments(ctx: Context) -> int:
    """Store new comments on every configured reel. Returns the number of rows stored.

    Each reel's comments are listed at most every `comment_poll_interval` (the listing cost
    grows with the reel's comment count). A rate limit pauses all comment listing 15 min.
    """
    now = ctx.now()
    backoff = _kv_time(ctx, COMMENT_BACKOFF_KEY)
    if backoff and now < backoff:
        return 0
    stored = 0
    for reel in ctx.config.reels:
        media_id = ctx.media_ids.get(reel.key)
        if media_id is None:
            log.warning("reel %r has no media id; skipping intake", reel.key)
            continue
        polled_key = COMMENT_POLLED_PREFIX + reel.key
        last = _kv_time(ctx, polled_key)
        if last and now - last < ctx.comment_poll_interval:
            continue
        ctx.db.kv_set(polled_key, now.isoformat())
        try:
            fetched = ctx.ig.list_comments(media_id)
        except InstagramError as e:
            note_access(ctx.db, e, now)
            log.warning("listing comments for reel %r failed: %s", reel.key, e)
            ctx.db.log_event(None, "list_comments_failed", f"{reel.key}: {e}")
            if isinstance(e, RateLimited):
                until = now + RATE_LIMIT_BACKOFF
                ctx.db.kv_set(COMMENT_BACKOFF_KEY, until.isoformat())
                log.warning("rate limited listing comments; paused until %s", until.isoformat())
                break
            continue
        comments = sorted(fetched, key=lambda c: (c.timestamp, c.id))
        paused_at = None
        if reel.paused:
            raw = ctx.db.kv_get(PAUSED_AT_PREFIX + reel.key)
            paused_at = datetime.fromisoformat(raw) if raw else now
        for c in comments:
            if ctx.db.has_comment(c.id) or _is_own(ctx, c):
                continue
            if paused_at is not None:
                if c.timestamp >= paused_at:  # older ones stay unstored and are handled on resume
                    ctx.db.insert_request(Request(
                        comment_id=c.id, reel_key=reel.key, media_id=media_id, username=c.username,
                        commenter_id=c.from_id, text=c.text, commented_at=c.timestamp,
                        status=Status.PAUSED_SKIPPED, note=PAUSED_NOTE))
                continue
            if _intake_one(ctx, reel, media_id, c):
                stored += 1
    return stored


# ---------------------------------------------------------------- DM 1

def _recover_in_flight(ctx: Context) -> None:
    """A send that started but never finished bookkeeping may have gone out: don't retry it."""
    marker = ctx.db.kv_get(IN_FLIGHT_KEY)
    if not marker:
        return
    kind, sep, cid = marker.partition(":")
    if not sep:  # bare comment id: treat as a DM 1
        kind, cid = "dm", marker
    if ctx.db.has_comment(cid) and ctx.db.get(cid).status in (Status.QUEUED, Status.WAITING):
        if kind == "public":
            _maybe_sent(ctx, cid, Status.MANUAL_DM_NEEDED,
                        "public reply was interrupted; check whether it was posted")
        else:
            _maybe_sent(ctx, cid, Status.NEEDS_OWNER,
                        "DM 1 send was interrupted; check whether it went out before messaging")
        log.warning("comment %s: interrupted DM 1 send, handed to the owner", cid)
    ctx.db.kv_set(IN_FLIGHT_KEY, "")


def _sweep_expired(ctx: Context, now: datetime) -> None:
    max_age = ctx.config.pacing.private_reply_max_age_hours
    for r in ctx.db.with_status(Status.QUEUED, Status.WAITING):
        if is_expired(r.commented_at, now, max_age):
            ctx.db.update(r.comment_id, status=Status.EXPIRED)


def _blocked(ctx: Context, now: datetime) -> bool:
    p = ctx.config.pacing
    if in_quiet_hours(now, ctx.config.tz, p.quiet_start, p.quiet_end):
        return True
    if not under_hourly_cap(ctx.db.dm1_times_since(now - timedelta(hours=1)), now, p.max_dms_per_hour):
        return True
    nb = ctx.db.kv_get(NEXT_DM1_KEY)
    return bool(nb) and now < datetime.fromisoformat(nb)


def _next_row(ctx: Context, now: datetime) -> Request | None:
    db = ctx.db
    due = [r for r in db.with_status(Status.QUEUED) if r.dm_due_at is not None and r.dm_due_at <= now]
    rows = due + db.with_status(Status.WAITING)
    rows = [r for r in rows if _reel(ctx, r.reel_key) is not None and not is_paused(ctx, r.reel_key)]
    rows.sort(key=lambda r: (r.commented_at, r.comment_id))
    for r in rows:
        others = [o for o in db.person_requests(r.username) if o.comment_id != r.comment_id]
        if any(o.status in OWNER_HANDLING for o in others):
            db.update(r.comment_id, status=Status.NEEDS_OWNER, note=OWNER_HANDLING_NOTE)
            log.info("comment %s: the owner is already handling @%s", r.comment_id, r.username)
            continue
        if any(_blocks(o, now) for o in others):
            if r.status != Status.WAITING:
                db.update(r.comment_id, status=Status.WAITING)
            continue
        return r
    return None


def _unanswered_dm1(r: Request) -> bool:
    """DM 1 went out and they never answered (either flow's first DM)."""
    return r.status == Status.AWAITING_YES or (
        r.status == Status.AWAITING_EMAIL and r.last_user_msg_at is None and r.kind != "hand"
        and r.seen_until is None)  # seen_until: the owner handed it back, so it isn't unanswered


def _blocks(other: Request, now: datetime) -> bool:
    """Another row of the same person that holds back a new DM 1."""
    if _unanswered_dm1(other):
        # unanswered DM 1: blocks for 24 h (a missing send time blocks, to be safe)
        return other.dm1_sent_at is None or now - other.dm1_sent_at < UNANSWERED_DM1_BLOCK
    return other.status in ENGAGED


def _set_gap(ctx: Context, at: datetime) -> None:
    gap = uniform_delay(ctx.config.pacing.backlog_gap_seconds, ctx.rng)
    ctx.db.kv_set(NEXT_DM1_KEY, (at + gap).isoformat())


def _maybe_sent(ctx: Context, cid: str, status: Status, note: str) -> None:
    """A DM 1 / public-only send that may have gone out: hand it to the owner, count it, pause.

    It counts toward the hourly cap and the gap like a real send, and new DM 1s pause for
    an hour so a systematic failure can't send ~60 unseen messages an hour.
    """
    at = ctx.now()
    ctx.db.update(cid, status=status, note=_clip(note), dm1_sent_at=at)
    _set_gap(ctx, at)
    ctx.db.kv_set(NEXT_DM1_KEY, (at + UNEXPECTED_PAUSE).isoformat())
    ctx.db.log_event(cid, "dm1_paused", f"send may have gone out; DM 1 paused for 1 h: {note}")
    log.error("comment %s: send may have gone out (%s); DM 1 paused for 1 hour", cid, note)


def _unexpected(err: InstagramError) -> bool:
    """No usable answer from Instagram: the send may have gone out."""
    return isinstance(err, MaybeSent) or err.status == 0


def _rate_limited(ctx: Context, row: Request, now: datetime, err: RateLimited) -> bool:
    ctx.db.kv_set(NEXT_DM1_KEY, (now + RATE_LIMIT_BACKOFF).isoformat())
    ctx.db.log_event(row.comment_id, "rate_limited", str(err))
    log.warning("rate limited sending to comment %s; backing off 15 min", row.comment_id)
    return False


def _pick_public(ctx: Context) -> str:
    pool = ctx.config.messages.public_replies
    return pick_public_reply(pool, ctx.db.recent_public_replies(RECENT_PUBLIC_REPLIES), ctx.rng)


def _post_public_reply(ctx: Context, cid: str) -> None:
    """Public reply after DM 1. A failure here is only logged."""
    pool = ctx.config.messages.public_replies
    text = _pick_public(ctx)
    try:
        ctx.ig.reply_to_comment(cid, text)
    except MaybeSent as e:  # may have been posted: never retried, and counted for variety
        ctx.db.log_event(cid, "public_reply_maybe_sent", str(e))
        log.warning("public reply on %s may have been posted (no answer): %s", cid, e)
        ctx.db.record_public_reply(base_of(text, pool), ctx.now(), cid)
        return
    except InstagramError as e:
        if isinstance(e, RateLimited):
            ctx.db.kv_set(NEXT_DM1_KEY, (ctx.now() + RATE_LIMIT_BACKOFF).isoformat())
        ctx.db.log_event(cid, "public_reply_failed", str(e))
        log.warning("public reply on %s failed: %s", cid, e)
        return
    ctx.db.record_public_reply(base_of(text, pool), ctx.now(), cid)


def _public_only(ctx: Context, row: Request, now: datetime) -> bool:
    db, cid = ctx.db, row.comment_id
    pool = ctx.config.messages.public_replies
    text = _pick_public(ctx)
    db.kv_set(IN_FLIGHT_KEY, f"public:{cid}")
    try:
        ctx.ig.reply_to_comment(cid, text)
    except RateLimited as e:
        db.kv_set(IN_FLIGHT_KEY, "")
        return _rate_limited(ctx, row, now, e)
    except InstagramError as e:
        if _unexpected(e):
            _maybe_sent(ctx, cid, Status.MANUAL_DM_NEEDED,
                        f"public reply got no answer ({e}); check whether it was posted")
        else:
            db.update(cid, status=Status.MANUAL_DM_NEEDED, note=f"public reply failed: {e}")
        db.kv_set(IN_FLIGHT_KEY, "")
        return False
    except Exception as e:
        _maybe_sent(ctx, cid, Status.MANUAL_DM_NEEDED,
                    f"unexpected error posting public reply ({type(e).__name__}: {e}); "
                    "check whether it was posted")
        db.kv_set(IN_FLIGHT_KEY, "")
        raise
    sent_at = ctx.now()
    # dm1_sent_at doubles as the send time counted by the hourly cap (no DM went out)
    db.update(cid, status=Status.MANUAL_DM_NEEDED, dm1_sent_at=sent_at)
    db.kv_set(IN_FLIGHT_KEY, "")
    _set_gap(ctx, sent_at)
    db.record_public_reply(base_of(text, pool), sent_at, cid)
    return True


def _name(ctx: Context, row: Request) -> str:
    who = ctx.db.known_igsid(row.username) or row.commenter_id
    display = ctx.ig.get_display_name(who) if who else None
    return first_name(display) or row.username.lstrip("@")


def _send_dm1(ctx: Context, row: Request, reel: ReelConfig, now: datetime) -> bool:
    db, cid, m = ctx.db, row.comment_id, ctx.config.messages
    # Checked at send time, whatever `kind` says: an email may have been captured while this
    # row was WAITING on the person's other conversation.
    email = db.latest_email_for(row.username)
    template = m.repeat_dm1 if email else m.dm1
    name = _name(ctx, row) if "{name}" in template else ""  # skips a lookup when unused
    text = render_dm1(template, name=name, keyword=reel.keyword, offer=reel.dm_offer or DEFAULT_OFFER)

    db.kv_set(IN_FLIGHT_KEY, f"dm:{cid}")
    try:
        igsid, mid = ctx.ig.send_private_reply(ctx.own_id, cid, text)
    except RateLimited as e:
        db.kv_set(IN_FLIGHT_KEY, "")
        return _rate_limited(ctx, row, now, e)
    except InstagramError as e:
        if _unexpected(e):  # timeout / lost answer: it may have gone out
            _maybe_sent(ctx, cid, Status.NEEDS_OWNER,
                        f"DM 1 got no answer ({e}); check whether it went out before messaging")
        else:
            db.update(cid, status=Status.NEEDS_OWNER, note=str(e))
            db.log_event(cid, "dm1_failed", str(e))
            log.warning("private reply to %s refused: %s", cid, e)
        db.kv_set(IN_FLIGHT_KEY, "")
        return False
    except Exception as e:
        # The message may have gone out (e.g. a malformed success response): never retry it.
        _maybe_sent(ctx, cid, Status.NEEDS_OWNER,
                    f"unexpected error sending DM 1 ({type(e).__name__}: {e}); "
                    "check whether it went out")
        db.kv_set(IN_FLIGHT_KEY, "")
        raise

    # Record state first so this row can never be DMed again.
    sent_at = ctx.now()
    if email:
        status = Status.AWAITING_SAME_EMAIL
    else:  # email-first: DM 1 is already the first ask for the email
        status = Status.AWAITING_EMAIL if m.ask_email_first else Status.AWAITING_YES
    asked = {"asks": 1} if status == Status.AWAITING_EMAIL else {}
    db.update(cid, igsid=igsid, dm1_text=text, dm1_sent_at=sent_at, status=status,
              kind="repeat" if email else "normal", **asked)
    db.kv_set(IN_FLIGHT_KEY, "")
    _set_gap(ctx, sent_at)
    db.record_sent(mid, igsid, cid, text, sent_at)
    for o in db.person_requests(row.username):
        if o.comment_id != cid and _unanswered_dm1(o):
            db.update(o.comment_id, status=Status.SUPERSEDED)
    log.info("DM 1 sent for comment %s to @%s", cid, row.username)

    ctx.sleep(uniform_delay(ctx.config.pacing.public_reply_delay_seconds, ctx.rng).total_seconds())
    _post_public_reply(ctx, cid)
    return True


OWNER_PUBLIC_REPLY_NOTE = "you already replied to the comment publicly; no DM sent"


def _owner_replied_publicly(ctx: Context, cid: str) -> bool:
    """True if the owner already answered the comment by hand. A failed check counts as no."""
    try:
        replies = ctx.ig.list_comment_replies(cid)
    except Exception as e:  # noqa: BLE001 - the check is best effort
        ctx.db.log_event(cid, "reply_check_failed", str(e))
        log.warning("checking replies under comment %s failed (sending anyway): %s", cid, e)
        return False
    return any(_is_own(ctx, r) for r in replies)


def send_due_dm(ctx: Context) -> bool:
    """Send at most one DM 1 (or public-only reply). True if one went out."""
    now = ctx.now()
    _recover_in_flight(ctx)
    _sweep_expired(ctx, now)
    if _blocked(ctx, now):
        return False
    row = _next_row(ctx, now)
    if row is None:
        return False
    if _owner_replied_publicly(ctx, row.comment_id):
        ctx.db.update(row.comment_id, status=Status.HANDLED_BY_OWNER, note=OWNER_PUBLIC_REPLY_NOTE)
        log.info("comment %s: the owner already replied publicly; no DM", row.comment_id)
        return False
    if ctx.config.mode == "public_only":
        return _public_only(ctx, row, now)
    reel = _reel(ctx, row.reel_key)
    assert reel is not None  # filtered in _next_row
    return _send_dm1(ctx, row, reel, now)


# ---------------------------------------------------------------- replies

WATERMARK_KEY = "conv_watermark"
REPLY_IN_FLIGHT_KEY = "reply_in_flight"  # "f1:<cid>", "f2:<cid>" or "gotit:<cid>" mid-send
WATERMARK_OVERLAP = timedelta(minutes=5)
REPLY_BACKOFF_KEY = "reply_backoff_until"  # ISO UTC; no reply handling before this
REPLY_RATE_LIMIT_BACKOFF = timedelta(minutes=15)
FOLLOWUP_FAILURES_PREFIX = "followup_failures:"  # + comment id -> count of refused follow-ups
MAX_FOLLOWUP_FAILURES = 3
NOTE_MAX = 500
CLOCK_SKEW = timedelta(seconds=5)

ACTED, DEFERRED, SKIPPED = "acted", "deferred", "skipped"


class QuietHours(InstagramError):
    """Quiet hours began while a reply was on its way: nothing was sent, try again after."""

    def __init__(self):
        super().__init__(0, None, "quiet hours started")


class _QuickFailed(Exception):
    """A quick check couldn't list conversations: leave the rest to the next full tick."""


class _Stop(Exception):
    """Instagram rate-limited us: stop handling replies this tick."""

    def __init__(self, acted: bool):
        super().__init__("rate limited")
        self.acted = acted


def _clip(text: str) -> str:
    return text if len(text) <= NOTE_MAX else text[:NOTE_MAX] + "…"


def _recover_reply_in_flight(ctx: Context) -> None:
    """A follow-up or got-it send that never finished bookkeeping may have gone out: never resend."""
    db = ctx.db
    marker = db.kv_get(REPLY_IN_FLIGHT_KEY)
    if not marker:
        return
    kind, _, cid = marker.partition(":")
    if kind == "reply":  # "reply:<cid>:<asks before the send>"
        cid, _, before = cid.rpartition(":")
        if db.has_comment(cid):
            r = db.get(cid)
            if r.status in LIVE and r.asks <= int(before or 0):
                db.update(cid, status=Status.NEEDS_OWNER,
                          note="reply send was interrupted; check whether it went out")
                log.warning("comment %s: interrupted reply send, not retried", cid)
        db.kv_set(REPLY_IN_FLIGHT_KEY, "")
        return
    if db.has_comment(cid):
        r = db.get(cid)
        if kind == "f1" and r.status == Status.AWAITING_YES:
            db.update(cid, status=Status.NEEDS_OWNER,
                      note="follow-up 1 send was interrupted; check whether it went out")
        elif kind == "f2" and r.status == Status.FOLLOWUP1_SENT:
            db.update(cid, status=Status.NEEDS_OWNER,
                      note="follow-up 2 send was interrupted; check whether it went out")
        elif kind == "gotit" and r.status == Status.GOTIT_PENDING:
            db.update(cid, status=Status.EMAIL_CAPTURED,
                      note="got-it DM send was interrupted; check whether it went out")
        elif kind == "gotit" and r.status in LIVE:
            if any(e.comment_id == cid for e in db.emails()):
                db.update(cid, status=Status.EMAIL_CAPTURED,
                          note="got-it DM send was interrupted; check whether it went out")
            else:
                db.update(cid, status=Status.NEEDS_OWNER,
                          note="got-it DM send was interrupted before the email was saved")
        log.warning("comment %s: interrupted %s send, not retried", cid, kind)
    db.kv_set(REPLY_IN_FLIGHT_KEY, "")


def _sender_action(ctx: Context, igsid: str, action: str) -> None:
    """Seen / typing… are only for looks: a failure is logged and the reply still goes out."""
    try:
        ctx.ig.send_action(ctx.own_id, igsid, action)
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't send %s to %s: %s", action, igsid, e)


def _act_human(ctx: Context, igsid: str, text: str, typing: float | None) -> None:
    """Before a DM: mark their message Seen, pause, then show typing… for a while.

    `typing` given: this message follows one the bot just sent, so it is already Seen and
    typing… simply fills that gap.
    """
    if typing is None:
        _sender_action(ctx, igsid, "mark_seen")
        ctx.sleep(uniform_delay(SEEN_PAUSE, ctx.rng).total_seconds())
        typing = typing_seconds(text, ctx.rng)
    _sender_action(ctx, igsid, "typing_on")
    ctx.sleep(typing)


def _send_live(ctx: Context, req: Request, kind: str, text: str,
               done: dict, crashed: dict, marker: str | None = None,
               typing: float | None = None) -> InstagramError | None:
    """Send one DM in a live conversation, after Seen and typing… (see _act_human).

    On success the send is recorded and `done` applied in one transaction. An InstagramError
    is logged and returned (nothing changes, so the caller decides), except MaybeSent: the
    message may have gone out, so `crashed` is applied (never retried) and it is returned.
    Any other exception may also mean the message went out: `crashed` is applied and the
    exception re-raised.
    """
    db, cid = ctx.db, req.comment_id
    assert req.igsid is not None
    if _quiet(ctx, ctx.now()):
        return QuietHours()
    _act_human(ctx, req.igsid, text, typing)
    if _quiet(ctx, ctx.now()):  # Seen and typing… ran past the start of quiet hours
        return QuietHours()
    db.kv_set(REPLY_IN_FLIGHT_KEY, marker or f"{kind}:{cid}")
    try:
        mid = ctx.ig.send_message(ctx.own_id, req.igsid, text)
    except MaybeSent as e:
        db.update(cid, **crashed,
                  note=_clip(f"{kind} send got no answer ({e}); check whether it went out"))
        db.kv_set(REPLY_IN_FLIGHT_KEY, "")
        db.log_event(cid, "maybe_sent", f"{kind}: {e}")
        log.warning("comment %s: %s may have gone out (no answer); not retried", cid, kind)
        return e
    except InstagramError as e:
        db.kv_set(REPLY_IN_FLIGHT_KEY, "")
        db.log_event(cid, "got_it_failed" if kind == "gotit" else "followup_failed", f"{kind}: {e}")
        log.warning("comment %s: %s send failed: %s", cid, kind, e)
        return e
    except Exception as e:
        db.update(cid, **crashed,
                  note=f"unexpected error sending {kind} ({type(e).__name__}: {e}); "
                       "check whether it went out")
        db.kv_set(REPLY_IN_FLIGHT_KEY, "")
        raise
    db.record_sent_and_update(mid, req.igsid, cid, text, ctx.now(), **done)
    db.kv_set(REPLY_IN_FLIGHT_KEY, "")
    log.info("comment %s: %s sent to @%s", cid, kind, req.username)
    return None


def _followup_refused(ctx: Context, req: Request, err: InstagramError) -> bool:
    """Count a non-rate-limit follow-up failure. True if the cap was hit (row -> NEEDS_OWNER)."""
    if isinstance(err, QuietHours):  # nothing went wrong: it goes out after quiet hours
        return False
    db, key = ctx.db, FOLLOWUP_FAILURES_PREFIX + req.comment_id
    n = int(db.kv_get(key) or 0) + 1
    db.kv_set(key, str(n))
    if n < MAX_FOLLOWUP_FAILURES:
        return False
    db.update(req.comment_id, status=Status.NEEDS_OWNER,
              note=_clip(f"follow-up failed {n} times; last error: {err}"))
    log.warning("comment %s: follow-up failed %d times; handed to the owner", req.comment_id, n)
    return True


def _gap(ctx: Context) -> float:
    return uniform_delay(ctx.config.pacing.followup_gap_seconds, ctx.rng).total_seconds()


def _send_followup2(ctx: Context, req: Request, typing: float | None = None) -> bool:
    """FOLLOWUP1_SENT -> AWAITING_EMAIL. True if the row changed state.

    A refusal keeps FOLLOWUP1_SENT (retried by resume_followups) until the failure cap
    hands the row to the owner.
    """
    err = _send_live(ctx, req, "f2", ctx.config.messages.followup_2,
                     done={"status": Status.AWAITING_EMAIL, "asks": max(req.asks, 1)},
                     crashed={"status": Status.NEEDS_OWNER}, typing=typing)
    if isinstance(err, MaybeSent):
        return True  # handed to the owner
    if isinstance(err, RateLimited):
        raise _Stop(acted=False)
    if err is not None:
        return _followup_refused(ctx, req, err)
    return True


def _say_yes(ctx: Context, req: Request, newest: datetime) -> str:
    err = _send_live(ctx, req, "f1", ctx.config.messages.followup_1,
                     done={"status": Status.FOLLOWUP1_SENT, "last_user_msg_at": newest},
                     crashed={"status": Status.NEEDS_OWNER, "last_user_msg_at": newest})
    if isinstance(err, MaybeSent):
        return ACTED  # handed to the owner; follow-up 2 is not sent
    if isinstance(err, RateLimited):
        raise _Stop(acted=False)
    if err is not None:  # nothing went out: retried next tick, up to the cap
        return ACTED if _followup_refused(ctx, req, err) else DEFERRED
    try:  # typing… fills the gap; a failure is retried by resume_followups
        _send_followup2(ctx, ctx.db.get(req.comment_id), typing=_gap(ctx))
    except _Stop:
        raise _Stop(acted=True) from None
    return ACTED


def _capture(ctx: Context, req: Request, email: str, newest: datetime,
             their_message: str | None) -> str:
    """Save a typed address and send the got-it DM. `their_message` is what they typed; None means
    the address is one we already hold ("same email as before"), which is not checked again."""
    db, cid = ctx.db, req.comment_id
    fix = suspect_domain(email)
    if fix:
        db.flag(cid, "info", f"the address looks mistyped, maybe they meant {fix}",
                "check with them, then paste the right address on the dashboard", newest=newest)
        log.info("comment %s: email %s looks mistyped; flagged", cid, email)
        return ACTED
    if their_message is not None:
        try:
            real = ctx.jev.email_looks_real(email, their_message)
        except JevError as e:  # an outage never holds up a real lead
            db.log_event(cid, "jev_error", str(e))
            log.warning("jev failed checking the email for comment %s (carrying on): %s", cid, e)
        else:
            if real < ctx.config.email_threshold:
                db.flag(cid, "info", f"the address looks made up: {email}",
                        "check with them, then paste the real address on the dashboard",
                        newest=newest)
                log.info("comment %s: email %s looks made up; flagged", cid, email)
                return ACTED
    reel = _reel(ctx, req.reel_key)
    if reel is None:
        log.warning("comment %s: reel %r no longer configured; saving email anyway", cid, req.reel_key)
    ec = ctx.config.email
    auto = ec.enabled and reel is not None
    email_id = db.add_email(
        comment_id=cid, handle=req.username, igsid=req.igsid, email=email, provided_at=newest,
        reel_url=reel.url if reel else "", keyword=reel.keyword if reel else req.reel_key,
        name=_display_first_name(ctx, req.igsid),
        send_after=newest + timedelta(minutes=ec.delay_minutes) if auto else None,
    )
    log.info("comment %s: email captured for @%s", cid, req.username)
    captured = {"status": Status.EMAIL_CAPTURED, "last_user_msg_at": newest}
    if not window_open(newest, ctx.now()):
        db.update(cid, **captured, note="window closed, no got-it DM")
        return ACTED
    text = pick_got_it(ctx.config.messages.got_it, ctx.rng)
    err = _send_live(ctx, req, "gotit", text, done=captured, crashed=captured)
    if err is None and auto and ec.after_got_it_seconds:  # email follows the got-it DM
        db.set_send_after(email_id, ctx.now() + uniform_delay(ec.after_got_it_seconds, ctx.rng))
    if err is not None and not isinstance(err, MaybeSent):
        db.update(cid, **captured, note=_clip(f"got-it DM failed: {err}"))
        if isinstance(err, RateLimited):
            raise _Stop(acted=True)
    return ACTED


def _display_first_name(ctx: Context, igsid: str | None) -> str | None:
    if not igsid:
        return None
    try:
        return first_name(ctx.ig.get_display_name(igsid))
    except Exception:  # noqa: BLE001 - a name is nice to have, never worth failing a capture
        return None


def _to(ctx: Context, req: Request, status: Status, newest: datetime, note: str) -> str:
    ctx.db.update(req.comment_id, status=status, last_user_msg_at=newest, note=_clip(note))
    log.info("comment %s -> %s", req.comment_id, status)
    return ACTED


def _intent(ctx: Context, req: Request, text: str) -> dict[str, float] | None:
    try:
        return ctx.jev.reply_intent(req.dm1_text or "", text)
    except JevError as e:
        ctx.db.log_event(req.comment_id, "jev_error", str(e))
        log.warning("jev failed on reply for comment %s (retry next tick): %s", req.comment_id, e)
        return None


def _dispatch(ctx: Context, req: Request, text: str, newest: datetime, now: datetime,
              thread: list[dict[str, str]] | None = None) -> str:
    thr = ctx.config.reply_threshold
    email = find_email(text)
    ai = ctx.config.replies.enabled

    if req.status == Status.AWAITING_YES:
        if email:  # a late email is still saved; _capture skips the got-it DM
            return _capture(ctx, req, email, newest, text)
        if not window_open(newest, now):
            return _to(ctx, req, Status.WINDOW_EXPIRED, newest, f"reply: {text}")
        if ai:
            from igdm.replies import route
            return route(ctx, req, thread or [], text, newest, now)
        p = _intent(ctx, req, text)
        if p is None:
            return DEFERRED
        if p["yes"] >= thr:
            return _say_yes(ctx, req, newest)
        if p["no"] >= thr:
            return _to(ctx, req, Status.DECLINED, newest, f"reply: {text}")
        return _to(ctx, req, Status.NEEDS_OWNER, newest, f"reply: {text}")

    if req.status == Status.AWAITING_EMAIL:
        if email:
            return _capture(ctx, req, email, newest, text)
        if ai:
            if not window_open(newest, now):
                return _to(ctx, req, Status.WINDOW_EXPIRED, newest, f"reply: {text}")
            from igdm.replies import route
            return route(ctx, req, thread or [], text, newest, now)
        wait = timedelta(minutes=ctx.config.pacing.email_wait_minutes)
        if now - newest >= wait:
            return _to(ctx, req, Status.NEEDS_OWNER, newest, f"no email in reply: {text}")
        return DEFERRED  # last_user_msg_at stays put so these messages are seen again

    if req.status == Status.AWAITING_SAME_EMAIL:
        if email:
            return _capture(ctx, req, email, newest, text)
        p = _intent(ctx, req, text)
        if p is None:
            return DEFERRED
        old = ctx.db.latest_email_for(req.username) if p["yes"] >= thr else None
        if old:
            return _capture(ctx, req, old, newest, None)
        return _to(ctx, req, Status.NEEDS_OWNER, newest, f"reply: {text}")

    # FOLLOWUP1_SENT: resume_followups sends follow-up 2 first; look again next tick
    return DEFERRED


def _handle_conversation(ctx: Context, conv: Conversation) -> str:
    db = ctx.db
    others = [p for p in conv.participant_ids if p != ctx.own_id]
    if len(others) != 1:
        return SKIPPED
    igsid = others[0]
    req = db.live_request_for_igsid(igsid)
    if req is None or is_paused(ctx, req.reel_key):  # paused: looked at again after resume
        return SKIPPED
    # Instagram's clock may run a little behind the Mac's: look a few seconds before DM 1.
    # The bot's own DM 1 is still recognised by its message id / text.
    dm1_mark = req.dm1_sent_at - CLOCK_SKEW if req.dm1_sent_at else None
    marks = [t for t in (dm1_mark, req.last_user_msg_at, req.seen_until) if t is not None]
    if not marks:
        log.warning("comment %s: live row without dm1_sent_at; skipping", req.comment_id)
        return SKIPPED
    cutoff = max(marks)

    try:
        msgs = ctx.ig.list_messages(conv.id)
    except RateLimited as e:
        db.log_event(req.comment_id, "rate_limited", f"list_messages: {e}")
        log.warning("rate limited listing messages; stopping replies this tick")
        raise _Stop(acted=False) from None
    except InstagramError as e:
        db.log_event(req.comment_id, "list_messages_failed", str(e))
        log.warning("listing messages of %s failed: %s", conv.id, e)
        return DEFERRED

    new = sorted((m for m in msgs if m.created_at > cutoff), key=lambda m: m.created_at)
    for m in new:
        if (m.from_id == ctx.own_id and not db.is_bot_message(m.id)
                and not db.bot_sent_similar(igsid, m.text, m.created_at)):
            db.update(req.comment_id, status=Status.HANDLED_BY_OWNER,
                      note=_clip(f"you replied by hand: {m.text}"))
            log.info("comment %s: the owner replied by hand; backing off", req.comment_id)
            return ACTED

    incoming = [m for m in new if m.from_id != ctx.own_id]
    if not incoming:
        return SKIPPED
    newest = incoming[-1].created_at
    now = ctx.now()
    if now - newest < _settle(ctx, msgs, incoming[0]):
        return DEFERRED
    text = "\n".join(m.text for m in incoming)
    me = ctx.config.owner.name
    thread = [{"from": me if m.from_id == ctx.own_id else "them", "text": m.text}
              for m in sorted(msgs, key=lambda m: m.created_at)]
    if req.last_their_text != incoming[-1].text:
        db.update(req.comment_id, last_their_text=_clip(incoming[-1].text))
        req = db.get(req.comment_id)
    return _dispatch(ctx, req, text, newest, now, thread)


def _settle(ctx: Context, msgs: list[Message], first_new: Message) -> timedelta:
    """How long to wait after their latest message before answering: a few seconds when
    they're chatting (they wrote within chat_window_seconds of our last message), else the
    usual reply_settle_seconds in case they're still typing."""
    p = ctx.config.pacing
    ours = [m.created_at for m in msgs
            if m.from_id == ctx.own_id and m.created_at <= first_new.created_at]
    if ours and first_new.created_at - max(ours) <= timedelta(seconds=p.chat_window_seconds):
        return timedelta(seconds=p.chat_settle_seconds)
    return timedelta(seconds=p.reply_settle_seconds)


def _since(ctx: Context, now: datetime) -> datetime:
    raw = ctx.db.kv_get(WATERMARK_KEY)
    if raw:
        return datetime.fromisoformat(raw)
    sent = [r.dm1_sent_at for r in ctx.db.with_status(*LIVE) if r.dm1_sent_at is not None]
    return min(sent) if sent else now


def _quiet(ctx: Context, now: datetime) -> bool:
    """Quiet hours: the owner is asleep, so the bot sends no Instagram messages at all."""
    p = ctx.config.pacing
    return in_quiet_hours(now, ctx.config.tz, p.quiet_start, p.quiet_end)


def _backed_off(ctx: Context, now: datetime) -> bool:
    raw = ctx.db.kv_get(REPLY_BACKOFF_KEY)
    return bool(raw) and now < datetime.fromisoformat(raw)


def _start_backoff(ctx: Context) -> None:
    until = ctx.now() + REPLY_RATE_LIMIT_BACKOFF
    ctx.db.kv_set(REPLY_BACKOFF_KEY, until.isoformat())
    ctx.db.log_event(None, "reply_backoff", f"rate limited; replies paused until {until.isoformat()}")
    log.warning("rate limited; reply handling paused until %s", until.isoformat())


def process_replies(ctx: Context, only: dict[str, datetime] | None = None) -> int:
    """Act on settled replies in live conversations. Returns the number acted on.

    `only` (igsid -> the bot's latest DM to them, from chatting()) is a quick check between
    ticks: only those threads, only once something came in after the bot's message, and
    the watermark is left for the full tick.
    """
    db = ctx.db
    now = ctx.now()
    if _backed_off(ctx, now) or _quiet(ctx, now):
        return 0
    if only is not None and not only:
        return 0
    _recover_reply_in_flight(ctx)
    since = (min(only.values()) - CLOCK_SKEW if only is not None
             else _since(ctx, now) - WATERMARK_OVERLAP)
    try:
        convs = ctx.ig.list_conversations(ctx.own_id, since)
    except InstagramError as e:
        note_access(db, e, now)
        db.log_event(None, "list_conversations_failed", str(e))
        log.warning("listing conversations failed: %s", e)
        if isinstance(e, RateLimited):
            _start_backoff(ctx)
        if only is not None:
            raise _QuickFailed from e
        return 0
    note_access(db, None, now)
    if only is not None:  # chatting threads that changed since the last quick look
        convs = [c for c in convs if any(p in only for p in c.participant_ids)
                 and ctx.quick_seen.get(c.id) != c.updated_at]
    if not convs:
        return 0
    convs = sorted(convs, key=lambda c: (c.updated_at, c.id))

    acted = 0
    deferred: list[datetime] = []
    for i, conv in enumerate(convs):
        if i and _quiet(ctx, ctx.now()):  # quiet hours began part way through
            deferred.extend(c.updated_at for c in convs[i:])
            break
        try:
            outcome = _handle_conversation(ctx, conv)
        except _Stop as stop:
            acted += stop.acted
            _start_backoff(ctx)
            deferred.extend(c.updated_at for c in convs[i:])  # re-list everything not finished
            break
        if outcome == ACTED:
            acted += 1
        elif outcome == DEFERRED:
            deferred.append(conv.updated_at)
        if only is not None and outcome != DEFERRED:  # read again only once it changes
            if len(ctx.quick_seen) > 1000:
                ctx.quick_seen.clear()
            ctx.quick_seen[conv.id] = conv.updated_at

    if only is not None:
        return acted
    mark = max(c.updated_at for c in convs)
    if deferred:
        mark = min(mark, min(deferred) - timedelta(seconds=1))
    db.kv_set(WATERMARK_KEY, mark.isoformat())
    return acted


# ---------------------------------------------------------------- between ticks

MIN_WAKE = timedelta(seconds=1)
EMAIL_POLL = timedelta(seconds=5)  # emails queued on the dashboard go out within this long


def chatting(ctx: Context, now: datetime) -> dict[str, datetime]:
    """Live conversations the bot wrote to in the last chat_watch_seconds: igsid -> the bot's
    latest DM. Between ticks these are checked every chat_poll_seconds."""
    watch = timedelta(seconds=ctx.config.pacing.chat_watch_seconds)
    recent = ctx.db.latest_sends_since(now - watch)
    if not recent:
        return {}
    live = {r.igsid for r in ctx.db.with_status(*LIVE)
            if r.igsid and not is_paused(ctx, r.reel_key)}
    return {igsid: at for igsid, at in recent.items() if igsid in live}


def _dm1_wake(ctx: Context, now: datetime) -> datetime | None:
    """When the next first DM may go out, if one is waiting and nothing else holds it back."""
    p = ctx.config.pacing
    if in_quiet_hours(now, ctx.config.tz, p.quiet_start, p.quiet_end):
        return None
    if not under_hourly_cap(ctx.db.dm1_times_since(now - timedelta(hours=1)), now, p.max_dms_per_hour):
        return None
    due = [r.dm_due_at for r in ctx.db.with_status(Status.QUEUED) if r.dm_due_at is not None
           and _reel(ctx, r.reel_key) is not None and not is_paused(ctx, r.reel_key)]
    if not due:
        return None
    at = min(due)
    nb = ctx.db.kv_get(NEXT_DM1_KEY)
    return max(at, datetime.fromisoformat(nb)) if nb else at


def _quick(ctx: Context, name: str, fn: Callable[[], int]) -> int | str:
    try:
        return int(fn())
    except Exception as e:  # noqa: BLE001 - like tick(): log it, the bot carries on
        log.exception("quick %s check failed", name)
        try:
            ctx.db.log_event(None, "phase_failed", f"quick {name}: {type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            log.exception("couldn't log the failed quick check")
        return f"error: {type(e).__name__}: {e}"


def idle(ctx: Context, seconds: float, wait: Callable[[float], bool],
         before: Callable[[], object] | None = None) -> None:
    """The time between full ticks. Waits `seconds`, but wakes early to send a first DM the
    moment it's due and to check chatting people every chat_poll_seconds.

    `wait(s)` sleeps up to s seconds and returns True when the bot is stopping. `before()`
    runs before each quick check (the run loop reloads config.toml there, so a reel paused
    on the dashboard stops at once).
    """
    deadline = ctx.now() + timedelta(seconds=seconds)
    dm_tried = False  # a due DM that didn't go out waits for the next full tick
    replies_off = False  # a quick check failed: the next full tick tries again
    while True:
        now = ctx.now()
        if now >= deadline:
            return
        wake = deadline
        if not replies_off and not _quiet(ctx, now) and chatting(ctx, now):
            gap = max(MIN_WAKE, uniform_delay(ctx.config.pacing.chat_poll_seconds, ctx.rng))
            wake = min(wake, now + gap)
        dm_at = None if dm_tried else _dm1_wake(ctx, now)
        if dm_at is not None:
            wake = min(wake, max(dm_at, now + MIN_WAKE))
        if ctx.mailer is not None:  # a database read; any hour, like the email phase
            wake = min(wake, now + EMAIL_POLL)
        if wait(max(0.0, (wake - now).total_seconds())):
            return
        now = ctx.now()
        if now >= deadline:
            return
        if before is not None:
            try:
                before()
            except Exception:  # noqa: BLE001 - a reload problem never stops a check
                log.exception("reload before a quick check crashed")
        done: dict[str, int | str] = {}
        if ctx.mailer is not None and ctx.db.due_emails(ctx.now()):
            done["email"] = _quick(ctx, "email", lambda: _send_emails(ctx))
        if dm_at is not None and dm_at <= now:
            done["dm"] = _quick(ctx, "dm", lambda: send_due_dm(ctx))
            dm_tried = done["dm"] != 1
        hot = {} if replies_off else chatting(ctx, ctx.now())
        if hot:
            def replies() -> int:
                nonlocal replies_off
                try:
                    return process_replies(ctx, only=hot)
                except _QuickFailed:  # already logged; don't retry every few seconds
                    replies_off = True
                    return 0
            done["replies"] = _quick(ctx, "replies", replies)
        if any(v for v in done.values()):
            log.info("quick check: %s", done)


def resume_followups(ctx: Context) -> int:
    """Finish conversations left between follow-up 1 and 2. Returns the number acted on."""
    db = ctx.db
    if _backed_off(ctx, ctx.now()) or _quiet(ctx, ctx.now()):
        return 0
    _recover_reply_in_flight(ctx)
    n = 0
    for r in db.with_status(Status.FOLLOWUP1_SENT):
        if _quiet(ctx, ctx.now()):
            break
        if is_paused(ctx, r.reel_key):
            continue
        if r.igsid is None or r.last_user_msg_at is None:
            db.update(r.comment_id, status=Status.NEEDS_OWNER,
                      note="follow-up 1 sent but the conversation record is incomplete")
            n += 1
            continue
        if not window_open(r.last_user_msg_at, ctx.now()):
            db.update(r.comment_id, status=Status.WINDOW_EXPIRED,
                      note="24 h window closed before follow-up 2")
            n += 1
            continue
        try:
            if _send_followup2(ctx, r):
                n += 1
        except _Stop:
            _start_backoff(ctx)
            break
    return n


def _owner_spoke_since(ctx: Context, igsid: str, since: datetime) -> bool | None:
    """Did the owner type in this person's thread (by hand) after `since`? None if we can't tell."""
    db = ctx.db
    try:
        convs = ctx.ig.list_conversations(ctx.own_id, since - WATERMARK_OVERLAP)
        conv = next((c for c in convs if igsid in c.participant_ids), None)
        if conv is None:
            return None
        msgs = ctx.ig.list_messages(conv.id)
    except InstagramError as e:
        log.warning("couldn't check %s's thread before the got-it DM: %s", igsid, e)
        return None
    return any(m.from_id == ctx.own_id and m.created_at > since and not db.is_bot_message(m.id)
               and not db.bot_sent_similar(igsid, m.text, m.created_at) for m in msgs)


def send_pending_gotits(ctx: Context) -> int:
    """The owner pasted someone's email on the dashboard: send the usual got-it DM if we still can."""
    db = ctx.db
    rows = db.with_status(Status.GOTIT_PENDING)
    if not rows:
        return 0
    p = ctx.config.pacing
    now = ctx.now()
    if in_quiet_hours(now, ctx.config.tz, p.quiet_start, p.quiet_end) or _backed_off(ctx, now):
        return 0
    _recover_reply_in_flight(ctx)
    n = 0
    for r in db.with_status(Status.GOTIT_PENDING):
        if _quiet(ctx, ctx.now()):
            break
        captured = {"status": Status.EMAIL_CAPTURED}
        if r.igsid is None or r.last_user_msg_at is None or not window_open(r.last_user_msg_at, now):
            db.update(r.comment_id, **captured, note="email added by you; window closed, no DM")
            n += 1
            continue
        spoke = _owner_spoke_since(ctx, r.igsid, r.last_user_msg_at)
        if spoke is not False:  # True, or None when we couldn't check: stay quiet
            db.update(r.comment_id, **captured,
                      note="email added by you; no got-it DM (you're talking to them, "
                           "or the thread couldn't be checked)")
            n += 1
            continue
        text = pick_got_it(ctx.config.messages.got_it, ctx.rng)
        err = _send_live(ctx, r, "gotit", text, done=captured, crashed=captured)
        if isinstance(err, QuietHours):  # still pending: sent after quiet hours
            break
        if err is not None and not isinstance(err, MaybeSent):
            db.update(r.comment_id, **captured, note=_clip(f"got-it DM failed: {err}"))
            if isinstance(err, RateLimited):
                _start_backoff(ctx)
                break
        n += 1
    return n


# ---------------------------------------------------------------- tick

def _import_hand_dms(ctx: Context) -> int:
    from igdm.handdm import import_hand_dms
    return import_hand_dms(ctx)


def _send_emails(ctx: Context) -> int:
    from igdm.outbox import send_due_emails
    n = send_due_emails(ctx)
    send_sent_dms(ctx)
    return n


CONFIRM_MAX_AGE = timedelta(hours=1)  # a "just sent it" any later than this would read oddly


def send_sent_dms(ctx: Context) -> int:
    """Once the material has been emailed, DM them where it went (exactly once).

    Only for emails the bot sent from a conversation it knows, while the owner isn't talking to
    them himself. Returns how many went out.
    """
    db = ctx.db
    for r in db.rows("SELECT id FROM emails WHERE confirm_state='sending'"):
        db.set_confirm(r["id"], "unknown")  # interrupted mid-send: it may have gone, never resend
        log.warning("email %s: the sent DM was interrupted; not retried", r["id"])
    rows = db.emails_to_confirm()
    now = ctx.now()
    if not rows or _backed_off(ctx, now) or _quiet(ctx, now):
        return 0
    _recover_reply_in_flight(ctx)
    lines = ctx.config.messages.sent_dm
    n = 0
    for e in rows:
        def skip(why: str) -> None:
            db.set_confirm(e.id, "skipped")
            log.info("email %s: no sent DM (%s)", e.id, why)
        if not lines:
            skip("no sent_dm wording")
            continue
        if e.emailed_at is None or now - e.emailed_at > CONFIRM_MAX_AGE:
            skip("the email went out over an hour ago")
            continue
        if not db.has_comment(e.comment_id):
            skip("no conversation for it")
            continue
        req = db.get(e.comment_id)
        if is_paused(ctx, req.reel_key) or req.status == Status.GOTIT_PENDING:
            continue  # looked at again shortly (the hour limit still applies)
        if req.status != Status.EMAIL_CAPTURED:
            skip(f"the conversation is {req.status}")
            continue
        if not req.igsid or not req.last_user_msg_at or not window_open(req.last_user_msg_at, now):
            skip("Instagram's 24 h window is closed")
            continue
        spoke = _owner_spoke_since(ctx, req.igsid, req.last_user_msg_at)
        if spoke is None:
            continue  # couldn't check the thread: try again shortly
        if spoke:
            skip("the owner is talking to them")
            continue
        text = ctx.rng.choice(lines).format(email=e.email)
        db.set_confirm(e.id, "sending")
        err = _send_live(ctx, req, "sent", text, done={}, crashed={})
        if err is None:
            db.set_confirm(e.id, "sent")
            n += 1
        elif isinstance(err, MaybeSent):
            db.set_confirm(e.id, "unknown")
        elif isinstance(err, (QuietHours, RateLimited)):
            db.set_confirm(e.id, "pending")
            if isinstance(err, RateLimited):
                _start_backoff(ctx)
            break
        else:
            db.set_confirm(e.id, "failed")
    return n


def tick(ctx: Context) -> dict[str, int | str]:
    """Run one pass of every phase. A failing phase is logged and the others still run.

    process_replies runs before resume_followups so a manual reply from the owner is
    detected before follow-up 2 can go out.
    """
    result: dict[str, int | str] = {}
    start = ctx.now()
    phases = (
        ("pauses", sync_pauses),
        ("ingest", ingest_comments),
        ("dm", send_due_dm),
        ("hand_dms", _import_hand_dms),
        ("replies", process_replies),
        ("followups", resume_followups),
        ("gotits", send_pending_gotits),
        ("email", _send_emails),
        ("milestones", sync_milestones),
    )
    for name, fn in phases:
        try:
            result[name] = int(fn(ctx))
        except Exception as e:  # noqa: BLE001 - isolate phases from each other
            log.exception("phase %s failed", name)
            result[name] = f"error: {type(e).__name__}: {e}"
            try:
                ctx.db.log_event(None, "phase_failed", f"{name}: {type(e).__name__}: {e}")
            except Exception:  # noqa: BLE001 - the database itself may be what failed
                log.exception("couldn't log the failed phase")
    try:
        ctx.db.kv_set(HEARTBEAT_KEY, json.dumps({"start": start.isoformat(),
                                                 "end": ctx.now().isoformat()}))
    except Exception:  # noqa: BLE001
        log.exception("couldn't record the heartbeat")
    return result
