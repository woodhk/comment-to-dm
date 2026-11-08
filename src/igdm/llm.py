"""AI-written replies (OpenAI or Gemini over plain HTTP) for when nothing in the bank fits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from igdm.config import Owner, ReelConfig

OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "confident": {"type": "boolean"},
        "new_promise": {"type": "boolean"},
    },
    "required": ["reply", "confident", "new_promise"],
    "additionalProperties": False,
}


def _intro(owner: Owner) -> str:
    who = owner.name + (", " + owner.about if owner.about else "")
    return (f"You write one Instagram DM reply as {who}.\n\n"
            f"Someone commented on {owner.name}'s reel to get free material, and {owner.name} "
            f"wants their email so {owner.name} can send it.\n")


_ASK = """
Write ONE short reply to their latest message that answers it and moves towards getting their email.

"""

_RULES = """- one or two sentences, no greetings or sign-offs.
- never use an em dash or en dash.
- never start a sentence with a label and a colon (like "quick update: ...").
- never write clipped fragment sentences (like "all done. sent over.").
- never sound like an assistant ("certainly", "i'd be happy to", "great question", "delve").

Rules:
- never promise anything that isn't listed above: no calls, no custom work, no deadlines, no other files.
  If your reply would promise something new, set new_promise to true.
- If you're unsure what they want, set confident to false.

Answer as JSON with keys reply, confident, new_promise."""


class LlmError(Exception):
    pass


@dataclass(frozen=True)
class LlmReply:
    text: str
    confident: bool
    new_promise: bool
    input_tokens: int
    output_tokens: int


class Writer(Protocol):
    provider: str
    model: str

    def write(self, thread: list[dict[str, str]], reel: ReelConfig,
              fix: list[str] | None = None) -> LlmReply: ...


def _system(reel: ReelConfig, owner: Owner) -> str:
    # Built by joining strings, never str.format: names and notes may contain curly brackets.
    return (_intro(owner)
            + f"The material: {reel.offer}\nReel keyword: {reel.keyword}\n{reel.ai_notes}\n"
            + _ASK
            + f"How {owner.name} writes:\n- {owner.style}\n"
            + _RULES)


def _user(thread: list[dict[str, str]], fix: list[str] | None) -> str:
    lines = ["The conversation so far, oldest first:",
             json.dumps(thread, ensure_ascii=False, indent=1)]
    if fix:
        lines += ["", "Your last attempt broke these rules, write it again without them:",
                  *[f"- {p}" for p in fix]]
    return "\n".join(lines)


def _parse(raw: str, input_tokens: Any, output_tokens: Any) -> LlmReply:
    try:
        d = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise LlmError("model answer was not JSON") from e
    if not (isinstance(d, dict) and isinstance(d.get("reply"), str)
            and isinstance(d.get("confident"), bool) and isinstance(d.get("new_promise"), bool)):
        raise LlmError("model answer is missing reply / confident / new_promise")
    return LlmReply(d["reply"], d["confident"], d["new_promise"],
                    int(input_tokens or 0), int(output_tokens or 0))


class _HttpWriter:
    provider = ""

    def __init__(self, api_key: str, model: str, *, owner: Owner,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self._key = api_key
        self.model = model
        self._owner = owner
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        try:
            r = self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LlmError(f"{self.provider} request failed: {type(e).__name__}") from e
        if not 200 <= r.status_code < 300:
            raise LlmError(f"{self.provider} returned HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise LlmError(f"{self.provider} returned a non-JSON body") from e


def output_text(d: dict) -> str:
    """The text of a Responses API answer (the first output_text of the first message)."""
    text = next((c.get("text") for item in d.get("output") or []
                 if isinstance(item, dict) and item.get("type") == "message"
                 for c in item.get("content") or []
                 if isinstance(c, dict) and c.get("type") == "output_text"), None)
    if not isinstance(text, str):
        raise LlmError("openai answer had no output text")
    return text


class OpenAIWriter(_HttpWriter):
    provider = "openai"

    def write(self, thread, reel, fix=None) -> LlmReply:
        body = {
            "model": self.model,
            "input": [{"role": "system", "content": _system(reel, self._owner)},
                      {"role": "user", "content": _user(thread, fix)}],
            "text": {"format": {"type": "json_schema", "name": "dm_reply", "schema": SCHEMA,
                                "strict": True}},
        }
        d = self._post(OPENAI_URL, body, {"Authorization": f"Bearer {self._key}"})
        text = output_text(d)
        usage = d.get("usage") or {}
        return _parse(text, usage.get("input_tokens"), usage.get("output_tokens"))


class GeminiWriter(_HttpWriter):
    provider = "gemini"

    def write(self, thread, reel, fix=None) -> LlmReply:
        schema = {k: v for k, v in SCHEMA.items() if k != "additionalProperties"}
        body = {
            "systemInstruction": {"parts": [{"text": _system(reel, self._owner)}]},
            "contents": [{"role": "user", "parts": [{"text": _user(thread, fix)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
        }
        d = self._post(GEMINI_URL.format(model=self.model), body, {"x-goog-api-key": self._key})
        try:
            text = d["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LlmError("gemini answer had no text") from e
        usage = d.get("usageMetadata") or {}
        return _parse(text, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
