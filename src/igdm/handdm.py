"""Pick up DM threads the owner started by hand, so the AI can carry them on."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from igdm.db import Request, Status
from igdm.instagram import InstagramError, RateLimited
from igdm.messages import find_email

log = logging.getLogger(__name__)

HAND_SCAN_KEY = "hand_scan_watermark"  # ISO UTC: conversations up to here have been looked at
HAND_SCAN_AT_KEY = "hand_scan_at"  # ISO UTC of the last scan
HAND_SCAN_INTERVAL = timedelta(minutes=10)
HAND_FIRST_LOOKBACK = timedelta(days=7)
HAND_MAX_THREADS_PER_TICK = 20
ALREADY_TALKING = "you're already talking to them, so the bot left this thread alone"
BACKLOG_EMAIL = ("their email was already in the thread when the bot picked it up; check you "
                 "haven't sent it, then press Send now")


def _kv_time(db, key: str) -> datetime | None:
    raw = db.kv_get(key)
    return datetime.fromisoformat(raw) if raw else None


def _hand_messages(ctx, msgs, reels):
    for reel in reels:
        phrases = [p.casefold() for p in reel.hand_dm_phrases]
        hand = [m for m in msgs if m.from_id == ctx.own_id
                and any(p in m.text.casefold() for p in phrases)]
        if hand:
            return reel, hand
    return None, []


def _lower_reply_watermark(ctx, before: datetime) -> None:
    from igdm.engine import WATERMARK_KEY
    mark = before - timedelta(seconds=1)
    current = _kv_time(ctx.db, WATERMARK_KEY)
    if current is None or mark < current:
        ctx.db.kv_set(WATERMARK_KEY, mark.isoformat())


def _import(ctx, igsid: str, msgs, reels, known_username: str | None = None) -> bool:
    reel, hand = _hand_messages(ctx, msgs, reels)
    if reel is None:
        return False
    first = hand[0]
    own = [m for m in msgs if m.from_id == ctx.own_id]
    theirs_after = [m for m in msgs if m.from_id != ctx.own_id and m.created_at > first.created_at]
    newest = msgs[-1]
    unanswered = [m for m in msgs if m.from_id != ctx.own_id and m.created_at > own[-1].created_at]
    backlog_email = next((e for m in unanswered if (e := find_email(m.text))), None)
    if backlog_email:
        status, note = Status.EMAIL_CAPTURED, BACKLOG_EMAIL
    elif newest.from_id != ctx.own_id or not theirs_after:
        status, note = Status.AWAITING_YES, None
    else:
        status, note = Status.HANDLED_BY_OWNER, ALREADY_TALKING
    username = known_username
    if not username:
        try:
            username = ctx.ig.get_username(igsid) or igsid
        except Exception:  # noqa: BLE001 - the handle is only for display
            username = igsid
    ctx.db.insert_request(Request(
        comment_id=f"hand:{reel.key}:{igsid}", reel_key=reel.key,
        media_id=ctx.media_ids.get(reel.key, ""), username=username, commenter_id=None,
        text=first.text[:500], commented_at=first.created_at, status=status, kind="hand",
        igsid=igsid, dm1_text=first.text, dm1_sent_at=first.created_at, note=note,
        seen_until=own[-1].created_at,
    ))
    if backlog_email:  # sent from a "Send now" card, never automatically (go-live rule)
        ctx.db.add_email(comment_id=f"hand:{reel.key}:{igsid}", handle=username, igsid=igsid,
                         email=backlog_email, provided_at=unanswered[-1].created_at,
                         reel_url=reel.url, keyword=reel.keyword)
    elif newest.from_id != ctx.own_id:
        _lower_reply_watermark(ctx, newest.created_at)
    log.info("picked up a hand-DM thread with @%s (%s) -> %s", username, reel.key, status)
    return True


def import_hand_dms(ctx) -> int:
    """Look through recent DM threads for ones the owner started by hand. Returns rows added."""
    if not ctx.config.replies.enabled:
        return 0
    reels = [r for r in ctx.config.reels if r.hand_dm_phrases and not r.paused]
    if not reels:
        return 0
    db, now = ctx.db, ctx.now()
    last = _kv_time(db, HAND_SCAN_AT_KEY)
    if last and now - last < HAND_SCAN_INTERVAL:
        return 0
    db.kv_set(HAND_SCAN_AT_KEY, now.isoformat())
    mark = _kv_time(db, HAND_SCAN_KEY)
    try:
        convs = ctx.ig.list_conversations(ctx.own_id, mark or now - HAND_FIRST_LOOKBACK)
    except InstagramError as e:
        log.warning("hand-DM scan: listing conversations failed: %s", e)
        return 0
    convs = sorted((c for c in convs if mark is None or c.updated_at > mark),
                   key=lambda c: (c.updated_at, c.id))[:HAND_MAX_THREADS_PER_TICK]
    added = 0
    for conv in convs:
        others = [p for p in conv.participant_ids if p != ctx.own_id]
        if len(others) == 1 and not db.any_request_for_igsid(others[0]):
            try:
                msgs = sorted(ctx.ig.list_messages(conv.id), key=lambda m: m.created_at)
            except RateLimited:
                log.warning("hand-DM scan rate limited; stopping until the next scan")
                break
            except InstagramError as e:
                log.warning("hand-DM scan: listing messages of %s failed: %s", conv.id, e)
                break  # the watermark stays put, so this thread is looked at again
            if msgs and _import(ctx, others[0], msgs, reels, conv.usernames.get(others[0])):
                added += 1
        db.kv_set(HAND_SCAN_KEY, conv.updated_at.isoformat())
    return added
