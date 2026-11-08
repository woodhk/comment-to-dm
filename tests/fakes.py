"""In-memory test double for InstagramAPI.

Usage:
    fake = FakeInstagram("me1", "alex", now=some_utc_datetime)
    fake.comments["media1"] = [Comment(...)]
    fake.fail_private["c1"] = InstagramError(400, 100, "comment deleted")
    fake.fail_public["c1"] = InstagramError(400, 100, "cannot reply")
    fake.display_names["42"] = "Sam"
    fake.add_user_message(igsid, "yes!", at)

Rules:
- `.sent` records writes: ("private", comment_id, text), ("public", comment_id, text),
  ("dm", igsid, text). A failing private/public reply is NOT recorded in `.sent`.
- `.private_attempts` records every send_private_reply comment_id, including failures.
- `fail_private` / `fail_public` values (any exception) are raised for that comment id.
- Private reply recipient igsid is deterministic: "igsid-" + (comment.from_id or comment.username).
- Bot sends are appended to that person's conversation as messages from own_id,
  with created_at = `fake.now` (tests set/advance it). Echo id equals the returned
  message id, unless `echo_ids_differ` is True (then the echo gets "echo-<id>").
- One conversation per person, id "conv-<igsid>", participants [own_id, igsid].
- list_conversations returns conversations whose latest message time >= updated_since,
  newest first.
- list_messages returns at most the 20 newest messages (like the real client), oldest first.
- `fail_dm[text]` (any exception) makes send_message raise for that exact text; nothing is
  sent or echoed. `fail_messages[conversation_id]` makes list_messages raise.
- `comment_replies[comment_id]` lists public replies under a comment (bot public replies are
  appended too, from own_username/own_id). `fail_list_replies[comment_id]` makes
  list_comment_replies raise.
- `followers` is what follower_count returns (`fail_followers` makes it raise); `posts` are the
  feed posts list_posts returns, newest first, back to `since`.
- `add_own_message(igsid, text, at)` adds an outgoing message the bot didn't send (Alex typing
  by hand in the Instagram app).
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from igdm.config import Config, load_config
from igdm.instagram import Comment, Conversation, InstagramError, Media, Message
from igdm.jev import JevError


ROOT = Path(__file__).resolve().parent.parent
TEST_CUTOFF = datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)


def repo_config(path: Path | None = None) -> Config:
    """The repo's config.toml with a fixed cutoff and AI replies off, so tests never depend on
    the live settings (tests of the AI reply step switch it on themselves)."""
    cfg = load_config(path or ROOT / "tests" / "data" / "config.toml")
    return replace(cfg, reels=[replace(r, handle_comments_after=TEST_CUTOFF) for r in cfg.reels],
                   replies=replace(cfg.replies, enabled=False))


class FakeInstagram:
    def __init__(self, own_id: str = "me1", own_username: str = "alex", now: datetime | None = None):
        self.own_id = own_id
        self.own_username = own_username
        self.now = now or datetime(2026, 11, 3, tzinfo=timezone.utc)
        self.comments: dict[str, list[Comment]] = {}
        self.media_ids: dict[str, str] = {}  # shortcode -> media_id
        self.media_lookups: list[str] = []  # every find_media_id shortcode, in order
        self.media_posted: dict[str, datetime] = {}  # shortcode -> when it went up
        self.fail_private: dict[str, Exception] = {}
        self.fail_public: dict[str, Exception] = {}
        self.private_attempts: list[str] = []
        self.display_names: dict[str, str] = {}
        self.usernames: dict[str, str] = {}
        self.echo_ids_differ = False
        self.fail_dm: dict[str, Exception] = {}
        self.fail_messages: dict[str, Exception] = {}
        self.comment_replies: dict[str, list[Comment]] = {}
        self.fail_list_replies: dict[str, Exception] = {}
        self.sent: list[tuple[str, ...]] = []
        self.actions: list[tuple[str, str, int]] = []  # (igsid, action, messages sent before it)
        self.fail_actions: Exception | None = None
        self._messages: dict[str, list[Message]] = {}  # igsid -> messages
        self._ids = itertools.count(1)
        self.followers = 0
        self.posts: list[Media] = []  # feed posts, any order
        self.fail_followers: Exception | None = None
        self.post_reads: list[datetime | None] = []  # every list_posts `since`, in order

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def _conv_id(self, igsid: str) -> str:
        return f"conv-{igsid}"

    def _echo(self, igsid: str, text: str) -> str:
        mid = self._next_id("mid")
        echo_id = f"echo-{mid}" if self.echo_ids_differ else mid
        self._messages.setdefault(igsid, []).append(Message(echo_id, self.own_id, text, self.now))
        return mid

    def _find_comment(self, comment_id: str) -> Comment | None:
        for cs in self.comments.values():
            for c in cs:
                if c.id == comment_id:
                    return c
        return None

    def me(self) -> tuple[str, str]:
        return self.own_id, self.own_username

    def find_media_id(self, shortcode: str) -> str | None:
        self.media_lookups.append(shortcode)
        return self.media_ids.get(shortcode)

    def find_media(self, shortcode: str) -> Media | None:
        mid = self.find_media_id(shortcode)
        return Media(mid, self.media_posted.get(shortcode)) if mid else None

    def follower_count(self) -> int:
        if self.fail_followers:
            raise self.fail_followers
        return self.followers

    def list_posts(self, since: datetime | None = None) -> list[Media]:
        self.post_reads.append(since)
        floor = datetime.min.replace(tzinfo=timezone.utc)
        posts = sorted(self.posts, key=lambda m: m.posted_at or floor, reverse=True)
        return [m for m in posts if since is None or m.posted_at is None or m.posted_at >= since]

    def list_comments(self, media_id: str) -> list[Comment]:
        return list(self.comments.get(media_id, []))

    def send_private_reply(self, own_id: str, comment_id: str, text: str) -> tuple[str, str]:
        self.private_attempts.append(comment_id)
        if comment_id in self.fail_private:
            raise self.fail_private[comment_id]
        c = self._find_comment(comment_id)
        who = (c.from_id or c.username) if c else comment_id
        igsid = f"igsid-{who}"
        mid = self._echo(igsid, text)
        self.sent.append(("private", comment_id, text))
        return igsid, mid

    def reply_to_comment(self, comment_id: str, text: str) -> str:
        if comment_id in self.fail_public:
            raise self.fail_public[comment_id]
        self.sent.append(("public", comment_id, text))
        rid = self._next_id("reply")
        self.comment_replies.setdefault(comment_id, []).append(
            Comment(rid, text, self.own_username, self.own_id, self.now))
        return rid

    def list_comment_replies(self, comment_id: str) -> list[Comment]:
        if comment_id in self.fail_list_replies:
            raise self.fail_list_replies[comment_id]
        return list(self.comment_replies.get(comment_id, []))

    def send_message(self, own_id: str, igsid: str, text: str) -> str:
        if text in self.fail_dm:
            raise self.fail_dm[text]
        mid = self._echo(igsid, text)
        self.sent.append(("dm", igsid, text))
        return mid

    def send_action(self, own_id: str, igsid: str, action: str) -> None:
        if self.fail_actions:
            raise self.fail_actions
        self.actions.append((igsid, action, len(self.sent)))

    def add_user_message(self, igsid: str, text: str, at: datetime) -> str:
        mid = self._next_id("umid")
        self._messages.setdefault(igsid, []).append(Message(mid, igsid, text, at))
        return mid

    def add_own_message(self, igsid: str, text: str, at: datetime) -> str:
        mid = self._next_id("manual")
        self._messages.setdefault(igsid, []).append(Message(mid, self.own_id, text, at))
        return mid

    def list_conversations(self, own_id: str, updated_since: datetime) -> list[Conversation]:
        out = []
        for igsid, msgs in self._messages.items():
            latest = max(m.created_at for m in msgs)
            if latest >= updated_since:
                names = {igsid: self.usernames[igsid]} if igsid in self.usernames else {}
                out.append(Conversation(self._conv_id(igsid), latest, [self.own_id, igsid], names))
        return sorted(out, key=lambda c: c.updated_at, reverse=True)

    def list_messages(self, conversation_id: str) -> list[Message]:
        if conversation_id in self.fail_messages:
            raise self.fail_messages[conversation_id]
        igsid = conversation_id.removeprefix("conv-")
        msgs = sorted(self._messages.get(igsid, []), key=lambda m: m.created_at)
        return msgs[-20:]  # the real client asks for messages.limit(20)

    def get_display_name(self, user_id: str) -> str | None:
        return self.display_names.get(user_id)

    def get_username(self, user_id: str) -> str | None:
        return self.usernames.get(user_id)

    def refresh_token(self) -> str:
        return "fake-token"


class FakeClassifier:
    """Test double for igdm.jev.Classifier.

    FakeClassifier(comment_probs, reply_probs, default_comment=0.0)
    - comment_wants_material(text, reel) -> comment_probs.get(text, default_comment)
    - reply_intent(bot_message, reply_text) -> reply_probs[reply_text] (a copy), or
      {"yes": 0, "no": 0, "other": 1} when the reply text is unknown.
    - email_looks_real(email, their_message) -> email_probs.get(email, 1.0) (real unless set).
    - Texts in `.raise_on` raise JevError (comment text, reply text or email address).
    - `.calls` records ("comment", text) / ("reply", reply_text) / ("email", email).
    """

    def __init__(self, comment_probs=None, reply_probs=None, default_comment: float = 0.0):
        self.comment_probs = dict(comment_probs or {})
        self.reply_probs = dict(reply_probs or {})
        self.default_comment = default_comment
        self.raise_on: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.route_probs: dict[str, dict[str, float]] = {}
        self.email_probs: dict[str, float] = {}
        self.route_options: list[dict[str, str]] = []
        self.route_threads: list[list[dict[str, str]]] = []

    def comment_wants_material(self, comment_text: str, reel) -> float:
        self.calls.append(("comment", comment_text))
        if comment_text in self.raise_on:
            raise JevError("fake jev failure")
        return self.comment_probs.get(comment_text, self.default_comment)

    def reply_intent(self, bot_message: str, reply_text: str) -> dict[str, float]:
        self.calls.append(("reply", reply_text))
        if reply_text in self.raise_on:
            raise JevError("fake jev failure")
        return dict(self.reply_probs.get(reply_text, {"yes": 0.0, "no": 0.0, "other": 1.0}))

    def email_looks_real(self, email: str, their_message: str) -> float:
        self.calls.append(("email", email))
        if email in self.raise_on:
            raise JevError("fake jev failure")
        return self.email_probs.get(email, 1.0)

    def route_reply(self, thread, reply_text: str, options: dict[str, str]) -> dict[str, float]:
        """route_probs[reply_text] (missing options are 0); unknown text -> {"custom": 1}."""
        self.calls.append(("route", reply_text))
        self.route_options.append(dict(options))
        self.route_threads.append(list(thread))
        if reply_text in self.raise_on:
            raise JevError("fake jev failure")
        given = self.route_probs.get(reply_text, {"custom": 1.0})
        return {k: float(given.get(k, 0.0)) for k in options}


class FakeMailer:
    """Test double for igdm.mailer.Mailer: `.sent` records (to, subject, body);
    `.fail[address]` (an exception) is raised for that recipient, nothing recorded."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.fail: dict[str, Exception] = {}

    def send(self, to: str, subject: str, body: str) -> None:
        if to in self.fail:
            raise self.fail[to]
        self.sent.append((to, subject, body))


class FakeWriter:
    """Test double for igdm.llm.Writer. Returns (or raises) `replies` in order;
    `.calls` records the `fix` list passed on each call."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls: list[list[str] | None] = []

    def write(self, thread, reel, fix=None):
        self.calls.append(fix)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
