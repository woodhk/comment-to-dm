"""Instagram Graph API client (official API only).

Two official setups share one client:
- Instagram API with Instagram Login: graph.instagram.com, a 60-day user token, `/me`.
- Instagram API with Facebook Login: graph.facebook.com, a never-expiring Page token,
  explicit IG account and Page ids (pass `ig_id` and `page_id`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

# 80002 / 80006: Meta's business-use throttles for Instagram and Messenger (sent as HTTP 400)
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, 80002, 80006})
# Failures after a request was handed to Instagram: it may have acted on it.
_LOST_ANSWER = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError,
                httpx.RemoteProtocolError)
COMMENT_FIELDS = "id,text,timestamp,username,from"
TS_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
IG_GRAPH = "https://graph.instagram.com/v26.0"
FB_GRAPH = "https://graph.facebook.com/v26.0"
MESSAGING_NODES = ("page", "ig")


def parse_ts(s: str) -> datetime:
    """Parse Instagram's `2026-11-03T01:00:00+0000` into a tz-aware datetime."""
    return datetime.strptime(s, TS_FORMAT)


@dataclass(frozen=True)
class Media:
    id: str
    posted_at: datetime | None  # when the post went up, if Instagram said


@dataclass(frozen=True)
class Comment:
    id: str
    text: str
    username: str
    from_id: str | None
    timestamp: datetime


@dataclass(frozen=True)
class Message:
    id: str
    from_id: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class Conversation:
    id: str
    updated_at: datetime
    participant_ids: list[str]
    usernames: dict[str, str] = field(default_factory=dict)  # participant id -> @handle


class InstagramError(Exception):
    def __init__(self, status: int, code: int | None, message: str):
        super().__init__(f"instagram error status={status} code={code}: {message}")
        self.status = status
        self.code = code
        self.message = message


_BLOCKED_MARKERS = ("api access blocked", "cannot call api for app")


def is_access_blocked(err: BaseException) -> bool:
    """Meta switched the app's API access off (e.g. the account needs a verification check)."""
    return isinstance(err, InstagramError) and any(
        m in str(err.message).casefold() for m in _BLOCKED_MARKERS)


class RateLimited(InstagramError):
    """HTTP 429 or Graph codes 4, 17, 32, 613."""


class MaybeSent(InstagramError):
    """A write (POST) whose request went out but whose answer never came back.

    Instagram may have acted on it, so the caller must never retry it.
    """


class InstagramAPI(Protocol):
    def me(self) -> tuple[str, str]: ...
    def find_media_id(self, shortcode: str) -> str | None: ...
    def find_media(self, shortcode: str) -> Media | None: ...
    def list_comments(self, media_id: str) -> list[Comment]: ...
    def list_comment_replies(self, comment_id: str) -> list[Comment]: ...
    def send_private_reply(self, own_id: str, comment_id: str, text: str) -> tuple[str, str]: ...
    def reply_to_comment(self, comment_id: str, text: str) -> str: ...
    def send_message(self, own_id: str, igsid: str, text: str) -> str: ...
    def send_action(self, own_id: str, igsid: str, action: str) -> None: ...
    def list_conversations(self, own_id: str, updated_since: datetime) -> list[Conversation]: ...
    def list_messages(self, conversation_id: str) -> list[Message]: ...
    def get_display_name(self, user_id: str) -> str | None: ...
    def get_username(self, user_id: str) -> str | None: ...
    def refresh_token(self) -> str: ...


SENDER_ACTIONS = ("mark_seen", "typing_on", "typing_off")


class GraphInstagram:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = IG_GRAPH,
        ig_id: str | None = None,
        page_id: str | None = None,
        messaging_node: str = "page",
        transport: httpx.BaseTransport | None = None,
    ):
        """Instagram Login: just a token. Facebook Login: also ig_id and page_id.

        With Facebook Login, DMs go to POST /{node}/messages where node is the Page
        ("page") or the IG account ("ig"); conversations are always listed on the Page.
        """
        if messaging_node not in MESSAGING_NODES:
            raise ValueError(f"messaging_node must be one of {MESSAGING_NODES}")
        if ig_id is not None and not page_id:
            raise ValueError("facebook login needs both ig_id and page_id")
        self._token = token
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=20.0)
        self._ig_id = ig_id
        self._page_id = page_id
        self._node = messaging_node
        self._username: str | None = None

    @property
    def facebook_login(self) -> bool:
        return self._ig_id is not None

    def set_token(self, token: str) -> None:
        self._token = token

    def _request(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        if not url.startswith("http"):
            url = f"{self._base}/{url.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = self._http.request(method, url, headers=headers, **kw)
        except httpx.TransportError as e:
            cls = MaybeSent if method == "POST" and isinstance(e, _LOST_ANSWER) else InstagramError
            raise cls(0, None, f"{type(e).__name__}: {e}") from None
        if resp.status_code >= 400:
            self._raise(resp)
        try:
            return resp.json()
        except ValueError:
            raise InstagramError(resp.status_code, None, f"non-JSON response: {resp.text[:200]}") from None

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        code: int | None = None
        message = resp.text[:500]
        try:
            err = resp.json().get("error")
            if isinstance(err, dict):
                code = err.get("code")
                message = str(err.get("message", message))
        except (ValueError, AttributeError):
            pass
        cls = RateLimited if resp.status_code == 429 or code in RATE_LIMIT_CODES else InstagramError
        raise cls(resp.status_code, code, message)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A raw GET (used by `igdm connect`)."""
        return self._request("GET", path, params=params)

    def _pages(self, path: str, params: dict[str, Any]):
        """Yield items from a paged collection, following absolute paging.next URLs."""
        data = self._request("GET", path, params=params)
        while True:
            yield from data.get("data", [])
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                return
            data = self._request("GET", nxt)

    def paged(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Every item of a paged collection (used by `igdm connect`)."""
        return list(self._pages(path, params))

    def me(self) -> tuple[str, str]:
        if self.facebook_login:
            # /me with a Page token is the Page, so ask the IG account directly
            d = self._request("GET", self._ig_id, params={"fields": "username"})
            self._username = d["username"]
            return self._ig_id, self._username
        d = self._request("GET", "me", params={"fields": "user_id,username"})
        return str(d.get("user_id") or d["id"]), d["username"]

    def _own_username(self) -> str:
        if self._username is None:
            self.me()
        return self._username or ""

    def _own(self, who: dict[str, Any]) -> str:
        """Facebook Login: map any form of our own account (IG id, Page id, own username)
        to the IG id the engine knows us by. Instagram Login: the raw id, as before."""
        uid = str(who.get("id", ""))
        if not self.facebook_login:
            return uid
        if uid and uid in (self._ig_id, self._page_id):
            return self._ig_id
        name = who.get("username")
        if name and name.casefold() == self._own_username().casefold():
            return self._ig_id
        return uid

    def _messages_node(self, own_id: str) -> str:
        if not self.facebook_login:
            return own_id
        return self._page_id if self._node == "page" else self._ig_id

    def find_media_id(self, shortcode: str) -> str | None:
        m = self.find_media(shortcode)
        return m.id if m else None

    def find_media(self, shortcode: str) -> Media | None:
        """One of the account's own posts by its link's shortcode, with when it went up."""
        media = f"{self._ig_id}/media" if self.facebook_login else "me/media"
        for item in self._pages(media, {"fields": "id,permalink,timestamp", "limit": 50}):
            if f"/{shortcode}/" in item.get("permalink", ""):
                ts = item.get("timestamp")
                return Media(item["id"], parse_ts(ts) if ts else None)
        return None

    @staticmethod
    def _comment(c: dict[str, Any]) -> Comment:
        frm = c.get("from") or {}
        return Comment(
            id=c["id"],
            text=c.get("text", ""),
            username=c.get("username") or frm.get("username", ""),
            from_id=frm.get("id"),
            timestamp=parse_ts(c["timestamp"]),
        )

    def list_comments(self, media_id: str) -> list[Comment]:
        params = {"fields": COMMENT_FIELDS, "limit": 100}
        return [self._comment(c) for c in self._pages(f"{media_id}/comments", params)]

    def list_comment_replies(self, comment_id: str) -> list[Comment]:
        params = {"fields": COMMENT_FIELDS, "limit": 50}
        return [self._comment(c) for c in self._pages(f"{comment_id}/replies", params)]

    def send_private_reply(self, own_id: str, comment_id: str, text: str) -> tuple[str, str]:
        d = self._request(
            "POST", f"{self._messages_node(own_id)}/messages",
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text}},
        )
        return d["recipient_id"], d["message_id"]

    def reply_to_comment(self, comment_id: str, text: str) -> str:
        return self._request("POST", f"{comment_id}/replies", data={"message": text})["id"]

    def send_message(self, own_id: str, igsid: str, text: str) -> str:
        d = self._request(
            "POST", f"{self._messages_node(own_id)}/messages",
            json={"recipient": {"id": igsid}, "message": {"text": text}},
        )
        return d["message_id"]

    def send_action(self, own_id: str, igsid: str, action: str) -> None:
        """Mark their latest message Seen ("mark_seen") or show typing… ("typing_on")."""
        if action not in SENDER_ACTIONS:
            raise ValueError(f"sender action must be one of {SENDER_ACTIONS}")
        self._request("POST", f"{self._messages_node(own_id)}/messages",
                      json={"recipient": {"id": igsid}, "sender_action": action})

    def list_conversations(self, own_id: str, updated_since: datetime) -> list[Conversation]:
        params = {"platform": "instagram", "fields": "id,updated_time,participants", "limit": 50}
        out = []
        node = self._page_id if self.facebook_login else own_id
        for c in self._pages(f"{node}/conversations", params):  # newest first
            updated = parse_ts(c["updated_time"])
            if updated < updated_since:
                break
            people = (c.get("participants") or {}).get("data", [])
            ids = [self._own(p) for p in people]
            names = {self._own(p): p["username"] for p in people if p.get("username")}
            out.append(Conversation(c["id"], updated, ids, names))
        return out

    def list_messages(self, conversation_id: str) -> list[Message]:
        d = self._request(
            "GET", conversation_id,
            params={"fields": "messages.limit(20){id,created_time,from,message}"},
        )
        msgs = [
            Message(
                id=m["id"],
                from_id=self._own(m.get("from") or {}),
                text=m.get("message") or "",
                created_at=parse_ts(m["created_time"]),
            )
            for m in (d.get("messages") or {}).get("data", [])
        ]
        msgs.sort(key=lambda m: m.created_at)  # stable; API returns newest first
        return msgs

    def get_display_name(self, user_id: str) -> str | None:
        try:
            return self._request("GET", user_id, params={"fields": "name"}).get("name") or None
        except InstagramError:
            return None

    def get_username(self, user_id: str) -> str | None:
        try:
            return self._request("GET", user_id, params={"fields": "username"}).get("username") or None
        except InstagramError:
            return None

    def refresh_token(self) -> str:
        if self.facebook_login:
            raise InstagramError(0, None, "Facebook Login Page tokens do not expire; nothing to refresh")
        d = self._request(
            "GET", "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": self._token},
        )
        return d["access_token"]
