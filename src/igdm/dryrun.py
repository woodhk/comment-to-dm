"""Dry-run wrapper: reads go through, writes are only logged."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from igdm.instagram import Comment, Conversation, InstagramAPI, Message

log = logging.getLogger(__name__)


def _fake_id() -> str:
    return f"dry-{uuid.uuid4()}"


class DryRunInstagram:
    def __init__(self, inner: InstagramAPI):
        self._inner = inner

    def me(self) -> tuple[str, str]:
        return self._inner.me()

    def find_media_id(self, shortcode: str) -> str | None:
        return self._inner.find_media_id(shortcode)

    def find_media(self, shortcode: str):
        return self._inner.find_media(shortcode)

    def follower_count(self) -> int:
        return self._inner.follower_count()

    def list_posts(self, since: datetime | None = None):
        return self._inner.list_posts(since)

    def list_comments(self, media_id: str) -> list[Comment]:
        return self._inner.list_comments(media_id)

    def list_comment_replies(self, comment_id: str) -> list[Comment]:
        return self._inner.list_comment_replies(comment_id)

    def list_conversations(self, own_id: str, updated_since: datetime) -> list[Conversation]:
        return self._inner.list_conversations(own_id, updated_since)

    def list_messages(self, conversation_id: str) -> list[Message]:
        return self._inner.list_messages(conversation_id)

    def get_display_name(self, user_id: str) -> str | None:
        return self._inner.get_display_name(user_id)

    def get_username(self, user_id: str) -> str | None:
        return self._inner.get_username(user_id)

    def set_token(self, token: str) -> None:
        self._inner.set_token(token)

    def refresh_token(self) -> str:
        return self._inner.refresh_token()

    def send_private_reply(self, own_id: str, comment_id: str, text: str) -> tuple[str, str]:
        log.info("DRY-RUN would send private reply to comment %s: %r", comment_id, text)
        return f"dry-igsid-{comment_id}", _fake_id()

    def reply_to_comment(self, comment_id: str, text: str) -> str:
        log.info("DRY-RUN would send public reply to comment %s: %r", comment_id, text)
        return _fake_id()

    def send_action(self, own_id: str, igsid: str, action: str) -> None:
        log.info("DRY-RUN would send %s to %s", action, igsid)

    def send_message(self, own_id: str, igsid: str, text: str) -> str:
        log.info("DRY-RUN would send DM to %s: %r", igsid, text)
        return _fake_id()
