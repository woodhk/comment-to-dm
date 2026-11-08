"""The dashboard's Reels API: list, check, add, edit, pause and resume reels in config.toml."""

import threading
import tomllib
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fakes import ROOT, repo_config

from igdm.config import load_config
from igdm.dashboard import serve
from igdm.db import Database, Request, Status
from igdm.instagram import Media
from igdm.reelform import TEMPLATE_SUBJECT

UTC = timezone.utc
POSTED = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
SZ_URL = "https://www.instagram.com/reel/TESTREEL01/"


def form(**kw):
    f = {"keyword": "Rome", "url": "https://www.instagram.com/reel/ROME1/",
         "offer": "the Rome slides", "material_url": "https://drive.google.com/x",
         "email_subject": "the {keyword} slides you asked for",
         "email_body": "Hey {name},\n\nHere's the material like I promised.\n\n{material_url}\n\nEnjoy it!\n\nAlex",
         "handle_comments_after": "", "hand_dm_phrases": [], "ai_notes": ""}
    f.update(kw)
    return f


def _start(cfg, **kw):
    server = serve(cfg, port=0, **kw)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def app(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text((ROOT / "tests" / "data" / "config.toml").read_text())
    cfg = load_config(p)
    db = Database(cfg.db_path)
    calls = []

    def find(shortcode):
        calls.append(shortcode)
        return Media("m-" + shortcode, POSTED)

    server, base = _start(cfg, config_path=p, find_media=find)
    yield SimpleNamespace(base=base, db=db, path=p, calls=calls)
    server.shutdown()
    server.server_close()


def reels(app):
    return httpx.get(app.base + "/api/reels").json()


def test_reels_list(app):
    s = reels(app)
    (r,) = s["reels"]
    assert r["key"] == "tokyo" and r["paused"] is False and r["url"] == SZ_URL
    assert r["handle_comments_after"] == "2026-11-03T14:00"
    assert r["has_leads"] is False and r["counts"] == {"comments": 0, "emails_sent": 0}
    assert s["template"]["subject"] == TEMPLATE_SUBJECT and s["config_error"] is None
    assert s["template"]["body"].startswith("Hey {name},\n\nSam here,")
    assert s["template"]["body"].endswith("\n\nSam")


def test_check_reports_errors(app):
    r = httpx.post(app.base + "/api/reels/check", json={"editing": None}).json()
    assert {"keyword", "url", "offer"} <= set(r["errors"])


def test_check_returns_posted_at(app):
    r = httpx.post(app.base + "/api/reels/check", json={**form(), "editing": None}).json()
    assert r["errors"] == {} and r["key"] == "rome" and r["posted_at"] == "2026-11-04T09:00"


def test_new_reel_saved_to_config(app):
    r = httpx.post(app.base + "/api/reels/new", json=form())
    assert r.status_code == 200 and r.json() == {"ok": True, "key": "rome"}
    keys = [x["key"] for x in tomllib.loads(app.path.read_text())["reels"]]
    assert keys == ["tokyo", "rome"]
    assert [x["key"] for x in reels(app)["reels"]] == ["tokyo", "rome"]


def test_new_reel_with_errors_not_saved(app):
    before = app.path.read_bytes()
    r = httpx.post(app.base + "/api/reels/new", json=form(email_body="Hey {name}, [what's inside it]"))
    assert r.status_code == 400 and "email_body" in r.json()["errors"]
    assert app.path.read_bytes() == before


def test_edit_saves_only_that_reel(app):
    f = form(keyword="Tokyo", url=SZ_URL, offer="new offer", handle_comments_after="2026-11-03T14:00")
    r = httpx.post(app.base + "/api/reels/tokyo/save", json=f)
    assert r.status_code == 200, r.text
    (sz,) = load_config(app.path).reels
    assert sz.offer == "new offer" and sz.keyword == "Tokyo" and app.calls == []


def test_edit_locked_keyword_with_leads(app):
    app.db.insert_request(Request(
        comment_id="c1", reel_key="tokyo", media_id="m1", username="amy", commenter_id=None,
        text="Tokyo", commented_at=POSTED, status=Status.AWAITING_YES))
    assert reels(app)["reels"][0]["has_leads"] is True
    r = httpx.post(app.base + "/api/reels/tokyo/save", json=form(keyword="Tokyo2", url=SZ_URL))
    assert r.status_code == 400 and "keyword" in r.json()["errors"]


def test_pause_and_resume_write_config(app):
    assert httpx.post(app.base + "/api/reels/tokyo/pause").status_code == 200
    assert load_config(app.path).reels[0].paused is True and reels(app)["reels"][0]["paused"] is True
    assert httpx.post(app.base + "/api/reels/tokyo/resume").status_code == 200
    assert load_config(app.path).reels[0].paused is False
    assert app.path.read_text() == (ROOT / "tests" / "data" / "config.toml").read_text()


def test_unknown_reel_404(app):
    assert httpx.post(app.base + "/api/reels/nope/pause").status_code == 404
    assert httpx.post(app.base + "/api/reels/nope/save", json=form()).status_code == 404


def test_hold_counts(app):
    for cid, st in (("a", Status.AWAITING_YES), ("b", Status.QUEUED), ("c", Status.EMAIL_CAPTURED)):
        app.db.insert_request(Request(
            comment_id=cid, reel_key="tokyo", media_id="m1", username=cid, commenter_id=None,
            text="Tokyo", commented_at=POSTED, status=st))
    app.db.add_email(comment_id="c", handle="c", igsid=None, email="c@x.com", provided_at=POSTED,
                     reel_url=SZ_URL, keyword="Tokyo", send_after=POSTED)
    i = app.db.add_email(comment_id="d", handle="d", igsid=None, email="d@x.com", provided_at=POSTED,
                         reel_url=SZ_URL, keyword="Tokyo")
    app.db.queue_now(i, POSTED)  # Alex's own: not held
    assert reels(app)["reels"][0]["hold"] == {"mid": 1, "waiting_dm": 1, "queued_emails": 1}


def test_dashboard_picks_up_hand_edit(app):
    app.path.write_text(app.path.read_text().replace('keyword = "Tokyo"', 'keyword = "Tokyo"\npaused = true'))
    assert reels(app)["reels"][0]["paused"] is True


def test_bad_hand_edit_keeps_last_good_config(app):
    app.path.write_text(app.path.read_text() + "\n[[reels]\n")
    assert reels(app)["reels"][0]["key"] == "tokyo"


def test_config_error_in_state(app):
    app.db.kv_set("config_error", "2026-11-04T02:00:00+00:00 reel \"rome\": nope")
    assert httpx.get(app.base + "/api/state").json()["config_error"].endswith('reel "rome": nope')
    assert reels(app)["config_error"].endswith('reel "rome": nope')


def test_reel_routes_check_host(app):
    r = httpx.post(app.base + "/api/reels/new", json=form(), headers={"Host": "evil.com"})
    assert r.status_code == 403
    r = httpx.post(app.base + "/api/reels/tokyo/pause", headers={"Origin": "http://evil.com"})
    assert r.status_code == 403
    assert load_config(app.path).reels[0].paused is False


def test_no_config_path_409(tmp_path):
    from dataclasses import replace
    cfg = replace(repo_config(), db_path=tmp_path / "d.sqlite")
    server, base = _start(cfg)
    try:
        assert httpx.get(base + "/api/reels").status_code == 409
        assert httpx.post(base + "/api/reels/new", json=form()).status_code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_link_misses_are_not_looked_up_again_and_again(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text((ROOT / "tests" / "data" / "config.toml").read_text())
    cfg = load_config(p)
    calls = []

    def find(shortcode):
        calls.append(shortcode)
        return None

    server, base = _start(cfg, config_path=p, find_media=find)
    try:
        for _ in range(3):
            r = httpx.post(base + "/api/reels/check", json={**form(), "editing": None}).json()
            assert r["errors"]["url"] == "that link isn't one of your posts"
        assert calls == ["ROME1"]
    finally:
        server.shutdown()
        server.server_close()


def test_counts_belong_to_their_own_reel(app):
    # a newer reel goes first in the funnel but last in config.toml: each card keeps its own counts
    assert httpx.post(app.base + "/api/reels/new", json=form()).status_code == 200
    app.db.insert_request(Request(
        comment_id="a", reel_key="tokyo", media_id="m1", username="a", commenter_id=None,
        text="Tokyo", commented_at=POSTED, status=Status.AWAITING_YES))
    counts = {r["key"]: r["counts"]["comments"] for r in reels(app)["reels"]}
    assert counts == {"tokyo": 1, "rome": 0}


def test_check_names_reels_with_the_same_material(app):
    f = form(material_url="https://example.com/material")
    r = httpx.post(app.base + "/api/reels/check", json={**f, "editing": None}).json()
    assert r["same_material"] == ["Tokyo"]


def test_reels_list_has_first_dm_and_email_timing(app):
    m = reels(app)["messages"]
    assert m["dm1"].startswith("hey {name}, it's Sam")
    assert m["email_after_seconds"] is None and m["email_delay_minutes"] == 2
