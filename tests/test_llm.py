import json

import httpx
import pytest
from fakes import repo_config

from igdm.config import Owner
from igdm.llm import GeminiWriter, LlmError, LlmReply, OpenAIWriter, _system, _user

SAM = Owner("Sam")

REEL = repo_config().reels[0]
THREAD = [{"from": "Sam", "text": "do you still want the material?"},
          {"from": "them", "text": "do you have the robotics one?"}]
OUT = {"reply": "yes it's all in there, what's your email?", "confident": True,
       "new_promise": False}


def openai_ok(seen):
    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(OUT)}]}],
            "usage": {"input_tokens": 321, "output_tokens": 45},
        })
    return handler


def test_openai_request_and_parse():
    seen = {}
    w = OpenAIWriter("sk-test", "gpt-6-luna", owner=SAM, transport=httpx.MockTransport(openai_ok(seen)))
    r = w.write(THREAD, REEL)
    assert r == LlmReply(OUT["reply"], True, False, 321, 45)
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "gpt-6-luna"
    assert body["text"]["format"]["type"] == "json_schema"
    assert set(body["text"]["format"]["schema"]["properties"]) == {"reply", "confident", "new_promise"}
    assert "robotics" in json.dumps(body["input"])
    assert REEL.offer in json.dumps(body["input"])


def test_openai_fix_is_sent():
    seen = {}
    w = OpenAIWriter("k", "m", owner=SAM, transport=httpx.MockTransport(openai_ok(seen)))
    w.write(THREAD, REEL, fix=["uses a dash (em dash or en dash)"])
    assert "uses a dash" in json.dumps(seen["body"]["input"])


@pytest.mark.parametrize("resp", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"output": []}),
    httpx.Response(200, json={"output": [{"type": "message", "content": [
        {"type": "output_text", "text": "not json inside"}]}]}),
    httpx.Response(200, json={"output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"reply": "x"})}]}]}),
])
def test_openai_bad_responses_raise(resp):
    w = OpenAIWriter("k", "m", owner=SAM, transport=httpx.MockTransport(lambda r: resp))
    with pytest.raises(LlmError):
        w.write(THREAD, REEL)


def test_openai_timeout_raises():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)
    w = OpenAIWriter("k", "m", owner=SAM, transport=httpx.MockTransport(handler))
    with pytest.raises(LlmError):
        w.write(THREAD, REEL)


def test_gemini_request_and_parse():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(OUT)}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        })

    w = GeminiWriter("g-key", "gemini-3.8-flash", owner=SAM, transport=httpx.MockTransport(handler))
    r = w.write(THREAD, REEL)
    assert r == LlmReply(OUT["reply"], True, False, 100, 20)
    assert seen["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-3.8-flash:generateContent")
    assert seen["key"] == "g-key"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_bad_response_raises():
    w = GeminiWriter("k", "m", owner=SAM, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"candidates": []})))
    with pytest.raises(LlmError):
        w.write(THREAD, REEL)


def test_system_prompt_uses_owner():
    owner = Owner("Sam", "a baker who posts recipes", "all lowercase")
    prompt = _system(REEL, owner)
    assert "as Sam, a baker who posts recipes." in prompt
    assert "- all lowercase" in prompt
    assert "How Sam writes:" in prompt
    assert "Alex" not in prompt
    bare = _system(REEL, Owner("Sam", "", "all lowercase"))
    assert "as Sam." in bare and "Alex" not in bare


def test_system_prompt_keeps_voice_rules():
    prompt = _system(REEL, SAM)
    assert "never use an em dash or en dash" in prompt
    assert "never sound like an assistant" in prompt


def test_owner_name_with_brackets():
    from igdm.jev import route_instructions
    owner = Owner("Zoë's {Kitchen}", "a {baker}", "x {y}")
    assert "Zoë's {Kitchen}" in _system(REEL, owner)
    assert "Zoë's {Kitchen}" in route_instructions(owner.name)


def test_writers_send_the_owner_prompt():
    seen = {}
    w = OpenAIWriter("k", "m", owner=Owner("Sam", "a baker"), transport=httpx.MockTransport(openai_ok(seen)))
    w.write(THREAD, REEL)
    assert "as Sam, a baker." in json.dumps(seen["body"]["input"])


GOLDEN_SYSTEM = """You write one Instagram DM reply as Sam.

Someone commented on Sam's reel to get free material, and Sam wants their email so Sam can send it.
The material: the free guide
Reel keyword: Tokyo
There is also a short checklist included with the guide.

Write ONE short reply to their latest message that answers it and moves towards getting their email.

How Sam writes:
- casual and short, like texting a friend. everyday words, no big words.
- one or two sentences, no greetings or sign-offs.
- never use an em dash or en dash.
- never start a sentence with a label and a colon (like "quick update: ...").
- never write clipped fragment sentences (like "all done. sent over.").
- never sound like an assistant ("certainly", "i'd be happy to", "great question", "delve").

Rules:
- never promise anything that isn't listed above: no calls, no custom work, no deadlines, no other files.
  If your reply would promise something new, set new_promise to true.
- If you're unsure what they want, set confident to false.

Answer as JSON with keys reply, confident, new_promise."""

GOLDEN_USER = """The conversation so far, oldest first:
[
 {
  "from": "Sam",
  "text": "do you still want the material?"
 },
 {
  "from": "them",
  "text": "do you have the robotics one?"
 }
]

Your last attempt broke these rules, write it again without them:
- uses a dash (em dash or en dash)"""


def test_prompt_golden():
    """The whole text the model sees, for the test config (owner Sam). Change it on purpose only."""
    cfg = repo_config()
    assert cfg.owner == Owner("Sam", "", cfg.owner.style)
    assert _system(cfg.reels[0], cfg.owner) == GOLDEN_SYSTEM
    assert _user(THREAD, ["uses a dash (em dash or en dash)"]) == GOLDEN_USER
    assert _user(THREAD, None) == GOLDEN_USER.split("\n\nYour last")[0]


def test_prompt_golden_is_what_gets_sent():
    seen = {}
    w = OpenAIWriter("k", "m", owner=cfg_owner(), transport=httpx.MockTransport(openai_ok(seen)))
    w.write(THREAD, REEL, fix=["uses a dash (em dash or en dash)"])
    assert [m["content"] for m in seen["body"]["input"]] == [GOLDEN_SYSTEM, GOLDEN_USER]


def cfg_owner():
    return repo_config().owner
