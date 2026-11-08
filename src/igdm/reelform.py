"""Check a reel form from the dashboard: what's missing, what clashes, what breaks the owner's voice.

No AI here. The email starts from a template whose [gaps] the owner must fill in themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from igdm import voice
from igdm.config import _SHORTCODE_RE, Config, ReelConfig
from igdm.engine import material_id
from igdm.instagram import InstagramError, Media
from igdm.messages import render

TEMPLATE_SUBJECT = "the {keyword} [what it is] you asked for"
_TEMPLATE_BODY = """Hey {name},

OWNER_NAME here, from the reel you commented "{keyword}" on. Here's the material like I promised 👇

{material_url}

[what's inside it]

[any tips for using it]

Enjoy, and let me know what you think!

OWNER_NAME"""
GAP_RE = re.compile(r"\[[^\[\]\n]{1,60}\]")
REQUIRED = ("keyword", "url", "offer", "material_url", "email_subject", "email_body")
PLACEHOLDERS = "only {name}, {keyword} and {material_url} can go in curly brackets"


def template_body(owner_name: str) -> str:
    """The starting email, signed with the owner's name. Its {placeholders} stay as they are."""
    return _TEMPLATE_BODY.replace("OWNER_NAME", owner_name)


def slug(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")


@dataclass(frozen=True)
class CheckResult:
    errors: dict[str, str]  # form field -> what's wrong, in plain words
    key: str
    posted_at: datetime | None
    fields: dict[str, object]  # TOML-ready values for reelfile
    same_material: list[str] = field(default_factory=list)  # keywords of other reels with this material


def _text(form: dict, name: str) -> str:
    v = form.get(name)
    return v.replace("\r\n", "\n").replace("\r", "\n") if isinstance(v, str) else ""


def _phrases(v: Any) -> list[str]:
    items = v.splitlines() if isinstance(v, str) else (v or [])
    return [p.strip() for p in items if isinstance(p, str) and p.strip()]


def _local(dt: datetime, cfg: Config) -> str:
    return dt.astimezone(cfg.tz).replace(tzinfo=None).isoformat(timespec="seconds")


def _check_email_text(name: str, text: str, form_kw: dict, cfg: Config, errors: dict) -> None:
    if not text.strip():
        return
    gaps = GAP_RE.findall(text)
    if gaps:
        errors[name] = "still to fill in: " + ", ".join(gaps)
        return
    try:
        render(text, name="Sam", **form_kw)
    except (KeyError, ValueError, IndexError):
        errors[name] = PLACEHOLDERS
        return
    found = voice.problems(text, cfg.replies.deny_words)
    if found:
        errors[name] = "this " + "; ".join(found)


def check_reel(form: dict, cfg: Config, *, editing: str | None, has_leads: bool,
               find_media: Callable[[str], Media | None] | None,
               now: datetime | None = None) -> CheckResult:
    errors: dict[str, str] = {}
    now = now or datetime.now(timezone.utc)
    vals = {k: _text(form, k) for k in ("keyword", "url", "offer", "material_url", "email_subject",
                                       "email_body", "handle_comments_after", "ai_notes", "dm_offer")}
    for k in ("keyword", "url", "offer", "material_url", "handle_comments_after", "ai_notes", "dm_offer"):
        vals[k] = vals[k].strip()
    for k in REQUIRED:
        if not vals[k].strip():
            errors[k] = "this can't be empty"

    current: ReelConfig | None = None
    if editing is not None:
        current = next((r for r in cfg.reels if r.key == editing), None)
        if current is None:
            errors["key"] = "that reel isn't in config.toml any more"
    others = [r for r in cfg.reels if r.key != editing]
    key = editing if editing is not None else slug(vals["keyword"])

    kw, url = vals["keyword"], vals["url"]
    if kw and "keyword" not in errors:
        if any(r.keyword.casefold() == kw.casefold() for r in others):
            errors["keyword"] = "another reel already uses this keyword"
        elif editing is None and not key:
            errors["keyword"] = "use at least one letter or number"
        elif editing is None and any(r.key == key for r in cfg.reels):
            errors["keyword"] = f'a reel with the key "{key}" already exists'
        elif current is not None and has_leads and kw != current.keyword:
            errors["keyword"] = "this reel already has leads, so its keyword can't change"

    shortcode = None
    if url and "url" not in errors:
        m = _SHORTCODE_RE.match(url)
        if not m:
            errors["url"] = "paste the reel's Instagram link, like instagram.com/reel/..."
        else:
            shortcode = m.group(1)
            if any(r.shortcode == shortcode for r in others):
                errors["url"] = "another reel already uses this link"
            elif current is not None and has_leads and url != current.url:
                errors["url"] = "this reel already has leads, so its link can't change"

    mat = vals["material_url"]
    if mat and not mat.startswith(("http://", "https://")):
        errors["material_url"] = "this needs to be a full link starting with https://"
    mid = material_id(mat)  # anyone who got it from one of these reels isn't offered it again
    same = [r.keyword for r in others if mid and material_id(r.material_url) == mid]

    if vals["dm_offer"]:  # it goes in a DM: same voice rules as the email, no curly brackets
        found = voice.problems(vals["dm_offer"], cfg.replies.deny_words)
        if "{" in vals["dm_offer"] or "}" in vals["dm_offer"]:
            errors["dm_offer"] = "just the words, no curly brackets"
        elif len(vals["dm_offer"]) > 80:
            errors["dm_offer"] = "keep it short, under 80 characters"
        elif found:
            errors["dm_offer"] = "this " + "; ".join(found)

    render_kw = {"keyword": kw, "material_url": mat}
    _check_email_text("email_subject", vals["email_subject"], render_kw, cfg, errors)
    _check_email_text("email_body", vals["email_body"], render_kw, cfg, errors)

    start = vals["handle_comments_after"]
    if start:
        try:
            parsed = datetime.fromisoformat(start)
            if parsed.tzinfo is not None:
                raise ValueError
            start = parsed.isoformat(timespec="seconds")
        except ValueError:
            errors["handle_comments_after"] = "pick a date and time"

    posted_at = None
    need_lookup = shortcode and "url" not in errors and (current is None or shortcode != current.shortcode)
    if need_lookup and find_media is not None:
        try:
            media = find_media(shortcode)
        except InstagramError as e:
            errors["url"] = f"couldn't check the link with Instagram ({getattr(e, 'message', e)})"
        else:
            if media is None:
                errors["url"] = "that link isn't one of your posts"
            else:
                posted_at = media.posted_at

    if not start or "handle_comments_after" in errors:
        start = _local(posted_at or now, cfg)

    fields: dict[str, object] = {
        "key": key, "url": url, "keyword": kw, "offer": vals["offer"], "material_url": mat,
        "handle_comments_after": start, "hand_dm_phrases": _phrases(form.get("hand_dm_phrases")),
        "ai_notes": vals["ai_notes"], "dm_offer": vals["dm_offer"],
        "email_subject": vals["email_subject"].strip(),
        "email_body": vals["email_body"],
    }
    return CheckResult(errors=errors, key=key, posted_at=posted_at, fields=fields, same_material=same)
