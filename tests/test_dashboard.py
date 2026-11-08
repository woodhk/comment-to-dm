import re
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import pytest
from fakes import repo_config

from igdm.dashboard import PAGE, serve
from igdm.db import Database, Request, Status


@pytest.fixture
def app(tmp_path):
    from dataclasses import replace
    cfg = replace(repo_config(), db_path=tmp_path / "d.sqlite")
    db = Database(cfg.db_path)
    db.insert_request(Request(
        comment_id="hand:tokyo:igsid-a", reel_key="tokyo", media_id="m1", username="amy",
        commenter_id=None, text="x", commented_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        status=Status.AWAITING_EMAIL, igsid="igsid-a",
        last_user_msg_at=datetime(2026, 11, 3, tzinfo=timezone.utc)))
    db.flag("hand:tokyo:igsid-a", "info", "email is in their bio", "find it")
    server = serve(cfg, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, db
    server.shutdown()
    server.server_close()


CID = quote("hand:tokyo:igsid-a", safe="")


def test_page_served(app):
    base, _ = app
    r = httpx.get(base + "/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_state_endpoint_returns_json(app):
    base, _ = app
    s = httpx.get(base + "/api/state").json()
    assert s["flags"][0]["handle"] == "amy" and s["flags"][0]["kind"] == "info"


def test_email_action_queues_and_sets_gotit_pending(app):
    base, db = app
    r = httpx.post(f"{base}/api/flags/{CID}/email", json={"email": " Amy@X.com "})
    assert r.status_code == 200
    assert db.get("hand:tokyo:igsid-a").status is Status.GOTIT_PENDING
    (e,) = db.emails()
    assert e.email == "amy@x.com" and e.send_state == "queued"


def test_bad_email_400(app):
    base, db = app
    r = httpx.post(f"{base}/api/flags/{CID}/email", json={"email": "amy at gmail"})
    assert r.status_code == 400 and db.emails() == []


def test_done(app):
    base, db = app
    assert httpx.post(f"{base}/api/flags/{CID}/done").status_code == 200
    assert db.get("hand:tokyo:igsid-a").status is Status.DONE_BY_OWNER


def test_carry_on(app):
    base, db = app
    assert httpx.post(f"{base}/api/flags/{CID}/carry-on").status_code == 200
    assert db.get("hand:tokyo:igsid-a").status is Status.AWAITING_EMAIL


def test_carry_on_409(app):
    base, db = app
    db.update("hand:tokyo:igsid-a", status=Status.HANDLED_BY_OWNER)
    assert httpx.post(f"{base}/api/flags/{CID}/carry-on").status_code == 409


def test_unknown_flag_404(app):
    base, _ = app
    assert httpx.post(f"{base}/api/flags/nope/done").status_code == 404


def test_send_now_and_mark_sent(app):
    base, db = app
    i = db.add_email(comment_id="old", handle="p", igsid=None, email="p@x.com",
                     provided_at=datetime(2026, 11, 3, tzinfo=timezone.utc), reel_url="r",
                     keyword="Tokyo")
    assert httpx.post(f"{base}/api/emails/{i}/send-now").status_code == 200
    assert db.email_by_id(i).send_state == "queued"
    assert httpx.post(f"{base}/api/emails/{i}/send-now").status_code == 409
    j = db.add_email(comment_id="old2", handle="q", igsid=None, email="q@x.com",
                     provided_at=datetime(2026, 11, 3, tzinfo=timezone.utc), reel_url="r",
                     keyword="Tokyo")
    assert httpx.post(f"{base}/api/emails/{j}/mark-sent").status_code == 200
    assert db.email_by_id(j).send_state == "sent"


@pytest.mark.parametrize("headers", [{"Host": "evil.com"}, {"Origin": "https://evil.com"},
                                     {"Host": "attacker.localhost:9"}])
def test_rejects_foreign_host_and_origin(app, headers):
    base, db = app
    r = httpx.post(f"{base}/api/flags/{CID}/done", headers=headers)
    assert r.status_code == 403
    assert db.get("hand:tokyo:igsid-a").status is Status.NEEDS_INFO


def test_get_with_foreign_host_rejected(app):
    base, _ = app
    assert httpx.get(base + "/api/state", headers={"Host": "evil.com"}).status_code == 403


def test_binds_localhost_only(tmp_path):
    from dataclasses import replace
    s = serve(replace(repo_config(), db_path=tmp_path / "x.sqlite"), port=0)
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.server_close()


def test_page_has_no_external_urls():
    urls = re.findall(r"https?://[^\s\"'<>)]+", PAGE.read_text())
    assert all(u.startswith("https://www.instagram.com/") for u in urls), urls


def test_page_copy_follows_voice_rules():
    text = PAGE.read_text()
    assert "—" not in text and "–" not in text


def test_page_has_sidebar_and_views():
    html = PAGE.read_text()
    for needle in ('id="side"', 'id="side-toggle"', 'href="#overview"', 'href="#reels"',
                   'id="view-overview"', 'id="view-reels"', "igdm.sidebar", "hashchange"):
        assert needle in html, needle


def test_page_has_reels_ui():
    html = PAGE.read_text()
    for needle in ("/api/reels/check", "/api/reels/new", "Still to fill in", "Pause reel",
                   "Emails you send yourself with Send now still go out"):
        assert needle in html, needle


def test_activity_endpoint_pages(app):
    base, db = app
    db.rows("DELETE FROM events")  # the fixture's flag, so only the replies below are listed
    t = datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
    from datetime import timedelta
    for m in range(5):
        db.record_public_reply(f"r{m}", t - timedelta(minutes=m), "hand:tokyo:igsid-a")
    a = httpx.get(base + "/api/activity?limit=2").json()
    assert [i["detail"] for i in a["items"]] == ["r0", "r1"] and a["more"]
    assert a["items"][0]["handle"] == "amy"
    b = httpx.get(base + "/api/activity", params={"limit": 2, "before": a["next"]}).json()
    assert [i["detail"] for i in b["items"]] == ["r2", "r3"]
    assert "health" in a


def test_activity_endpoint_rejects_bad_params(app):
    base, _ = app
    assert httpx.get(base + "/api/activity?before=yesterday").status_code == 400
    assert httpx.get(base + "/api/activity?limit=abc").status_code == 400


def test_state_includes_latest_activity(app):
    base, db = app
    db.record_public_reply("check dms", datetime(2026, 11, 3, tzinfo=timezone.utc))
    s = httpx.get(base + "/api/state").json()
    types = [i["type"] for i in s["activity"]["items"]]
    assert types == ["flagged", "public_reply"]  # the fixture's flag is newer
    assert len(s["activity"]["items"]) <= 5 and "health" in s["activity"]


def test_page_has_activity_ui():
    html = PAGE.read_text()
    assert 'href="#activity"' in html and 'id="view-activity"' in html
    assert 'id="latest"' in html  # the Overview card


def test_page_has_a_filterable_funnel_and_a_by_reel_tab():
    html = PAGE.read_text()
    for needle in ('id="funnel-reel"', "All reels", "igdm.funnel", "byreel", "By reel"):
        assert needle in html, needle


TS = "igdm.tail1234.ts.net"


@pytest.fixture
def ts_app(tmp_path):
    from dataclasses import replace
    cfg = replace(repo_config(), db_path=tmp_path / "d.sqlite")
    server = serve(cfg, port=0, extra_hosts=(TS,))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_extra_host_allowed(ts_app):
    assert httpx.get(ts_app + "/", headers={"Host": TS}).status_code == 200
    assert httpx.get(ts_app + "/", headers={"Host": TS + ":443"}).status_code == 200


def test_extra_host_https_origin_allowed(ts_app):
    # past the Host/Origin check, an unknown route answers 404 (403 would mean refused)
    r = httpx.post(ts_app + "/api/no-such-thing", json={},
                   headers={"Host": TS, "Origin": "https://" + TS})
    assert r.status_code == 404


def test_lookalike_host_refused(ts_app):
    assert httpx.get(ts_app + "/", headers={"Host": TS + ".evil.com"}).status_code == 403


def test_http_origin_for_extra_host_refused(ts_app):
    r = httpx.post(ts_app + "/api/no-such-thing", json={},
                   headers={"Host": TS, "Origin": "http://" + TS})
    assert r.status_code == 403


def test_no_extra_hosts_by_default(app):
    base, _ = app
    assert httpx.get(base + "/", headers={"Host": TS}).status_code == 403


def test_localhost_still_allowed_with_extra_hosts(ts_app):
    assert httpx.get(ts_app + "/").status_code == 200
