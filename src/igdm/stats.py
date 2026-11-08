"""Everything the dashboard shows, as one JSON-ready dict."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from igdm.activity import activity
from igdm.config import Config
from igdm.db import LIVE, Database, EmailRow, Request, Status, _dec
from igdm.metering import cost

INBOX_URL = "https://www.instagram.com/direct/inbox/"
CARD_STATUSES = (Status.NEEDS_INFO, Status.NEEDS_OWNER, Status.MANUAL_DM_NEEDED,
                 Status.WINDOW_EXPIRED, Status.EXPIRED)
NOT_COMMENTS = (Status.PRE_CUTOFF, Status.LEFT_FOR_OWNER, Status.PAUSED_SKIPPED)
DEFAULTS = {
    Status.NEEDS_OWNER: ("needs you", "check the thread and reply yourself"),
    Status.NEEDS_INFO: ("needs their email", "find their email and paste it below"),
    Status.MANUAL_DM_NEEDED: ("couldn't DM them automatically", "DM them yourself"),
    Status.WINDOW_EXPIRED: ("their 24 hour reply window closed",
                            "message them yourself in Instagram if you still want to"),
    Status.EXPIRED: ("the comment was too old to DM", "reply to the comment yourself if you want"),
}
EMAIL_FLAGS = {
    "failed": ("the email failed to send ({err})",
               "check the address, then press Send now or ask them again"),
    "unknown": ("not sure the email went out ({err})",
                "check Gmail's Sent folder and press Send now only if it isn't there"),
    "manual": ("the email hasn't been sent (captured before auto-send)", "press Send now"),
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _reel_name(cfg: Config, key: str) -> str:
    r = next((r for r in cfg.reels if r.key == key), None)
    return r.keyword if r else key


def _updated(db: Database, cid: str) -> datetime | None:
    row = db.rows("SELECT updated_at FROM requests WHERE comment_id=?", (cid,))
    return _dec(row[0]["updated_at"]) if row and row[0]["updated_at"] else None


def _flag_card(db: Database, cfg: Config, r: Request) -> dict[str, Any]:
    reason, todo = DEFAULTS[r.status]
    return {
        "cid": r.comment_id, "email_id": None,
        "kind": "info" if r.status == Status.NEEDS_INFO else "you",
        "handle": r.username, "reel": _reel_name(cfg, r.reel_key),
        "reason": r.flag_reason or r.note or reason, "todo": r.flag_todo or todo,
        "quote": r.last_their_text or (r.text if r.kind != "hand" else None),
        "thread_url": INBOX_URL, "can_carry_on": r.paused_from in LIVE,
        "since": _iso(_updated(db, r.comment_id)),
    }


def _email_card(e: EmailRow) -> dict[str, Any]:
    reason, todo = EMAIL_FLAGS[e.send_state]
    return {
        "cid": e.comment_id, "email_id": e.id, "kind": "you", "handle": e.instagram_handle,
        "reel": e.reel_keyword, "reason": reason.format(err=(e.last_error or "no details")[:120]),
        "todo": todo, "quote": e.email, "thread_url": INBOX_URL, "can_carry_on": False,
        "since": _iso(e.provided_at),
    }


def _funnel(db: Database, cfg: Config) -> list[dict[str, Any]]:
    out = []
    excluded = tuple(str(s) for s in NOT_COMMENTS)
    # newest reel first (by when it starts counting comments, which defaults to when it went up)
    for reel in sorted(cfg.reels, key=lambda r: r.handle_comments_after, reverse=True):
        k = reel.key
        one = lambda sql, *a: db.rows(sql, (k, *a))[0][0]  # noqa: E731
        out.append({
            "key": k,
            "reel": reel.keyword,
            "since": reel.handle_comments_after.isoformat(),
            "commented": one("SELECT COUNT(*) FROM requests WHERE reel_key=? AND kind != 'hand' "
                             f"AND status NOT IN ({','.join('?' * len(excluded))})", *excluded),
            "dmed": one("SELECT COUNT(*) FROM requests WHERE reel_key=? AND dm1_sent_at IS NOT NULL"),
            "hand": one("SELECT COUNT(*) FROM requests WHERE reel_key=? AND kind = 'hand'"),
            "replied": one("SELECT COUNT(*) FROM requests WHERE reel_key=? "
                           "AND last_user_msg_at IS NOT NULL"),
            "gave_email": one("SELECT COUNT(*) FROM emails e JOIN requests r "
                              "ON r.comment_id = e.comment_id WHERE r.reel_key=?"),
            "emailed": one("SELECT COUNT(*) FROM emails e JOIN requests r "
                           "ON r.comment_id = e.comment_id WHERE r.reel_key=? "
                           "AND e.emailed_at IS NOT NULL"),
        })
        _add_hand_emails(db, reel, out[-1])
    return out


def _add_hand_emails(db: Database, reel, f: dict[str, Any]) -> None:
    """Count the emails the owner entered with Send material (people who DM'd instead of commenting).

    Someone already DM'd on this reel (matched by @handle) only adds their email; anyone
    else is also a person DM'd who replied. Nobody is counted twice.
    """
    k = reel.key
    handles = lambda sql: {r[0].lower() for r in db.rows(sql, (k,)) if r[0]}  # noqa: E731
    dmed = handles("SELECT username FROM requests WHERE reel_key=? AND dm1_sent_at IS NOT NULL")
    replied = handles("SELECT username FROM requests WHERE reel_key=? AND last_user_msg_at IS NOT NULL")
    gave = handles("SELECT r.username FROM emails e JOIN requests r ON r.comment_id = e.comment_id "
                   "WHERE r.reel_key=?")
    people: dict[str, tuple[str, bool]] = {}  # handle (or address) -> (handle, any of them sent)
    for r in db.rows("SELECT instagram_handle, email, send_state FROM emails "
                     "WHERE comment_id LIKE 'manual:%' AND (reel_url = ? OR reel_keyword = ?) "
                     "ORDER BY id", (reel.url, reel.keyword)):
        h = (r["instagram_handle"] or "").lower()
        who = h or r["email"].lower()
        sent = people.get(who, (h, False))[1] or r["send_state"] == "sent"
        people[who] = (h, sent)
    for h, sent in people.values():
        if h and h in gave:
            continue  # the bot already counted their email
        f["gave_email"] += 1
        f["emailed"] += int(sent)
        if h and h in dmed:
            f["replied"] += int(h not in replied)
            continue
        f["dmed"] += 1
        f["hand"] += 1
        f["replied"] += 1


def _per_day(times: list[datetime], cfg: Config, now: datetime) -> list[dict[str, Any]]:
    today = now.astimezone(cfg.tz).date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    counts = {d: 0 for d in days}
    for t in times:
        d = t.astimezone(cfg.tz).date()
        if d in counts:
            counts[d] += 1
    return [{"date": d.isoformat(), "count": counts[d]} for d in days]


def _bot_dm_times(db: Database) -> list[datetime]:
    rows = db.rows("SELECT dm1_sent_at FROM requests WHERE dm1_sent_at IS NOT NULL AND kind != 'hand'")
    return [_dec(r[0]) for r in rows]


def _last_bot(db: Database, cid: str) -> tuple[str, datetime] | None:
    row = db.rows("SELECT text, sent_at FROM sent_messages WHERE comment_id=? "
                  "ORDER BY sent_at DESC LIMIT 1", (cid,))
    return (row[0]["text"], _dec(row[0]["sent_at"])) if row else None


def _mid(db: Database, cfg: Config, now: datetime) -> list[dict[str, Any]]:
    out = []
    for r in db.with_status(*sorted(LIVE)):
        if r.last_user_msg_at is None:
            continue
        text, by, at = r.last_their_text, "them", r.last_user_msg_at
        bot = _last_bot(db, r.comment_id)
        if bot and bot[1] > at:
            text, by, at = bot[0], "bot", bot[1]
        left = timedelta(hours=24) - (now - r.last_user_msg_at)
        out.append({
            "cid": r.comment_id, "handle": r.username, "reel": _reel_name(cfg, r.reel_key),
            "status": str(r.status), "last_text": text, "last_by": by,
            "ago_minutes": int((now - at).total_seconds() // 60),
            "window_left_minutes": max(0, int(left.total_seconds() // 60)),
        })
    return sorted(out, key=lambda m: m["window_left_minutes"])


def _no_reply(db: Database, cfg: Config, now: datetime) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"bot": [], "hand": []}
    for r in db.with_status(Status.AWAITING_YES, Status.AWAITING_EMAIL):  # either first DM
        if r.last_user_msg_at is not None:
            continue
        if r.status == Status.AWAITING_EMAIL and r.seen_until is not None:
            continue  # the owner handed it back to the bot
        at = r.dm1_sent_at or r.commented_at
        out["hand" if r.kind == "hand" else "bot"].append({
            "handle": r.username, "reel": _reel_name(cfg, r.reel_key), "dm_at": _iso(at),
            "ago_hours": int((now - at).total_seconds() // 3600),
        })
    return out


def _spend(db: Database, cfg: Config, now: datetime) -> dict[str, Any]:
    total = week = 0.0
    by: dict[str, dict[str, Any]] = {}
    week_start = now - timedelta(days=7)
    for row in db.usage_rows():
        c = cost(row, cfg.prices)
        m = by.setdefault(row["model"], {"model": row["model"], "usd": 0.0, "calls": 0,
                                         "input_tokens": 0, "output_tokens": 0,
                                         "missing_price": False})
        m["calls"] += row["calls"]
        m["input_tokens"] += row["input_tokens"]
        m["output_tokens"] += row["output_tokens"]
        if c is None:
            m["missing_price"] = True
            continue
        m["usd"] += c
        total += c
        if row["at"] >= week_start:
            week += c
    return {"total": total, "last_7_days": week,
            "by_model": sorted(by.values(), key=lambda m: -m["usd"])}


def has_leads(db: Database, key: str) -> bool:
    return bool(db.rows("SELECT 1 FROM requests WHERE reel_key=? LIMIT 1", (key,)))


def reels_state(db: Database, cfg: Config) -> dict[str, Any]:
    """What the Reels tab shows: every reel's settings, counts, and what a pause would hold."""
    from igdm.outbox import reel_for_email
    from igdm.reelform import TEMPLATE_SUBJECT, template_body
    funnel = {f["key"]: f for f in _funnel(db, cfg)}  # newest first, so match by key, not position
    queued = [e for e in db.emails_in_state("queued") if not e.by_owner]
    waiting = (str(Status.QUEUED), str(Status.WAITING))
    live = tuple(str(s) for s in LIVE)
    out = []
    for reel in cfg.reels:
        f = funnel[reel.key]
        count = lambda statuses: db.rows(  # noqa: E731
            f"SELECT COUNT(*) FROM requests WHERE reel_key=? AND status IN "
            f"({','.join('?' * len(statuses))})", (reel.key, *statuses))[0][0]
        held = [e for e in queued if (m := reel_for_email(cfg, e)) is not None and m.key == reel.key]
        out.append({
            "key": reel.key, "keyword": reel.keyword, "url": reel.url, "offer": reel.offer,
            "material_url": reel.material_url, "email_subject": reel.email_subject,
            "email_body": reel.email_body,
            "handle_comments_after": reel.handle_comments_after.astimezone(cfg.tz)
                                         .replace(tzinfo=None).isoformat(timespec="minutes"),
            "hand_dm_phrases": list(reel.hand_dm_phrases), "ai_notes": reel.ai_notes,
            "dm_offer": reel.dm_offer,
            "paused": reel.paused, "has_leads": has_leads(db, reel.key),
            "counts": {"comments": f["commented"], "emails_sent": f["emailed"]},
            "hold": {"mid": count(live), "waiting_dm": count(waiting), "queued_emails": len(held)},
        })
    after = cfg.email.after_got_it_seconds
    return {"reels": out,
            "template": {"subject": TEMPLATE_SUBJECT, "body": template_body(cfg.owner.name)},
            "messages": {"dm1": cfg.messages.dm1, "email_after_seconds": list(after) if after else None,
                         "email_delay_minutes": cfg.email.delay_minutes},
            "config_error": db.kv_get("config_error") or None}


def dashboard_state(db: Database, cfg: Config, now: datetime) -> dict[str, Any]:
    emails = db.emails()
    flags = [_flag_card(db, cfg, r) for r in db.with_status(*CARD_STATUSES)]
    flags += [_email_card(e) for e in emails if e.send_state in EMAIL_FLAGS]
    flags.sort(key=lambda f: f["since"] or "", reverse=True)
    mid = _mid(db, cfg, now)
    spend = _spend(db, cfg, now)
    left = db.with_status(Status.LEFT_FOR_OWNER)
    return {
        "now": now.isoformat(),
        "ig_blocked_since": db.kv_get("ig_access_blocked_since") or None,
        "config_error": db.kv_get("config_error") or None,
        "cards": {"emails": len(emails), "spend_usd": spend["total"],
                  "spend_missing_price": any(m["missing_price"] for m in spend["by_model"]),
                  "mid_conversation": len(mid)},
        "flags": flags,
        "funnel": _funnel(db, cfg),
        "emails_per_day": _per_day([e.provided_at for e in emails], cfg, now),
        "dms_per_day": _per_day(_bot_dm_times(db), cfg, now),
        "emails": [{"id": e.id, "email": e.email, "handle": e.instagram_handle,
                    "name": e.name, "manual": e.comment_id.startswith("manual:"),
                    "reel": e.reel_keyword, "captured_at": _iso(e.provided_at),
                    "state": e.send_state, "sent_at": _iso(e.emailed_at)}
                   for e in sorted(emails, key=lambda e: e.provided_at, reverse=True)],
        "mid": mid,
        "no_reply": _no_reply(db, cfg, now),
        "left_alone": {"count": len(left),
                       "items": [{"handle": r.username, "text": r.text,
                                  "at": _iso(r.commented_at)} for r in left[-50:][::-1]]},
        "spend": spend,
        "activity": activity(db, cfg, now, limit=5),
        "reel_options": [{"key": r.key, "keyword": r.keyword} for r in cfg.reels],
    }
