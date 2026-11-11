"""Log every AI call so the dashboard can show what the models cost."""

from __future__ import annotations

from typing import Any

from igdm.config import Price


def cost(row: dict[str, Any], prices: dict[str, Price]) -> float | None:
    """USD for one usage row, from today's prices (None when the model has no price set)."""
    if row.get("usd_override") is not None:
        return float(row["usd_override"])
    p = prices.get(row["model"])
    if p is None:
        return None
    return (row["calls"] * p.per_call + row["input_tokens"] / 1e6 * p.per_m_input
            + row["output_tokens"] / 1e6 * p.per_m_output)


class MeteredClassifier:
    """Wraps a Classifier; one usage row per successful classifier call."""

    def __init__(self, inner: Any, db: Any):
        self._inner = inner
        self._db = db
        self.model = getattr(inner, "model", "jev")

    def _log(self, purpose: str) -> None:
        tokens = getattr(self._inner, "last_usage", (0, 0))
        self._db.log_usage(getattr(self._inner, "provider", "typesafe"), self.model, purpose,
                           input_tokens=tokens[0], output_tokens=tokens[1])

    def comment_wants_material(self, comment_text, reel):
        v = self._inner.comment_wants_material(comment_text, reel)
        self._log("comment")
        return v

    def comment_is_cheeky(self, comment_text, reel):
        v = self._inner.comment_is_cheeky(comment_text, reel)
        self._log("cheeky")
        return v

    def reply_intent(self, bot_message, reply_text):
        v = self._inner.reply_intent(bot_message, reply_text)
        self._log("intent")
        return v

    def email_looks_real(self, email, their_message):
        v = self._inner.email_looks_real(email, their_message)
        self._log("email")
        return v

    def route_reply(self, thread, reply_text, options):
        v = self._inner.route_reply(thread, reply_text, options)
        self._log("route")
        return v


class MeteredWriter:
    """Wraps a Writer; logs the tokens of every successful call."""

    def __init__(self, inner: Any, db: Any):
        self._inner = inner
        self._db = db
        self.provider = inner.provider
        self.model = inner.model

    def write(self, thread, reel, fix=None):
        r = self._inner.write(thread, reel, fix)
        self._db.log_usage(self.provider, self.model, "reply",
                           input_tokens=r.input_tokens, output_tokens=r.output_tokens)
        return r
