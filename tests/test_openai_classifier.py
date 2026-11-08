import json
from datetime import datetime, timezone

import httpx
import pytest

from igdm.config import ReelConfig
from igdm.jev import (
    COMMENT_INSTRUCTIONS,
    EMAIL_INSTRUCTIONS,
    INTENT_INSTRUCTIONS,
    JevError,
    route_instructions,
)
from igdm.openai_classifier import OpenAIClassifier

REEL = ReelConfig("r", "u", "sc", "TOKYO", "the guide", "m", datetime(2026, 11, 3, tzinfo=timezone.utc))


def answer(obj, usage=None):
    body = {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(obj)}]}]}
    if usage is not None:
        body["usage"] = usage
    return body


def make(handler, **kw):
    return OpenAIClassifier("sk-test", "gpt-6-luna", owner_name="Sam",
                            transport=httpx.MockTransport(handler), **kw)


def replying(obj, seen=None, usage=None):
    def h(req):
        if seen is not None:
            seen["url"] = str(req.url)
            seen["auth"] = req.headers["authorization"]
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=answer(obj, usage))
    return h


def system_text(seen):
    return next(m["content"] for m in seen["body"]["input"] if m["role"] == "system")


def test_comment_request_body():
    seen = {}
    c = make(replying({"true": 0.9, "false": 0.1}, seen))
    assert c.comment_wants_material("tokyo pls", REEL) == pytest.approx(0.9)
    b = seen["body"]
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["auth"] == "Bearer sk-test"
    assert b["model"] == "gpt-6-luna"
    fmt = b["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert set(fmt["schema"]["properties"]) == {"true", "false"}
    assert all(p == {"type": "number"} for p in fmt["schema"]["properties"].values())
    assert sorted(fmt["schema"]["required"]) == ["false", "true"]
    assert fmt["schema"]["additionalProperties"] is False
    sys = system_text(seen)
    assert COMMENT_INSTRUCTIONS in sys and "for Sam," in sys
    assert "Options:\n- true: " in sys and "- false: " in sys
    assert sys.endswith("If it's unclear, spread the probability.")
    user = next(m["content"] for m in b["input"] if m["role"] == "user")
    assert json.loads(user)["comment"] == "tokyo pls"
    assert json.loads(user)["reel"]["offer"] == "the guide"


def test_email_uses_email_instructions():
    seen = {}
    c = make(replying({"true": 0.2, "false": 0.8}, seen))
    assert c.email_looks_real("no@thanks.com", "no@thanks.com") == pytest.approx(0.2)
    assert EMAIL_INSTRUCTIONS in system_text(seen)


def test_reply_intent_normalised():
    seen = {}
    c = make(replying({"yes": 2, "no": 1, "other": 1}, seen))
    assert c.reply_intent("want it?", "sure") == {"yes": 0.5, "no": 0.25, "other": 0.25}
    assert INTENT_INSTRUCTIONS in system_text(seen)


def test_route_reply_uses_given_options():
    seen = {}
    opts = {"yes": "agrees", "custom": "asks for the robotics one"}
    c = make(replying({"yes": 0.25, "custom": 0.75}, seen))
    assert c.route_reply([{"from": "them", "text": "hi"}], "robotics?", opts) == {
        "yes": 0.25, "custom": 0.75}
    props = seen["body"]["text"]["format"]["schema"]["properties"]
    assert set(props) == set(opts)
    assert route_instructions("Sam") in system_text(seen)
    assert "- custom: asks for the robotics one" in system_text(seen)


def _raw(text):
    return httpx.Response(200, json={"output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}]})


@pytest.mark.parametrize("resp", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"output": []}),
    _raw("not json either"),
    _raw(json.dumps({"yes": 0.5, "no": 0.5})),
    _raw(json.dumps({"yes": -1, "no": 1, "other": 1})),
    _raw(json.dumps({"yes": 0, "no": 0, "other": 0})),
    _raw(json.dumps({"yes": "0.5", "no": 0.25, "other": 0.25})),
    _raw(json.dumps({"yes": True, "no": 0.25, "other": 0.25})),
    _raw(json.dumps([0.5, 0.25, 0.25])),
], ids=["http500", "non-json-body", "no-output-text", "text-not-json", "missing-option",
        "negative", "all-zeros", "string-value", "bool-value", "not-an-object"])
def test_bad_answers_raise(resp):
    c = make(lambda req: resp)
    with pytest.raises(JevError):
        c.reply_intent("want it?", "sure")


def test_transport_error_raises():
    def h(req):
        raise httpx.ConnectError("down")
    with pytest.raises(JevError):
        make(h).comment_wants_material("hi", REEL)


def test_usage_recorded():
    c = make(replying({"true": 0.9, "false": 0.1}, usage={"input_tokens": 120, "output_tokens": 8}))
    assert c.last_usage == (0, 0)
    c.comment_wants_material("hi", REEL)
    assert c.last_usage == (120, 8)
    assert c.provider == "openai" and c.model == "gpt-6-luna"


def test_missing_usage_is_zero():
    c = make(replying({"true": 0.9, "false": 0.1}))
    c.comment_wants_material("hi", REEL)
    assert c.last_usage == (0, 0)
