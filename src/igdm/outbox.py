"""The email phase: send each captured address its reel's material, exactly once."""

from __future__ import annotations

import logging

from igdm.db import EmailRow
from igdm.mailer import MailRefused
from igdm.messages import render

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry for a clear refusal


def recover(ctx) -> None:
    """A row still `sending` means we crashed mid-send: it may have gone out, never resend."""
    for row in ctx.db.emails_in_state("sending"):
        ctx.db.mark_email(row.id, "unknown",
                          error="the bot stopped while sending; check Gmail's Sent folder")
        ctx.db.log_event(row.comment_id, "email_unknown",
                         "the bot stopped while sending; check Gmail's Sent folder")
        log.warning("email %s to %s: interrupted mid-send, marked unknown", row.id, row.email)


def reel_for_email(cfg, row: EmailRow):
    """The configured reel an email row belongs to (rows store the reel's link and keyword)."""
    return next((r for r in cfg.reels if r.url == row.reel_url or r.keyword == row.reel_keyword),
                None)


def render_email(cfg, reel, *, name: str, keyword: str) -> tuple[str, str]:
    """The subject and body someone gets for `reel` (the [messages] fallback without one)."""
    m = cfg.messages
    subject = reel.email_subject if reel else m.email_subject
    body = reel.email_body if reel else m.email_body
    kw = dict(name=name, keyword=reel.keyword if reel else keyword,
              material_url=reel.material_url if reel else "")
    return render(subject, **kw), render(body, **kw)


def _compose(ctx, row: EmailRow) -> tuple[str, str]:
    return render_email(ctx.config, reel_for_email(ctx.config, row),
                        name=row.name or row.instagram_handle, keyword=row.reel_keyword)


def send_due_emails(ctx) -> int:
    """Send every queued email whose time has come. Returns how many went out."""
    if ctx.mailer is None:
        return 0
    db = ctx.db
    recover(ctx)
    sent = 0
    for row in db.due_emails(ctx.now()):
        reel = reel_for_email(ctx.config, row)
        if reel is not None and reel.paused and not row.by_owner:
            continue  # held, still queued, until the reel is resumed
        subject, body = _compose(ctx, row)
        db.mark_email(row.id, "sending")
        try:
            ctx.mailer.send(row.email, subject, body)
        except MailRefused as e:
            last = row.attempts + 1 >= MAX_ATTEMPTS
            db.mark_email(row.id, "failed" if last else "queued", error=str(e), bump_attempts=True)
            db.log_event(row.comment_id, "email_failed", str(e))
            log.warning("email %s to %s refused (%s): %s", row.id, row.email,
                        "giving up" if last else "will retry", e)
            continue
        except Exception as e:  # noqa: BLE001 - MailMaybeSent or anything unexpected
            db.mark_email(row.id, "unknown", error=f"{type(e).__name__}: {e}")
            db.log_event(row.comment_id, "email_unknown", f"{type(e).__name__}: {e}")
            log.error("email %s to %s may or may not have gone out: %s", row.id, row.email, e)
            continue
        db.mark_email(row.id, "sent", at=ctx.now())
        if ctx.config.messages.sent_dm and not row.comment_id.startswith("manual:"):
            db.set_confirm(row.id, "pending")  # engine.send_sent_dms DMs them it went
        log.info("emailed the %s material to %s", row.reel_keyword, row.email)
        sent += 1
    return sent
