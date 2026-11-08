"""Jev (TypeSafe System One) classifier over plain HTTP."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from igdm.config import ReelConfig
from igdm.routes import BUILTINS  # noqa: F401 - re-exported for callers

URL = "https://api.typesafe.ai/v1/systemone"
TIMEOUT = 20.0

COMMENT_INSTRUCTIONS = (
    "Is the commenter asking to receive the material offered in the reel? Commenting the keyword "
    "(even misspelled, in another language, or with extra words), asking for it ('me', 'send pls', "
    "'want this'), or an emoji that signals 'me too' counts as yes. Criticism, jokes, questions about "
    "something else, or general reactions count as no."
)
COMMENT_CRITERIA = {
    "true": "The commenter wants the material sent to them.",
    "false": "The comment is about something else.",
}
INTENT_INSTRUCTIONS = "How does their reply answer our message?"
INTENT_CRITERIA = {
    "yes": "They agree or still want it (yes, sure, please, of course, an email address, a thumbs up).",
    "no": "They decline or no longer want it.",
    "other": "Anything else: a question, a different topic, or unclear.",
}
INTENT_KEYS = ("yes", "no", "other")
EMAIL_INSTRUCTIONS = (
    "Is this an email address the person really uses? Joke, placeholder or obviously made-up "
    "addresses (like email@totallyreal.com, test@test.com, no@thanks.com, asdf@asdf.com) count as "
    "no. Ordinary personal or work addresses, even unusual-looking ones, count as yes."
)
EMAIL_CRITERIA = {
    "true": "A real address they'd actually read.",
    "false": "A joke, placeholder or made-up address.",
}
def route_instructions(owner_name: str) -> str:
    return (
        owner_name + " offered someone free material from their Instagram reel and wants their "
        "email to send it. Which option best describes their latest reply, given the "
        "conversation so far?"
    )


class JevError(Exception):
    pass


class Classifier(Protocol):
    def comment_wants_material(self, comment_text: str, reel: ReelConfig) -> float: ...

    def reply_intent(self, bot_message: str, reply_text: str) -> dict[str, float]: ...

    def email_looks_real(self, email: str, their_message: str) -> float: ...

    def route_reply(self, thread: list[dict[str, str]], reply_text: str,
                    options: dict[str, str]) -> dict[str, float]: ...


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class JevClassifier:
    def __init__(self, api_key: str, model: str = "jev-latest",
                 transport: httpx.BaseTransport | None = None, *, owner_name: str):
        self._key = api_key
        self.model = model
        self._owner_name = owner_name
        self._client = httpx.Client(transport=transport, timeout=TIMEOUT)

    def _ask(self, state: dict, qid: str, question: dict) -> dict:
        body = {"model": self.model, "state": state, "questions": {qid: question}}
        try:
            r = self._client.post(URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:  # transport errors and timeouts
            raise JevError(f"jev request failed: {type(e).__name__}") from e
        if not 200 <= r.status_code < 300:
            raise JevError(f"jev returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:
            raise JevError("jev returned non-JSON body") from e
        answers = data.get("answers") if isinstance(data, dict) else None
        answer = answers.get(qid) if isinstance(answers, dict) else None
        if not isinstance(answer, dict):
            raise JevError(f"jev response missing answer {qid!r}")
        return answer

    def comment_wants_material(self, comment_text: str, reel: ReelConfig) -> float:
        state = {
            "reel": {
                "call_to_action": f'Comment "{reel.keyword}" and I\'ll DM it to you',
                "offer": reel.offer,
            },
            "comment": comment_text,
        }
        q = {"type": "noul", "instructions": COMMENT_INSTRUCTIONS, "criteria": COMMENT_CRITERIA}
        v = self._ask(state, "wants_material", q).get("noul")
        if not _num(v):
            raise JevError("jev noul value missing or not a number")
        return float(v)

    def email_looks_real(self, email: str, their_message: str) -> float:
        """The chance the address is one they really use (low for jokes and placeholders)."""
        state = {"email": email, "their_message": their_message}
        q = {"type": "noul", "instructions": EMAIL_INSTRUCTIONS, "criteria": EMAIL_CRITERIA}
        v = self._ask(state, "email_real", q).get("noul")
        if not _num(v):
            raise JevError("jev noul value missing or not a number")
        return float(v)

    def reply_intent(self, bot_message: str, reply_text: str) -> dict[str, float]:
        state = {"our_message": bot_message, "their_reply": reply_text}
        q = {"type": "choice", "instructions": INTENT_INSTRUCTIONS, "criteria": INTENT_CRITERIA}
        probs = self._ask(state, "intent", q).get("probabilities")
        if not isinstance(probs, dict) or any(not _num(probs.get(k)) for k in INTENT_KEYS):
            raise JevError("jev probabilities missing yes/no/other")
        return {k: float(probs[k]) for k in INTENT_KEYS}

    def route_reply(self, thread: list[dict[str, str]], reply_text: str,
                    options: dict[str, str]) -> dict[str, float]:
        state = {"conversation": thread, "their_latest_reply": reply_text}
        q = {"type": "choice", "instructions": route_instructions(self._owner_name), "criteria": dict(options)}
        probs = self._ask(state, "route", q).get("probabilities")
        if not isinstance(probs, dict) or any(not _num(probs.get(k)) for k in options):
            raise JevError("jev probabilities missing route options")
        return {k: float(probs[k]) for k in options}
