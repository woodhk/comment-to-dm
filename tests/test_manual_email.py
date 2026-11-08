"""Send material by hand: Alex enters a name, reel and email on the dashboard and it goes out."""

import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fakes import repo_config

from igdm.activity import activity
from igdm.dashboard import PAGE, serve
from igdm.db import Database
from igdm.messages import render
from igdm.stats import dashboard_state


@pytest.fixture
def app(tmp_path):
    cfg = replace(repo_config(), db_path=tmp_path / "d.sqlite")
    db = Database(cfg.db_path)
    server = serve(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, db, cfg
    server.shutdown()
    server.server_close()


FORM = {"name": "Marwane", "handle": "@marwanezb", "reel": "tokyo",
        "email": " Marwane.Zinebioui@Gmail.com "}


def send(base, **kw):
    return httpx.post(base + "/api/emails/manual", json={**FORM, **kw})


def test_preview_is_the_reels_email_with_their_name(app):
    base, _, cfg = app
    reel = cfg.reels[0]
    r = httpx.post(base + "/api/emails/preview", json={"name": "Marwane", "reel": "tokyo"})
    assert r.status_code == 200
    p = r.json()
    kw = dict(name="Marwane", keyword=reel.keyword, material_url=reel.material_url)
    assert p["subject"] == render(reel.email_subject, **kw)
    assert p["body"] == render(reel.email_body, **kw) and p["body"].startswith("Hi Marwane,")
    assert p["to_reel"] == reel.keyword


def test_preview_of_an_unknown_reel_is_refused(app):
    base, _, _ = app
    r = httpx.post(base + "/api/emails/preview", json={"name": "x", "reel": "nope"})
    assert r.status_code == 400


def test_manual_email_is_queued_to_go_out_now(app):
    base, db, cfg = app
    before = datetime.now(timezone.utc)
    r = send(base)
    assert r.status_code == 200
    e = db.email_by_id(r.json()["id"])
    assert e.email == "marwane.zinebioui@gmail.com" and e.name == "Marwane"
    assert e.instagram_handle == "marwanezb" and e.ig_user_id is None
    assert e.send_state == "queued" and e.by_owner
    assert before - timedelta(seconds=1) <= e.send_after <= datetime.now(timezone.utc)
    assert e.reel_url == cfg.reels[0].url and e.reel_keyword == cfg.reels[0].keyword
    assert e.comment_id.startswith("manual:")
    assert db.rows("SELECT COUNT(*) AS n FROM requests")[0]["n"] == 0  # no DM, no conversation


def test_handle_is_optional(app):
    base, db, _ = app
    r = send(base, handle="")
    assert r.status_code == 200 and db.email_by_id(r.json()["id"]).instagram_handle == ""


@pytest.mark.parametrize("field, value", [
    ("name", "  "), ("email", "marwane at gmail"), ("reel", "nope"), ("handle", "not a handle!"),
    ("name", "x" * 61),
])
def test_bad_fields_are_refused_with_a_reason(app, field, value):
    base, db, _ = app
    r = send(base, **{field: value})
    assert r.status_code == 400 and field in r.json()["errors"]
    assert db.emails() == []


def test_same_address_and_reel_warns_before_sending_again(app):
    base, db, _ = app
    first = send(base).json()["id"]
    db.mark_email(first, "sent", at=datetime(2026, 11, 4, 14, 19, tzinfo=timezone.utc))
    again = send(base)
    assert again.status_code == 409
    dup = again.json()["duplicate"]
    assert dup["state"] == "sent" and dup["at"].startswith("2026-11-04T14:19")
    assert len(db.emails()) == 1
    forced = send(base, force=True)
    assert forced.status_code == 200 and len(db.emails()) == 2


def test_status_of_a_manual_email(app):
    base, db, _ = app
    eid = send(base).json()["id"]
    assert httpx.get(f"{base}/api/emails/{eid}").json() == {
        "id": eid, "state": "queued", "sent_at": None, "error": None}
    db.mark_email(eid, "sent", at=datetime(2026, 11, 4, 14, 19, tzinfo=timezone.utc))
    s = httpx.get(f"{base}/api/emails/{eid}").json()
    assert s["state"] == "sent" and s["sent_at"].startswith("2026-11-04T14:19")
    assert httpx.get(f"{base}/api/emails/999").status_code == 404


def test_manual_emails_show_as_added_by_owner(app):
    base, db, cfg = app
    send(base)
    now = datetime.now(timezone.utc)
    (item,) = activity(db, cfg, now)["items"]
    assert (item["type"], item["title"], item["handle"]) == (
        "email_captured", "Added by you", "marwanezb")
    (row,) = dashboard_state(db, cfg, now)["emails"]
    assert row["manual"] is True and row["name"] == "Marwane"


def test_page_has_the_send_material_panel():
    html = PAGE.read_text()
    for needle in ('id="manual-open"', 'id="manual"', "/api/emails/manual", "/api/emails/preview",
                   "Send material"):
        assert needle in html, needle
