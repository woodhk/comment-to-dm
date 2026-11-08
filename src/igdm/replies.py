"""Answering a reply: prewritten bank first, an AI-written reply second, the owner third."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from igdm.db import Request, Status
from igdm.jev import BUILTINS, JevError

if TYPE_CHECKING:
    from igdm.engine import Context

log = logging.getLogger(__name__)

THREAD_MAX = 10

FLAGS = {
    "cap": ("you", "asked {n} times, no email", "your call, reply yourself or press Done"),
    "email_elsewhere": ("info", "email is in their bio or somewhere else",
                        "find their email (check their bio) and paste it on the dashboard"),
    "off_topic": ("you", "not about the material", "read it and reply yourself if it's worth it"),
    "upset": ("you", "complaint or upset", "reply to them yourself"),
    "unclear": ("you", "not sure what they mean", "read it and reply yourself"),
    "no_fit": ("you", "nothing in the bank fits", "reply to them yourself"),
    "ai_cap": ("you", "AI already answered twice", "reply to them yourself"),
    "ai_failed": ("you", "AI reply failed ({err})", "reply to them yourself"),
    "ai_unsure": ("you", "the AI wasn't sure how to answer", "reply to them yourself"),
    "ai_promise": ("you", "the AI reply would promise something new", "reply to them yourself"),
    "ai_voice": ("you", "the AI reply broke your voice rules twice", "reply to them yourself"),
}


def flag(ctx: Context, req: Request, key: str, newest: datetime, **fmt: object) -> str:
    from igdm.engine import ACTED
    kind, reason, todo = FLAGS[key]
    ctx.db.flag(req.comment_id, kind, reason.format(**fmt), todo, newest=newest)
    log.info("comment %s flagged for the owner: %s", req.comment_id, reason.format(**fmt))
    return ACTED


def _options(ctx: Context, req: Request, stage: str) -> dict[str, str]:
    entries = [e for e in ctx.config.replies.bank
               if e.stage in (stage, "any") and (not e.reels or req.reel_key in e.reels)]
    return {**{e.id: e.when for e in entries}, **BUILTINS}


def _pick(probs: dict[str, float], bank_ids: set[str], threshold: float) -> str | None:
    top = max(probs, key=lambda k: probs[k])
    if probs[top] >= threshold:
        return top
    material = sum(p for k, p in probs.items() if k in bank_ids or k == "custom")
    return "custom" if material >= threshold else None


def send_reply(ctx: Context, req: Request, text: str, newest: datetime, **extra: object) -> str:
    """Send one bot reply that asks for the email. Exactly-once bookkeeping via the marker."""
    from igdm.engine import ACTED, DEFERRED, _followup_refused, _send_live, _Stop
    from igdm.instagram import MaybeSent, RateLimited
    done = {"status": Status.AWAITING_EMAIL, "asks": req.asks + 1, "last_user_msg_at": newest,
            **extra}
    err = _send_live(ctx, req, "reply", text, done=done,
                     crashed={"status": Status.NEEDS_OWNER, "last_user_msg_at": newest},
                     marker=f"reply:{req.comment_id}:{req.asks}")
    if isinstance(err, MaybeSent):
        return ACTED
    if isinstance(err, RateLimited):
        raise _Stop(acted=False)
    if err is not None:
        return ACTED if _followup_refused(ctx, req, err) else DEFERRED
    return ACTED


def route(ctx: Context, req: Request, thread: list[dict[str, str]], text: str,
          newest: datetime, now: datetime) -> str:
    from igdm.engine import DEFERRED, _say_yes, _to
    cfg = ctx.config.replies
    if req.status == Status.AWAITING_EMAIL and req.asks >= cfg.max_asks:
        return flag(ctx, req, "cap", newest, n=req.asks)
    stage = "offer" if req.status == Status.AWAITING_YES else "asked"
    options = _options(ctx, req, stage)
    try:
        probs = ctx.jev.route_reply(thread[-THREAD_MAX:], text, options)
    except JevError as e:
        ctx.db.log_event(req.comment_id, "jev_error", str(e))
        log.warning("jev failed routing comment %s (retry next tick): %s", req.comment_id, e)
        return DEFERRED
    bank_ids = set(options) - set(BUILTINS)
    choice = _pick(probs, bank_ids, ctx.config.reply_threshold)
    log.info("comment %s: reply routed to %s", req.comment_id, choice)

    if choice is None:
        return flag(ctx, req, "unclear", newest)
    if choice == "yes" and stage == "offer":
        return _say_yes(ctx, req, newest)
    if choice in bank_ids:
        entry = next(e for e in cfg.bank if e.id == choice)
        return send_reply(ctx, req, ctx.rng.choice(entry.replies), newest)
    if choice == "decline":
        return _to(ctx, req, Status.DECLINED, newest, f"reply: {text}")
    if choice in ("email_elsewhere", "off_topic", "upset"):
        return flag(ctx, req, choice, newest)
    return custom(ctx, req, thread, text, newest)


def custom(ctx: Context, req: Request, thread: list[dict[str, str]], text: str,
           newest: datetime) -> str:
    """Nothing in the bank fits: the AI writes a reply, checked against the owner's voice rules."""
    from igdm.voice import problems
    cfg = ctx.config.replies
    reel = next((r for r in ctx.config.reels if r.key == req.reel_key), None)
    if ctx.writer is None or reel is None:
        return flag(ctx, req, "no_fit", newest)
    if req.llm_replies >= cfg.max_llm_replies:
        return flag(ctx, req, "ai_cap", newest)
    fix = None
    for _ in range(2):  # one rewrite when the voice check fails
        try:
            r = ctx.writer.write(thread[-THREAD_MAX:], reel, fix)
        except Exception as e:  # noqa: BLE001 - LlmError or anything else: the owner takes it
            ctx.db.log_event(req.comment_id, "llm_error", f"{type(e).__name__}: {e}")
            log.warning("AI reply for comment %s failed: %s", req.comment_id, e)
            return flag(ctx, req, "ai_failed", newest, err=str(e)[:150])
        if not r.confident:
            return flag(ctx, req, "ai_unsure", newest)
        if r.new_promise:
            return flag(ctx, req, "ai_promise", newest)
        reply = r.text.strip()
        fix = problems(reply, cfg.deny_words) if reply else ["the reply was empty"]
        if not fix:
            return send_reply(ctx, req, reply, newest, llm_replies=req.llm_replies + 1)
        log.info("AI reply for comment %s broke the voice rules: %s", req.comment_id, fix)
    return flag(ctx, req, "ai_voice", newest)
