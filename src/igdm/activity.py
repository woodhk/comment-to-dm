"""The dashboard's activity feed: everything the bot did, newest first.

Built from what the bot already records (sent DMs, public replies, emails, events), so
nothing extra is tracked. Runs of the same error with nothing else in between fold into
one row, so a night of "couldn't reach Instagram" reads as one line.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from igdm.config import Config
from igdm.db import FOR_OWNER, Database, _dec, _enc

HEARTBEAT_KEY = "last_tick"  # {"start": iso, "end": iso} of the bot's latest check
FETCH = 2000  # rows read per source for one page, before folding
MAX_DETAIL = 300

# event name -> (title, level). Anything not listed shows as an error under its own name.
ERRORS: dict[str, tuple[str, str]] = {
    "list_comments_failed": ("Couldn't check comments", "error"),
    "list_conversations_failed": ("Couldn't check DMs", "error"),
    "list_messages_failed": ("Couldn't read a conversation", "error"),
    "reply_check_failed": ("Couldn't check a reply", "error"),
    "public_reply_failed": ("Comment reply failed", "error"),
    "public_reply_maybe_sent": ("Comment reply may not have posted", "warn"),
    "maybe_sent": ("A DM may not have gone out", "warn"),
    "dm1_paused": ("First DMs paused for an hour", "warn"),
    "rate_limited": ("Instagram asked the bot to slow down", "warn"),
    "reply_backoff": ("Replies paused for a bit", "warn"),
    "jev_error": ("The request checker couldn't be reached", "error"),
    "llm_error": ("AI reply failed", "error"),
    "got_it_failed": ("DM failed", "error"),
    "dm1_failed": ("First DM failed", "error"),
    "followup_failed": ("DM failed", "error"),
    "email_failed": ("Email failed", "error"),
    "email_unknown": ("Email may not have sent", "warn"),
    "phase_failed": ("Part of a check crashed", "error"),
}


# Raw error text -> what it means, for the ones that keep coming up. First match wins.
PLAIN: list[tuple[tuple[str, ...], str]] = [
    (("ConnectError", "nodename nor servname", "Network is unreachable"),
     "No internet connection. The server couldn't reach the internet."),
    (("ReadTimeout", "ConnectTimeout", "timed out"), "Instagram took too long to answer."),
    (("log in to www.facebook.com",), "Facebook wants you to log in and finish a check."),
    (("code=190",), "Instagram's login stopped working, so the bot needs a new token."),
    (("status=500", "status=502", "status=503"), "Instagram had a problem on its side."),
]


def _plain(raw: str) -> str:
    for needles, text in PLAIN:
        if any(n in raw for n in needles):
            return text
    return _clip(raw)


def _error_title(event: str) -> tuple[str, str]:
    return ERRORS.get(event, (event.replace("_", " ").capitalize(), "error"))


def _clip(s: str | None) -> str:
    s = s or ""
    return s if len(s) <= MAX_DETAIL else s[: MAX_DETAIL - 1] + "…"


def _raw(db: Database, cfg: Config, before: str | None) -> tuple[list[dict], bool]:
    """Every feed row older than `before`, newest first. True if a source hit its cap."""
    keyword = {r.key: r.keyword for r in cfg.reels}
    cut = "AND {col} < ?" if before else ""
    args = (before, FETCH) if before else (FETCH,)
    out: list[dict] = []
    capped = False

    def q(sql: str, col: str):
        nonlocal capped
        rows = db.rows(sql.format(cut=cut.format(col=col)) + f" ORDER BY {col} DESC LIMIT ?", args)
        capped = capped or len(rows) >= FETCH
        return rows

    for r in q("SELECT s.text, s.sent_at, r.username, r.reel_key, r.dm1_text, r.dm1_sent_at, r.kind "
               "FROM sent_messages s LEFT JOIN requests r ON r.comment_id = s.comment_id "
               "WHERE 1=1 {cut}", "s.sent_at"):
        first = (r["kind"] != "hand" and r["dm1_text"] == r["text"]
                 and r["dm1_sent_at"] == r["sent_at"])
        out.append({"type": "dm" if first else "dm_reply",
                    "title": "Sent first DM" if first else "Replied in DMs",
                    "handle": r["username"], "reel": keyword.get(r["reel_key"]),
                    "detail": _clip(r["text"]), "at": r["sent_at"]})

    for r in q("SELECT p.base_text, p.at, r.username, r.reel_key FROM public_replies p "
               "LEFT JOIN requests r ON r.comment_id = p.comment_id WHERE 1=1 {cut}", "p.at"):
        out.append({"type": "public_reply", "title": "Replied to a comment",
                    "handle": r["username"], "reel": keyword.get(r["reel_key"]),
                    "detail": _clip(r["base_text"]), "at": r["at"]})

    for r in q("SELECT instagram_handle, email, reel_keyword, provided_at, comment_id FROM emails "
               "WHERE 1=1 {cut}", "provided_at"):
        by_hand = r["comment_id"].startswith("manual:")
        out.append({"type": "email_captured", "title": "Added by you" if by_hand else "Got an email",
                    "handle": r["instagram_handle"] or None, "reel": r["reel_keyword"],
                    "detail": r["email"], "at": r["provided_at"]})

    for r in q("SELECT instagram_handle, email, reel_keyword, emailed_at FROM emails "
               "WHERE send_state = 'sent' AND emailed_at IS NOT NULL {cut}", "emailed_at"):
        out.append({"type": "email_sent", "title": "Emailed the material",
                    "handle": r["instagram_handle"], "reel": r["reel_keyword"],
                    "detail": r["email"], "at": r["emailed_at"]})

    flagged = {str(s) for s in FOR_OWNER}
    for r in q("SELECT e.event, e.detail, e.at, r.username, r.reel_key, r.flag_reason, r.note "
               "FROM events e LEFT JOIN requests r ON r.comment_id = e.comment_id "
               "WHERE 1=1 {cut}", "e.at"):
        ev = r["event"]
        base = {"handle": r["username"], "reel": keyword.get(r["reel_key"]), "at": r["at"]}
        if ev.startswith("status:"):
            if ev.split("->", 1)[-1] in flagged:
                out.append({**base, "type": "flagged", "title": "Handed to you",
                            "detail": _clip(r["flag_reason"] or r["note"])})
            continue
        title, level = _error_title(ev)
        out.append({**base, "type": "error", "event": ev, "level": level, "title": title,
                    "detail": _plain(r["detail"] or ""), "raw": _clip(r["detail"])})

    for o in out:
        o["at"] = _dec(o["at"])
    out.sort(key=lambda o: o["at"], reverse=True)
    return out, capped


def _fold(rows: list[dict]) -> list[dict]:
    """Within each unbroken run of errors (nothing else happened in between), merge the
    errors of the same kind and person into one row with a count. During an outage the DM
    and comment checks fail in turn, so this gives one row per kind instead of dozens."""
    groups: list[dict] = []
    run: dict[tuple, dict] = {}  # the current run of errors, by (event, handle)
    run_no = 0
    for r in rows:
        if r["type"] != "error":
            run = {}
            run_no += 1
            groups.append({**r, "count": 1, "first_at": r["at"], "_run": run_no})
            run_no += 1
            continue
        g = run.get((r["event"], r["handle"]))
        if g:
            g["count"] += 1
            g["first_at"] = r["at"]
        else:
            g = run[(r["event"], r["handle"])] = {**r, "count": 1, "first_at": r["at"],
                                                  "_run": run_no}
            groups.append(g)
    return groups


def _health(db: Database, cfg: Config, now: datetime) -> dict[str, Any]:
    raw = db.kv_get(HEARTBEAT_KEY)
    if not raw:
        return {"state": "unknown", "last_check": None}
    beat = json.loads(raw)
    start, end = datetime.fromisoformat(beat["start"]), datetime.fromisoformat(beat["end"])
    out: dict[str, Any] = {"state": "ok", "last_check": end.isoformat()}
    if now - end > max(timedelta(minutes=5), timedelta(seconds=3 * cfg.poll_seconds)):
        out["state"] = "stopped"
        return out
    errs = db.rows("SELECT event FROM events WHERE at >= ? AND event NOT LIKE 'status:%' "
                   "ORDER BY at DESC LIMIT 1", (_enc(start),))
    if errs:
        out["state"] = "trouble"
        out["error"] = _error_title(errs[0]["event"])[0]
    return out


def activity(db: Database, cfg: Config, now: datetime, *, before: str | None = None,
             limit: int = 50) -> dict[str, Any]:
    """One page of the feed. Pass the returned `next` as `before` for the page after it."""
    if before:
        before = _enc(datetime.fromisoformat(before))
    rows, capped = _raw(db, cfg, before)
    groups = _fold(rows)
    n = min(limit, len(groups))
    # A run of errors folds into rows that overlap in time, so a page takes all of a run or
    # none of it; otherwise the next page would show part of it again or skip some.
    while 0 < n < len(groups) and groups[n]["_run"] == groups[n - 1]["_run"]:
        n += 1
    page = groups[:n]
    more = len(groups) > n or capped
    items = [{**{k: v for k, v in g.items() if k != "_run"}, "at": g["at"].isoformat(),
              "first_at": g["first_at"].isoformat()} for g in page]
    oldest = min((g["first_at"] for g in page), default=None)
    return {"items": items, "more": more,
            "next": oldest.isoformat() if oldest and more else None,
            "health": _health(db, cfg, now)}
