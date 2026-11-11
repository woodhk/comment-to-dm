"""Spots requests with OpenAI when there is no Jev key: the same questions, one call each."""

from __future__ import annotations

import json
from typing import Any

import httpx

from igdm.config import ReelConfig
from igdm.jev import (
    CHEEKY_CRITERIA,
    CHEEKY_INSTRUCTIONS,
    COMMENT_CRITERIA,
    COMMENT_INSTRUCTIONS,
    EMAIL_CRITERIA,
    EMAIL_INSTRUCTIONS,
    INTENT_CRITERIA,
    INTENT_INSTRUCTIONS,
    INTENT_KEYS,
    JevError,
    _num,
    comment_state,
    route_instructions,
)
from igdm.llm import OPENAI_URL, LlmError, output_text

TIMEOUT = 20.0


class OpenAIClassifier:
    provider = "openai"

    def __init__(self, api_key: str, model: str, *, owner_name: str,
                 transport: httpx.BaseTransport | None = None, timeout: float = TIMEOUT):
        self._key = api_key
        self.model = model
        self._owner_name = owner_name
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self.last_usage: tuple[int, int] = (0, 0)

    def _system(self, instructions: str, criteria: dict[str, str]) -> str:
        # Joined, never str.format: names and criteria may contain curly brackets.
        options = "\n".join(f"- {k}: {v}" for k, v in criteria.items())
        return (f"You classify one Instagram message for {self._owner_name}, a creator who gives "
                "away free material to people who comment on their reels.\n\n"
                + instructions + "\n\nOptions:\n" + options
                + "\n\nGive every option a probability from 0 to 1. They must add up to 1. "
                "If it's unclear, spread the probability.")

    def _ask(self, state: dict, instructions: str, criteria: dict[str, str]) -> dict[str, float]:
        keys = list(criteria)
        body = {
            "model": self.model,
            "input": [{"role": "system", "content": self._system(instructions, criteria)},
                      {"role": "user", "content": json.dumps(state, ensure_ascii=False)}],
            "text": {"format": {
                "type": "json_schema", "name": "answer", "strict": True,
                "schema": {"type": "object",
                           "properties": {k: {"type": "number"} for k in keys},
                           "required": keys, "additionalProperties": False}}},
        }
        try:
            r = self._client.post(OPENAI_URL, json=body,
                                  headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:  # transport errors and timeouts
            raise JevError(f"openai request failed: {type(e).__name__}") from e
        if not 200 <= r.status_code < 300:
            raise JevError(f"openai returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise JevError("openai returned a non-JSON body") from e
        if not isinstance(data, dict):
            raise JevError("openai returned an unexpected body")
        try:
            answer = json.loads(output_text(data))
        except LlmError as e:
            raise JevError(str(e)) from e
        except ValueError as e:
            raise JevError("openai answer was not JSON") from e
        probs = self._normalise(answer, keys)
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        self.last_usage = (_count(usage.get("input_tokens")), _count(usage.get("output_tokens")))
        return probs

    @staticmethod
    def _normalise(answer: Any, keys: list[str]) -> dict[str, float]:
        if not isinstance(answer, dict) or any(not _num(answer.get(k)) for k in keys):
            raise JevError("openai answer is missing an option or has a non-number")
        values = {k: float(answer[k]) for k in keys}
        total = sum(values.values())
        if any(v < 0 for v in values.values()) or not total > 0 or total == float("inf"):
            raise JevError("openai probabilities must be 0 or more and add up to more than 0")
        return {k: v / total for k, v in values.items()}

    def comment_wants_material(self, comment_text: str, reel: ReelConfig) -> float:
        return self._ask(comment_state(comment_text, reel), COMMENT_INSTRUCTIONS,
                         COMMENT_CRITERIA)["true"]

    def comment_is_cheeky(self, comment_text: str, reel: ReelConfig) -> float:
        """The chance the comment adds an insult, a joke, bot instructions or an unrelated ask."""
        return self._ask(comment_state(comment_text, reel), CHEEKY_INSTRUCTIONS,
                         CHEEKY_CRITERIA)["true"]

    def email_looks_real(self, email: str, their_message: str) -> float:
        """The chance the address is one they really use (low for jokes and placeholders)."""
        state = {"email": email, "their_message": their_message}
        return self._ask(state, EMAIL_INSTRUCTIONS, EMAIL_CRITERIA)["true"]

    def reply_intent(self, bot_message: str, reply_text: str) -> dict[str, float]:
        state = {"our_message": bot_message, "their_reply": reply_text}
        probs = self._ask(state, INTENT_INSTRUCTIONS, INTENT_CRITERIA)
        return {k: probs[k] for k in INTENT_KEYS}

    def route_reply(self, thread: list[dict[str, str]], reply_text: str,
                    options: dict[str, str]) -> dict[str, float]:
        state = {"conversation": thread, "their_latest_reply": reply_text}
        return self._ask(state, route_instructions(self._owner_name), dict(options))


def _count(v: Any) -> int:
    return int(v) if _num(v) else 0
