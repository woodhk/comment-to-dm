import json
from datetime import datetime, timezone

import httpx
import pytest

from igdm.config import ReelConfig
from igdm.jev import JevClassifier, JevError
from fakes import FakeClassifier

REEL = ReelConfig("r", "u", "sc", "TOKYO", "the guide", "m", datetime(2026, 11, 3, tzinfo=timezone.utc))


def make(handler):
    return JevClassifier("k", owner_name="Sam", transport=httpx.MockTransport(handler))


def test_comment_request_and_result():
    seen = {}

    def h(req):
        seen["auth"] = req.headers["Authorization"]
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"wants_material": {"type": "noul", "noul": 0.93}}})

    assert make(h).comment_wants_material("tokyo pls", REEL) == 0.93
    b = seen["body"]
    assert seen["auth"] == "Bearer k"
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert b["model"] == "jev-latest"
    assert b["questions"]["wants_material"]["type"] == "noul"
    assert b["state"]["comment"] == "tokyo pls"
    assert b["state"]["reel"]["call_to_action"] == 'Comment "TOKYO" and I\'ll DM it to you'
    assert b["state"]["reel"]["offer"] == "the guide"
    assert b["questions"]["wants_material"]["criteria"]["true"]


def test_reply_intent():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"intent": {"type": "choice", "choice": "yes", "confidence": 0.7,
                                                                "probabilities": {"yes": 0.7, "no": 0.1, "other": 0.2}}}})

    r = make(h).reply_intent("want it?", "yes")
    assert r == {"yes": 0.7, "no": 0.1, "other": 0.2}
    assert seen["body"]["state"] == {"our_message": "want it?", "their_reply": "yes"}
    assert seen["body"]["questions"]["intent"]["type"] == "choice"


@pytest.mark.parametrize("resp", [
    httpx.Response(500, json={}),
    httpx.Response(200, json={"nope": 1}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"answers": {"wants_material": {"type": "noul", "noul": "x"}}}),
    httpx.Response(200, json={"answers": {"wants_material": {"type": "noul"}}}),
])
def test_comment_errors(resp):
    with pytest.raises(JevError):
        make(lambda req: resp).comment_wants_material("x", REEL)


@pytest.mark.parametrize("probs", [{"yes": 0.5, "no": 0.5}, "bad", None])
def test_reply_missing_keys(probs):
    body = {"answers": {"intent": {"type": "choice", "probabilities": probs}}}
    with pytest.raises(JevError):
        make(lambda req: httpx.Response(200, json=body)).reply_intent("a", "b")


def test_transport_error():
    def h(req):
        raise httpx.ConnectTimeout("t")

    with pytest.raises(JevError):
        make(h).comment_wants_material("x", REEL)


def test_fake_classifier():
    f = FakeClassifier({"hi": 0.9}, {"yes": {"yes": 1.0, "no": 0.0, "other": 0.0}}, default_comment=0.1)
    assert f.comment_wants_material("hi", REEL) == 0.9
    assert f.comment_wants_material("zzz", REEL) == 0.1
    assert f.reply_intent("m", "yes")["yes"] == 1.0
    assert f.reply_intent("m", "unknown") == {"yes": 0.0, "no": 0.0, "other": 1.0}
    f.raise_on.add("boom")
    with pytest.raises(JevError):
        f.comment_wants_material("boom", REEL)
    with pytest.raises(JevError):
        f.reply_intent("m", "boom")


def test_route_reply_request():
    import json

    import httpx

    from igdm.jev import BUILTINS, JevClassifier
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        probs = {k: 0.0 for k in ("yes", "neater", *BUILTINS)}
        probs["neater"] = 0.9
        return httpx.Response(200, json={"answers": {"route": {"probabilities": probs}}})

    jev = JevClassifier("k", owner_name="Sam", transport=httpx.MockTransport(handler))
    options = {"yes": "they want it", "neater": "they picked neater", **BUILTINS}
    thread = [{"from": "owner", "text": "raw or neater?"}, {"from": "them", "text": "neater"}]
    probs = jev.route_reply(thread, "neater", options)
    assert probs["neater"] == 0.9
    q = seen["body"]["questions"]["route"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(options)
    assert seen["body"]["state"]["conversation"] == thread
    assert seen["body"]["state"]["their_latest_reply"] == "neater"


def test_route_reply_missing_option_raises():
    import httpx

    from igdm.jev import JevClassifier, JevError
    jev = JevClassifier("k", owner_name="Sam", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"answers": {"route": {"probabilities": {"yes": 1}}}})))
    with pytest.raises(JevError):
        jev.route_reply([], "x", {"yes": "a", "custom": "b"})


def test_email_check_request():
    from igdm.jev import EMAIL_CRITERIA, EMAIL_INSTRUCTIONS
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"email_real": {"type": "noul", "noul": 0.1}}})

    assert make(h).email_looks_real("email@totallyreal.com", "here you go") == 0.1
    b = seen["body"]
    q = b["questions"]["email_real"]
    assert q["type"] == "noul"
    assert q["instructions"] == EMAIL_INSTRUCTIONS
    assert q["criteria"] == EMAIL_CRITERIA
    assert b["state"] == {"email": "email@totallyreal.com", "their_message": "here you go"}


def test_email_check_bad_value_raises():
    h = lambda r: httpx.Response(200, json={"answers": {"email_real": {"type": "noul"}}})  # noqa: E731
    with pytest.raises(JevError):
        make(h).email_looks_real("a@b.com", "a@b.com")


def test_route_uses_owner_name():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"route": {"probabilities": {"yes": 1.0}}}})

    make(handler).route_reply([], "x", {"yes": "a"})
    q = seen["body"]["questions"]["route"]
    assert q["instructions"].startswith("Sam offered someone free material from their Instagram reel")


def test_cheeky_request_and_result():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"answers": {"cheeky": {"type": "noul", "noul": 0.97}}})

    from igdm.jev import CHEEKY_INSTRUCTIONS
    assert make(h).comment_is_cheeky("tokyo. also you're a fraud lol", REEL) == 0.97
    b = seen["body"]
    q = b["questions"]["cheeky"]
    assert q["type"] == "noul" and q["instructions"] == CHEEKY_INSTRUCTIONS
    assert set(q["criteria"]) == {"true", "false"}
    assert b["state"]["comment"] == "tokyo. also you're a fraud lol"
    assert 'Comment "TOKYO"' in b["state"]["reel"]["call_to_action"]


def test_cheeky_bad_value_raises():
    def h(req):
        return httpx.Response(200, json={"answers": {"cheeky": {"type": "noul", "noul": "high"}}})

    with pytest.raises(JevError):
        make(h).comment_is_cheeky("x", REEL)


def test_cheeky_instructions_cover_the_cases():
    from igdm.jev import CHEEKY_INSTRUCTIONS as s
    for words in ("insult", "mock", "slur", "joke", "instructions", "unrelated", "emphasis"):
        assert words in s
