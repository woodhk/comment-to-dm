import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from igdm.dryrun import DryRunInstagram
from igdm.instagram import GraphInstagram, InstagramError, RateLimited
from fakes import FakeInstagram

UTC = timezone.utc
BASE = "https://graph.instagram.com/v26.0"


def make(handler):
    return GraphInstagram("TOK", transport=httpx.MockTransport(handler))


def test_comments_paginate_two_pages():
    def h(req):
        assert req.headers["Authorization"] == "Bearer TOK"
        if "page2" in str(req.url):
            return httpx.Response(200, json={"data": [
                {"id": "c3", "text": "c", "timestamp": "2026-11-03T03:00:00+0000", "username": "u3"}]})
        assert req.url.path.endswith("/m1/comments")
        return httpx.Response(200, json={"data": [
            {"id": "c1", "text": "a", "timestamp": "2026-11-03T01:00:00+0000", "username": "u1",
             "from": {"id": "1", "username": "u1"}},
            {"id": "c2", "text": "b", "timestamp": "2026-11-03T02:00:00+0000", "username": "u2"}],
            "paging": {"next": "https://graph.instagram.com/page2"}})
    cs = make(h).list_comments("m1")
    assert [c.id for c in cs] == ["c1", "c2", "c3"]
    assert cs[0].timestamp == datetime(2026, 11, 3, 1, tzinfo=UTC)
    assert cs[0].from_id == "1" and cs[1].from_id is None


def test_send_private_reply_body():
    seen = {}
    def h(req):
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx.Response(200, json={"recipient_id": "igsid1", "message_id": "mid1"})
    assert make(h).send_private_reply("me1", "c9", "hi") == ("igsid1", "mid1")
    assert seen["body"] == {"recipient": {"comment_id": "c9"}, "message": {"text": "hi"}}
    assert seen["path"] == "/v26.0/me1/messages"


def test_graph_error():
    g = make(lambda r: httpx.Response(400, json={"error": {"code": 10, "message": "x", "error_subcode": 5}}))
    with pytest.raises(InstagramError) as e:
        g.me()
    assert e.value.code == 10 and e.value.status == 400 and e.value.message == "x"
    assert not isinstance(e.value, RateLimited)


def test_non_json_error():
    with pytest.raises(InstagramError) as e:
        make(lambda r: httpx.Response(502, text="bad gateway")).me()
    assert e.value.status == 502 and "bad gateway" in e.value.message


def test_rate_limited():
    with pytest.raises(RateLimited):
        make(lambda r: httpx.Response(429, text="slow")).me()
    with pytest.raises(RateLimited):
        make(lambda r: httpx.Response(400, json={"error": {"code": 613, "message": "m"}})).me()


def test_transport_error():
    def h(req):
        raise httpx.ConnectError("down")
    with pytest.raises(InstagramError) as e:
        make(h).me()
    assert e.value.status == 0 and e.value.code is None


def test_me_fallback_id():
    g = make(lambda r: httpx.Response(200, json={"id": "7", "username": "alex"}))
    assert g.me() == ("7", "alex")
    g = make(lambda r: httpx.Response(200, json={"user_id": "8", "id": "7", "username": "alex"}))
    assert g.me() == ("8", "alex")


def test_list_messages_oldest_first():
    def h(req):
        return httpx.Response(200, json={"messages": {"data": [
            {"id": "m2", "created_time": "2026-11-03T02:00:00+0000", "from": {"id": "u"}, "message": "new"},
            {"id": "m1", "created_time": "2026-11-03T01:00:00+0000", "from": {"id": "me"}},
        ]}, "id": "conv"})
    ms = make(h).list_messages("conv")
    assert [m.id for m in ms] == ["m1", "m2"]
    assert ms[0].text == "" and ms[1].from_id == "u"


def test_list_conversations_stops_at_old():
    calls = []
    def h(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"data": [
            {"id": "a", "updated_time": "2026-11-03T05:00:00+0000", "participants": {"data": [{"id": "me"}, {"id": "u1"}]}},
            {"id": "b", "updated_time": "2026-11-03T01:00:00+0000", "participants": {"data": [{"id": "u2"}]}},
        ], "paging": {"next": "https://graph.instagram.com/never"}})
    cs = make(h).list_conversations("me", datetime(2026, 11, 3, 3, tzinfo=UTC))
    assert [c.id for c in cs] == ["a"] and cs[0].participant_ids == ["me", "u1"]
    assert len(calls) == 1


def test_find_media_id():
    def h(req):
        if "p2" in str(req.url):
            return httpx.Response(200, json={"data": [
                {"id": "99", "permalink": "https://www.instagram.com/reel/TESTREEL01/"}]})
        return httpx.Response(200, json={"data": [
            {"id": "1", "permalink": "https://www.instagram.com/p/other/"}],
            "paging": {"next": "https://graph.instagram.com/p2"}})
    g = make(h)
    assert g.find_media_id("TESTREEL01") == "99"
    assert g.find_media_id("nope") is None


def test_display_name():
    assert make(lambda r: httpx.Response(200, json={"name": "Sam"})).get_display_name("u") == "Sam"
    assert make(lambda r: httpx.Response(200, json={})).get_display_name("u") is None
    assert make(lambda r: httpx.Response(400, json={"error": {"code": 1, "message": "m"}})).get_display_name("u") is None


def test_reply_and_send_message_and_refresh():
    seen = []
    def h(req):
        seen.append(req)
        if "refresh_access_token" in req.url.path:
            return httpx.Response(200, json={"access_token": "NEW"})
        if req.url.path.endswith("/replies"):
            return httpx.Response(200, json={"id": "r1"})
        return httpx.Response(200, json={"recipient_id": "i", "message_id": "m5"})
    g = make(h)
    assert g.reply_to_comment("c1", "yo") == "r1"
    assert b"yo" in seen[0].content
    assert g.send_message("me", "i", "t") == "m5"
    assert json.loads(seen[1].content) == {"recipient": {"id": "i"}, "message": {"text": "t"}}
    assert g.refresh_token() == "NEW"
    assert seen[2].url.params["access_token"] == "TOK"
    assert seen[2].url.host == "graph.instagram.com"


def test_dryrun_makes_no_writes(caplog):
    def h(req):
        if req.method != "GET":
            raise AssertionError("write attempted")
        return httpx.Response(200, json={"user_id": "1", "username": "a"})
    d = DryRunInstagram(make(h))
    assert d.me() == ("1", "a")
    with caplog.at_level("INFO"):
        assert d.send_message("1", "i", "hello").startswith("dry-")
        ig, mid = d.send_private_reply("1", "c5", "t")
        assert ig == "dry-igsid-c5" and mid.startswith("dry-")
        assert d.reply_to_comment("c5", "t").startswith("dry-")
    assert "DRY-RUN would send" in caplog.text


def test_fake_basics():
    t0 = datetime(2026, 11, 3, tzinfo=UTC)
    f = FakeInstagram("me", "alex", now=t0)
    from igdm.instagram import Comment
    f.comments["m"] = [Comment("c1", "x", "sam", "42", t0)]
    ig, mid = f.send_private_reply("me", "c1", "hi")
    assert ig == "igsid-42" and f.sent == [("private", "c1", "hi")]
    f.now = t0 + timedelta(minutes=1)
    f.add_user_message(ig, "yes", f.now)
    convs = f.list_conversations("me", t0)
    assert len(convs) == 1 and convs[0].participant_ids == ["me", "igsid-42"]
    ms = f.list_messages(convs[0].id)
    assert [m.text for m in ms] == ["hi", "yes"] and ms[0].id == mid and ms[0].from_id == "me"
    assert f.list_conversations("me", t0 + timedelta(minutes=2)) == []
    f.fail_private["c1"] = InstagramError(400, 100, "gone")
    with pytest.raises(InstagramError):
        f.send_private_reply("me", "c1", "again")
    f.echo_ids_differ = True
    returned = f.send_message("me", ig, "d")
    assert f.list_messages(convs[0].id)[-1].id != returned
    assert f.list_messages(convs[0].id)[-1].text == "d"
    assert f.get_display_name("42") is None


# ---- final review: maybe-sent writes, comment replies
from igdm.instagram import Comment, MaybeSent


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError,
                                 httpx.ReadError])
def test_post_lost_answer_is_maybe_sent(exc):
    def h(req):
        raise exc("lost", request=req)
    g = make(h)
    with pytest.raises(MaybeSent) as e:
        g.send_message("me", "i", "t")
    assert e.value.status == 0
    with pytest.raises(MaybeSent):
        g.send_private_reply("me", "c1", "t")
    with pytest.raises(MaybeSent):
        g.reply_to_comment("c1", "t")


@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ConnectTimeout])
def test_post_never_connected_is_plain_error(exc):
    def h(req):
        raise exc("down", request=req)
    with pytest.raises(InstagramError) as e:
        make(h).send_message("me", "i", "t")
    assert not isinstance(e.value, MaybeSent) and e.value.status == 0


def test_get_timeout_is_plain_error():
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(InstagramError) as e:
        make(h).me()
    assert not isinstance(e.value, MaybeSent)


def test_list_comment_replies():
    seen = {}
    def h(req):
        seen["path"] = req.url.path
        seen["fields"] = req.url.params["fields"]
        return httpx.Response(200, json={"data": [
            {"id": "r1", "text": "dm'd you", "timestamp": "2026-11-03T02:00:00+0000",
             "username": "alex", "from": {"id": "me1", "username": "alex"}},
            {"id": "r2", "timestamp": "2026-11-03T02:01:00+0000", "from": {"id": "9", "username": "bob"}},
        ]})
    rs = make(h).list_comment_replies("c1")
    assert seen["path"] == "/v26.0/c1/replies"
    assert "from" in seen["fields"] and "username" in seen["fields"]
    assert [(r.id, r.username, r.from_id) for r in rs] == [("r1", "alex", "me1"), ("r2", "bob", "9")]
    assert rs[1].text == ""


def test_dryrun_reads_comment_replies():
    f = FakeInstagram()
    f.comment_replies["c1"] = [Comment("r1", "hi", "alex", "me1", datetime(2026, 11, 3, tzinfo=UTC))]
    assert [r.id for r in DryRunInstagram(f).list_comment_replies("c1")] == ["r1"]


def test_comments_requested_100_per_page():
    seen = {}
    def h(req):
        seen["limit"] = req.url.params["limit"]
        return httpx.Response(200, json={"data": []})
    make(h).list_comments("m1")
    assert seen["limit"] == "100"


# ---- platform change: Instagram API with Facebook Login (graph.facebook.com, Page token)
FB = "https://graph.facebook.com/v26.0"
IG_ID, PAGE_ID = "1784", "555"


def make_fb(handler, node="page"):
    return GraphInstagram("PAGETOK", base_url=FB, ig_id=IG_ID, page_id=PAGE_ID,
                          messaging_node=node, transport=httpx.MockTransport(handler))


def _fb_router(routes, seen):
    """routes: {(METHOD, path): json}. Records every request; unknown routes fail the test."""
    def h(req):
        seen.append(req)
        assert req.url.host == "graph.facebook.com"
        assert req.headers["Authorization"] == "Bearer PAGETOK"
        key = (req.method, req.url.path)
        assert key in routes, f"unexpected {key}"
        return httpx.Response(200, json=routes[key])
    return h


def test_fb_me_uses_ig_id_not_me():
    seen = []
    g = make_fb(_fb_router({("GET", f"/v26.0/{IG_ID}"): {"id": IG_ID, "username": "alex"}}, seen))
    assert g.me() == (IG_ID, "alex")
    assert seen[0].url.params["fields"] == "username"
    assert all("/me" not in r.url.path for r in seen)


def test_fb_find_media_id_uses_ig_media():
    seen = []
    g = make_fb(_fb_router({("GET", f"/v26.0/{IG_ID}/media"): {"data": [
        {"id": "99", "permalink": "https://www.instagram.com/reel/TESTREEL01/"}]}}, seen))
    assert g.find_media_id("TESTREEL01") == "99"
    assert seen[0].url.params["fields"] == "id,permalink,timestamp"


def test_fb_comments_and_replies_paths():
    seen = []
    c = {"id": "c1", "text": "hi", "timestamp": "2026-11-03T01:00:00+0000", "username": "sam",
         "from": {"id": "42", "username": "sam"}}
    g = make_fb(_fb_router({("GET", "/v26.0/m1/comments"): {"data": [c]},
                            ("GET", "/v26.0/c1/replies"): {"data": [c]}}, seen))
    assert [x.from_id for x in g.list_comments("m1")] == ["42"]
    assert [x.username for x in g.list_comment_replies("c1")] == ["sam"]
    assert seen[0].url.params["fields"] == "id,text,timestamp,username,from"
    assert seen[1].url.params["fields"] == "id,text,timestamp,username,from"


def test_fb_public_reply():
    seen = []
    g = make_fb(_fb_router({("POST", "/v26.0/c1/replies"): {"id": "r1"}}, seen))
    assert g.reply_to_comment("c1", "check dms") == "r1"
    assert httpx.QueryParams(seen[0].content.decode())["message"] == "check dms"


@pytest.mark.parametrize("node,expected", [("page", PAGE_ID), ("ig", IG_ID)])
def test_fb_private_reply_and_message_use_messaging_node(node, expected):
    seen = []
    g = make_fb(_fb_router({("POST", f"/v26.0/{expected}/messages"):
                            {"recipient_id": "igsid1", "message_id": "mid1"}}, seen), node=node)
    # the engine passes its own IG id; the client sends via the configured node
    assert g.send_private_reply(IG_ID, "c9", "hey") == ("igsid1", "mid1")
    assert json.loads(seen[0].content) == {"recipient": {"comment_id": "c9"}, "message": {"text": "hey"}}
    assert g.send_message(IG_ID, "igsid1", "yo") == "mid1"
    assert json.loads(seen[1].content) == {"recipient": {"id": "igsid1"}, "message": {"text": "yo"}}


def test_fb_bad_messaging_node_rejected():
    with pytest.raises(ValueError):
        make_fb(lambda r: httpx.Response(200, json={}), node="me")


def test_fb_conversations_on_page_and_own_detected_by_id_or_username():
    seen = []
    g = make_fb(_fb_router({
        ("GET", f"/v26.0/{IG_ID}"): {"id": IG_ID, "username": "alex"},
        ("GET", f"/v26.0/{PAGE_ID}/conversations"): {"data": [
            {"id": "t1", "updated_time": "2026-11-03T05:00:00+0000", "participants": {"data": [
                {"id": "999", "username": "Alex"}, {"id": "u1", "username": "sam"}]}},
            {"id": "t2", "updated_time": "2026-11-03T04:00:00+0000", "participants": {"data": [
                {"id": IG_ID}, {"id": "u2", "username": "bob"}]}},
        ]},
    }, seen))
    g.me()
    cs = g.list_conversations(IG_ID, datetime(2026, 11, 3, 3, tzinfo=UTC))
    assert [c.participant_ids for c in cs] == [[IG_ID, "u1"], [IG_ID, "u2"]]
    conv = seen[-1]
    assert conv.url.params["platform"] == "instagram"
    assert conv.url.params["fields"] == "id,updated_time,participants"


def test_fb_messages_from_own_username_normalised_to_ig_id():
    seen = []
    g = make_fb(_fb_router({
        ("GET", f"/v26.0/{IG_ID}"): {"id": IG_ID, "username": "alex"},
        ("GET", "/v26.0/t1"): {"id": "t1", "messages": {"data": [
            {"id": "m3", "created_time": "2026-11-03T03:00:00+0000",
             "from": {"id": "u1", "username": "sam"}, "message": "yes"},
            {"id": "m2", "created_time": "2026-11-03T02:00:00+0000",
             "from": {"id": IG_ID, "username": "alex"}, "message": "dm1"},
            {"id": "m1", "created_time": "2026-11-03T01:00:00+0000",
             "from": {"id": "scoped-777", "username": "alex"}, "message": "by hand"},
        ]}},
    }, seen))
    ms = g.list_messages("t1")  # no prior me(): the client fetches its username itself
    assert [(m.id, m.from_id) for m in ms] == [("m1", IG_ID), ("m2", IG_ID), ("m3", "u1")]
    conv = next(r for r in seen if r.url.path == "/v26.0/t1")
    assert conv.url.params["fields"] == "messages.limit(20){id,created_time,from,message}"
    assert sum(1 for r in seen if r.url.path == f"/v26.0/{IG_ID}") == 1
    g.list_messages("t1")
    assert sum(1 for r in seen if r.url.path == f"/v26.0/{IG_ID}") == 1  # cached


def test_fb_display_name_and_error_none():
    seen = []
    g = make_fb(_fb_router({("GET", "/v26.0/u1"): {"name": "Sam"}}, seen))
    assert g.get_display_name("u1") == "Sam"
    assert seen[0].url.params["fields"] == "name"
    bad = make_fb(lambda r: httpx.Response(400, json={"error": {"code": 100, "message": "m"}}))
    assert bad.get_display_name("u1") is None


def test_fb_refresh_token_not_supported():
    g = make_fb(lambda r: (_ for _ in ()).throw(AssertionError("no request expected")))
    with pytest.raises(InstagramError):
        g.refresh_token()


def test_instagram_login_messages_unchanged_by_username():
    """The old mode keeps reporting raw from ids (no username matching)."""
    def h(req):
        return httpx.Response(200, json={"messages": {"data": [
            {"id": "m1", "created_time": "2026-11-03T01:00:00+0000",
             "from": {"id": "x", "username": "alex"}}]}})
    assert make(h).list_messages("t")[0].from_id == "x"


def test_find_media_returns_timestamp():
    def h(req):
        assert "timestamp" in req.url.params["fields"]
        return httpx.Response(200, json={"data": [
            {"id": "99", "permalink": "https://www.instagram.com/reel/TESTREEL01/",
             "timestamp": "2026-11-03T05:59:00+0000"}]})
    m = make(h).find_media("TESTREEL01")
    assert m.id == "99" and m.posted_at == datetime(2026, 11, 3, 5, 59, tzinfo=UTC)
    assert make(h).find_media("nope") is None


@pytest.mark.parametrize("action", ["mark_seen", "typing_on"])
def test_send_action_body(action):
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx.Response(200, json={"recipient_id": "igsid1"})
    make(h).send_action("me1", "igsid1", action)
    assert seen["body"] == {"recipient": {"id": "igsid1"}, "sender_action": action}
    assert seen["path"] == "/v26.0/me1/messages"


def test_fb_send_action_goes_to_the_page():
    seen = []
    g = make_fb(_fb_router({("POST", f"/v26.0/{PAGE_ID}/messages"): {"recipient_id": "u"}}, seen))
    g.send_action(IG_ID, "u", "typing_on")
    assert json.loads(seen[0].content) == {"recipient": {"id": "u"}, "sender_action": "typing_on"}


def test_send_action_rejects_unknown_actions():
    with pytest.raises(ValueError):
        make(lambda r: httpx.Response(200, json={})).send_action("me1", "u", "dance")


def test_dry_run_does_not_send_actions():
    calls = []
    inner = make(lambda r: (calls.append(r), httpx.Response(200, json={}))[1])
    DryRunInstagram(inner).send_action("me1", "u", "mark_seen")
    assert calls == []


@pytest.mark.parametrize("code", [80002, 80006])  # Meta's business-use throttles (Instagram, Messenger)
def test_business_use_case_throttles_are_rate_limits(code):
    g = make(lambda r: httpx.Response(400, json={"error": {"code": code, "message": "slow down"}}))
    with pytest.raises(RateLimited):
        g.me()
