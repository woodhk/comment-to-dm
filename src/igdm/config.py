from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from igdm.milestones import MilestonesConfig, parse_milestones
from igdm.routes import BUILTINS

CONFIG_VERSION = 2  # bump when a setting is renamed, removed or newly required; add a step in upgrade.py
DEFAULT_STYLE = "casual and short, like texting a friend. everyday words, no big words."
MODES = ("full", "public_only")
APIS = ("facebook_login", "instagram_login")
MESSAGING_NODES = ("page", "ig")
_SHORTCODE_RE = re.compile(r"^https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(?:reel|reels|p)/([\w-]+)/?")
_REEL_FIELDS = ("key", "url", "keyword", "offer", "material_url", "handle_comments_after")


@dataclass(frozen=True)
class Owner:
    """Who the bot writes as: their name goes into the AI prompts."""
    name: str
    about: str = ""  # a few words on who they are, e.g. "a baker who posts recipes"
    style: str = DEFAULT_STYLE  # how they write, one line


@dataclass(frozen=True)
class ReelConfig:
    key: str
    url: str
    shortcode: str
    keyword: str
    offer: str
    material_url: str
    handle_comments_after: datetime
    email_subject: str = ""
    email_body: str = ""
    hand_dm_phrases: tuple[str, ...] = ()
    ai_notes: str = ""  # extra true facts the AI writer may mention for this reel
    paused: bool = False  # the owner paused it on the dashboard: the bot does nothing for this reel
    dm_offer: str = ""  # finishes the email-first DM 1 ({offer}), e.g. "the free guide"


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    from_address: str
    from_name: str
    delay_minutes: float
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    # Send the email this many seconds (random in [min, max]) after the got-it DM goes out.
    # None: delay_minutes after their message, as before (also used when no got-it goes out).
    after_got_it_seconds: tuple[float, float] | None = None


@dataclass(frozen=True)
class BankEntry:
    id: str
    when: str  # what Jev classifies the reply against
    stage: Literal["offer", "asked", "any"]  # offer: before the email ask; asked: after it
    replies: tuple[str, ...]  # wordings, one picked at random ("yes" uses followup_1/2)
    reels: tuple[str, ...] = ()  # reel keys this entry is for; empty = every reel


@dataclass(frozen=True)
class RepliesConfig:
    enabled: bool = False
    bank: tuple[BankEntry, ...] = ()
    max_asks: int = 3
    max_llm_replies: int = 2
    deny_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class Price:
    per_call: float = 0.0
    per_m_input: float = 0.0  # USD per million input tokens
    per_m_output: float = 0.0


STAGES = ("offer", "asked", "any")
PROVIDERS = ("openai", "gemini")


@dataclass(frozen=True)
class LlmConfig:
    provider: Literal["openai", "gemini"]
    model: str
    timeout: float = 30.0


@dataclass(frozen=True)
class Pacing:
    dm_delay_seconds: tuple[float, float]
    public_reply_delay_seconds: tuple[float, float]
    followup_gap_seconds: tuple[float, float]
    backlog_gap_seconds: tuple[float, float]
    max_dms_per_hour: int
    quiet_start: time
    quiet_end: time
    reply_settle_seconds: int
    email_wait_minutes: int
    private_reply_max_age_hours: int
    # Someone who answers within chat_window_seconds of the bot's last message is chatting:
    # their conversation is checked every chat_poll_seconds for chat_watch_seconds after the
    # bot's last message, and answered chat_settle_seconds after they write (not
    # reply_settle_seconds).
    chat_window_seconds: int = 120
    chat_poll_seconds: tuple[float, float] = (10.0, 15.0)
    chat_watch_seconds: int = 180
    chat_settle_seconds: int = 5


@dataclass(frozen=True)
class Messages:
    dm1: str
    repeat_dm1: str
    followup_1: str
    followup_2: str
    public_replies: list[str]
    got_it: list[str]
    email_subject: str
    email_body: str
    # The first DM asks for the email straight away (False: "do you still want it?" first)
    ask_email_first: bool = False
    # DM'd once the email has gone out; {email} is their address. Empty: no such DM.
    sent_dm: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    tz: ZoneInfo
    poll_seconds: int
    mode: Literal["full", "public_only"]
    db_path: Path
    jev_model: str
    comment_threshold: float
    reply_threshold: float
    pacing: Pacing
    messages: Messages
    reels: list[ReelConfig]
    owner: Owner
    # Platform change (2026-11-03): Meta no longer offers Instagram Login to new apps.
    api: Literal["facebook_login", "instagram_login"] = "facebook_login"
    messaging_node: Literal["page", "ig"] = "page"  # facebook_login: POST /{node}/messages
    email: EmailConfig = EmailConfig(False, "", "", 2.0)
    replies: RepliesConfig = RepliesConfig()
    llm: LlmConfig | None = None
    prices: dict[str, Price] = field(default_factory=dict)
    email_threshold: float = 0.5  # below this chance of being real, a typed address is flagged
    jev_fallback_model: str = "gpt-6-luna"  # the OpenAI model that spots requests without a Jev key
    milestones: MilestonesConfig = MilestonesConfig()  # the dashboard's Milestones page


def _need(d: dict, key: str, where: str):
    if key not in d:
        raise ValueError(f"missing {key!r} in {where}")
    return d[key]


def _range(d: dict, key: str) -> tuple[float, float]:
    v = _need(d, key, "[pacing]")
    if not (isinstance(v, list) and len(v) == 2 and v[0] <= v[1]):
        raise ValueError(f"pacing.{key} must be [min, max]")
    return (float(v[0]), float(v[1]))


def _flag(d: dict, key: str) -> bool:
    v = d.get(key, False)
    if not isinstance(v, bool):
        raise ValueError(f"messages.{key} must be true or false")
    return v


def _sent_dm(m: dict) -> list[str]:
    lines = m.get("sent_dm", [])
    if not isinstance(lines, list) or not all(isinstance(x, str) and x.strip() for x in lines):
        raise ValueError('messages.sent_dm must be a list of wordings, like ["just sent it to {email}"]')
    for line in lines:
        try:
            line.format(email="x@y.com")
        except (KeyError, IndexError, ValueError, AttributeError):
            raise ValueError("messages.sent_dm can only use {email} in curly brackets") from None
    return list(lines)


def _check_dm1(messages: Messages) -> None:
    """A typo here would stall every first DM, so refuse the config instead."""
    from igdm.messages import render_dm1
    for key in ("dm1", "repeat_dm1"):
        try:
            render_dm1(getattr(messages, key), name="x", keyword="k", offer="o")
        except (KeyError, IndexError, ValueError, AttributeError):
            raise ValueError(f"messages.{key} can only use {{name}}, {{keyword}} and {{offer}} "
                             "in curly brackets") from None


def _dm_delay(p: dict) -> tuple[float, float]:
    """`dm_delay_seconds`, or the older `dm_delay_minutes` converted to seconds."""
    if "dm_delay_seconds" in p or "dm_delay_minutes" not in p:
        return _range(p, "dm_delay_seconds")
    lo, hi = _range(p, "dm_delay_minutes")
    return (lo * 60, hi * 60)


def _after_got_it(e: dict) -> tuple[float, float] | None:
    v = e.get("after_got_it_seconds")
    if v is None:
        return None
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)
            and 0 <= v[0] <= v[1]):
        raise ValueError("email.after_got_it_seconds must be [min, max] seconds, like [20, 30]")
    return (float(v[0]), float(v[1]))


def _hhmm(s: str) -> time:
    try:
        return time.fromisoformat(s)
    except (TypeError, ValueError) as e:
        raise ValueError(f"bad time {s!r}, expected HH:MM") from e


def _parse_reel(raw: dict, tz: ZoneInfo, msgs: dict) -> ReelConfig:
    where = f"reel {raw.get('key', '?')!r}"
    for f in _REEL_FIELDS:
        _need(raw, f, where)
    m = _SHORTCODE_RE.match(raw["url"])
    if not m:
        raise ValueError(f"bad reel url (no shortcode): {raw['url']!r}")
    when = raw["handle_comments_after"]
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError as e:
            raise ValueError(f"bad handle_comments_after in {where}") from e
    if not isinstance(when, datetime):
        raise ValueError(f"bad handle_comments_after in {where}")
    when = when.replace(tzinfo=tz) if when.tzinfo is None else when
    paused = raw.get("paused", False)
    if not isinstance(paused, bool):
        raise ValueError(f"paused in {where} must be true or false")
    return ReelConfig(
        key=raw["key"], url=raw["url"], shortcode=m.group(1), keyword=raw["keyword"],
        offer=raw["offer"], material_url=raw["material_url"], handle_comments_after=when,
        email_subject=raw.get("email_subject") or _need(msgs, "email_subject", "[messages]"),
        email_body=raw.get("email_body") or _need(msgs, "email_body", "[messages]"),
        hand_dm_phrases=tuple(raw.get("hand_dm_phrases", ())),
        ai_notes=raw.get("ai_notes", ""),
        paused=paused,
        dm_offer=str(raw.get("dm_offer", "")).strip(),
    )


def _parse_replies(raw: dict | None) -> RepliesConfig:
    if raw is None:
        return RepliesConfig()
    bank = []
    for i, b in enumerate(raw.get("bank", [])):
        where = f"[[replies.bank]] #{i + 1} ({b.get('id', '?')!r})"
        for k in ("id", "when", "stage"):
            _need(b, k, where)
        if b["stage"] not in STAGES:
            raise ValueError(f"{where}: stage must be one of {STAGES}")
        if b["id"] in BUILTINS:
            raise ValueError(f"{where}: id {b['id']!r} is built in, pick another")
        replies = tuple(b.get("replies", ()))
        if b["id"] != "yes" and not replies:
            raise ValueError(f"{where}: needs at least one reply wording")
        bank.append(BankEntry(b["id"], b["when"], b["stage"], replies, tuple(b.get("reels", ()))))
    ids = [e.id for e in bank]
    if len(set(ids)) != len(ids):
        raise ValueError("[[replies.bank]] ids must be unique")
    yes = next((e for e in bank if e.id == "yes"), None)
    if yes is None or yes.stage != "offer":
        raise ValueError('[[replies.bank]] needs an entry with id = "yes" and stage = "offer"')
    return RepliesConfig(
        enabled=bool(raw.get("enabled", False)), bank=tuple(bank),
        max_asks=int(raw.get("max_asks", 3)), max_llm_replies=int(raw.get("max_llm_replies", 2)),
        deny_words=tuple(raw.get("deny_words", ())),
    )


def _parse_llm(raw: dict | None) -> LlmConfig | None:
    if raw is None:
        return None
    provider = _need(raw, "provider", "[llm]")
    if provider not in PROVIDERS:
        raise ValueError(f"llm.provider must be one of {PROVIDERS}, got {provider!r}")
    return LlmConfig(provider, _need(raw, "model", "[llm]"), float(raw.get("timeout", 30)))


def _parse_owner(raw: dict | None) -> Owner:
    if not isinstance(raw, dict):
        raise ValueError("missing [owner] in config (it needs a name)")
    name = raw.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("owner.name can't be empty")
    about, style = raw.get("about", ""), raw.get("style", "")
    if not isinstance(about, str) or not isinstance(style, str):
        raise ValueError("owner.about and owner.style must be text")
    return Owner(name.strip(), about.strip(), style.strip() or DEFAULT_STYLE)


def settings_version(raw: dict) -> int:
    """The config_version of a parsed config.toml (1 when the key is missing)."""
    version = raw.get("config_version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("config_version must be a whole number (like 2), not text or a decimal")
    return version


def load_config(path: Path) -> Config:
    path = Path(path)
    with path.open("rb") as f:
        raw = tomllib.load(f)

    version = settings_version(raw)
    if version < CONFIG_VERSION:
        raise ValueError(
            f"config.toml uses settings version {version}, and this version of the bot "
            f"needs {CONFIG_VERSION}. Run: uv run igdm upgrade"
        )
    if version > CONFIG_VERSION:
        raise ValueError(
            f"config.toml uses settings version {version}, newer than this bot understands "
            f"({CONFIG_VERSION}). Update the bot's code first."
        )

    tz = ZoneInfo(_need(raw, "timezone", "config"))
    mode = _need(raw, "mode", "config")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    api = raw.get("api", "facebook_login")
    if api not in APIS:
        raise ValueError(f"api must be one of {APIS}, got {api!r}")
    node = raw.get("messaging_node", "page")
    if node not in MESSAGING_NODES:
        raise ValueError(f"messaging_node must be one of {MESSAGING_NODES}, got {node!r}")

    db_path = Path(_need(raw, "db_path", "config"))
    if not db_path.is_absolute():
        db_path = path.resolve().parent / db_path

    jev = _need(raw, "jev", "config")
    p = _need(raw, "pacing", "config")
    qh = _need(p, "quiet_hours", "[pacing]")
    if len(qh) != 2:
        raise ValueError("pacing.quiet_hours must be [start, end]")
    pacing = Pacing(
        dm_delay_seconds=_dm_delay(p),
        public_reply_delay_seconds=_range(p, "public_reply_delay_seconds"),
        followup_gap_seconds=_range(p, "followup_gap_seconds"),
        backlog_gap_seconds=_range(p, "backlog_gap_seconds"),
        max_dms_per_hour=int(_need(p, "max_dms_per_hour", "[pacing]")),
        quiet_start=_hhmm(qh[0]),
        quiet_end=_hhmm(qh[1]),
        reply_settle_seconds=int(_need(p, "reply_settle_seconds", "[pacing]")),
        email_wait_minutes=int(_need(p, "email_wait_minutes", "[pacing]")),
        private_reply_max_age_hours=int(_need(p, "private_reply_max_age_hours", "[pacing]")),
        chat_window_seconds=int(p.get("chat_window_seconds", 120)),
        chat_poll_seconds=_range(p, "chat_poll_seconds") if "chat_poll_seconds" in p else (10.0, 15.0),
        chat_watch_seconds=int(p.get("chat_watch_seconds", 180)),
        chat_settle_seconds=int(p.get("chat_settle_seconds", 5)),
    )

    m = _need(raw, "messages", "config")
    messages = Messages(
        dm1=_need(m, "dm1", "[messages]"),
        repeat_dm1=_need(m, "repeat_dm1", "[messages]"),
        followup_1=_need(m, "followup_1", "[messages]"),
        followup_2=_need(m, "followup_2", "[messages]"),
        public_replies=list(_need(m, "public_replies", "[messages]")),
        got_it=list(_need(m, "got_it", "[messages]")),
        email_subject=_need(m, "email_subject", "[messages]"),
        email_body=_need(m, "email_body", "[messages]"),
        ask_email_first=_flag(m, "ask_email_first"),
        sent_dm=_sent_dm(m),
    )
    _check_dm1(messages)

    reels = [_parse_reel(r, tz, m) for r in raw.get("reels", [])]

    e = raw.get("email", {})
    email = EmailConfig(
        enabled=bool(e.get("enabled", False)),
        from_address=e.get("from_address", ""),
        from_name=e.get("from_name", ""),
        delay_minutes=float(e.get("delay_minutes", 2)),
        smtp_host=e.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(e.get("smtp_port", 465)),
        after_got_it_seconds=_after_got_it(e),
    )
    if email.enabled and not email.from_address:
        raise ValueError("[email] enabled needs from_address")
    keys = [r.key for r in reels]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate reel keys")

    return Config(
        tz=tz,
        poll_seconds=int(_need(raw, "poll_seconds", "config")),
        mode=mode,
        db_path=db_path,
        jev_model=_need(jev, "model", "[jev]"),
        comment_threshold=float(_need(jev, "comment_threshold", "[jev]")),
        reply_threshold=float(_need(jev, "reply_threshold", "[jev]")),
        email_threshold=float(jev.get("email_threshold", 0.5)),
        jev_fallback_model=str(jev.get("fallback_model", "gpt-6-luna")),
        pacing=pacing,
        messages=messages,
        reels=reels,
        owner=_parse_owner(raw.get("owner")),
        api=api,
        messaging_node=node,
        email=email,
        replies=_parse_replies(raw.get("replies")),
        llm=_parse_llm(raw.get("llm")),
        prices={model: Price(float(p.get("per_call", 0)), float(p.get("per_m_input", 0)),
                             float(p.get("per_m_output", 0)))
                for model, p in raw.get("prices", {}).items()},
        milestones=parse_milestones(raw.get("milestones")),
    )
