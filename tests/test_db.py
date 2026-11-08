from datetime import datetime, timedelta, timezone

import pytest

from igdm.db import Database, EmailRow, Request, Status

T0 = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)


def mk(cid="c1", username="sarah", status=Status.QUEUED, **kw):
    return Request(
        comment_id=cid, reel_key="tokyo", media_id="m1", username=username,
        commenter_id=kw.pop("commenter_id", "u1"), text="guide", commented_at=T0,
        status=status, **kw,
    )


@pytest.fixture
def db():
    return Database(":memory:")


def test_round_trip_tz_utc(db):
    sgt = timezone(timedelta(hours=8))
    r = mk(dm_due_at=datetime(2026, 11, 3, 9, 30, tzinfo=sgt), jev_yes=0.9)
    db.insert_request(r)
    got = db.get("c1")
    assert got.commented_at == T0 and got.commented_at.tzinfo is not None
    assert got.dm_due_at == datetime(2026, 11, 3, 1, 30, tzinfo=timezone.utc)
    assert got.dm_due_at.utcoffset() == timedelta(0)
    assert got.status is Status.QUEUED and got.jev_yes == 0.9
    assert got.dm1_sent_at is None
    assert db.has_comment("c1") and not db.has_comment("zz")


def test_naive_rejected(db):
    with pytest.raises(ValueError):
        db.insert_request(mk(dm_due_at=datetime(2026, 11, 3, 9, 30)))
    db.insert_request(mk())
    with pytest.raises(ValueError):
        db.update("c1", dm1_sent_at=datetime(2026, 11, 3))
    with pytest.raises(ValueError):
        db.dm1_times_since(datetime(2026, 11, 3))


def test_update_status_logs_event_and_rejects_unknown(db):
    db.insert_request(mk())
    db.update("c1", status=Status.AWAITING_YES, igsid="ig1")
    assert db.get("c1").igsid == "ig1"
    evs = db.events("c1")
    assert any(e[1] == "status:queued->awaiting_yes" for e in evs)
    assert evs[0][0].tzinfo is not None
    with pytest.raises(ValueError):
        db.update("c1", bogus=1)
    with pytest.raises(ValueError):
        db.update("c1", comment_id="x")


def test_with_status_ordering(db):
    db.insert_request(mk("a", status=Status.QUEUED))
    r = mk("b", status=Status.QUEUED)
    r.commented_at = T0 - timedelta(hours=1)
    db.insert_request(r)
    db.insert_request(mk("c", status=Status.DECLINED))
    assert [x.comment_id for x in db.with_status(Status.QUEUED)] == ["b", "a"]
    assert len(db.with_status(Status.QUEUED, Status.DECLINED)) == 3
    assert db.with_status() == []


def test_person_requests_case_insensitive(db):
    db.insert_request(mk("a", username="sarah"))
    assert [r.comment_id for r in db.person_requests("SARAH")] == ["a"]


def test_live_request_for_igsid(db):
    db.insert_request(mk("a", status=Status.AWAITING_YES, igsid="ig", dm1_sent_at=T0))
    db.insert_request(mk("b", status=Status.AWAITING_EMAIL, igsid="ig",
                         dm1_sent_at=T0 + timedelta(hours=1)))
    db.insert_request(mk("c", status=Status.DECLINED, igsid="ig",
                         dm1_sent_at=T0 + timedelta(hours=2)))
    assert db.live_request_for_igsid("ig").comment_id == "b"
    assert db.live_request_for_igsid("other") is None


def test_known_igsid_and_dm1_times(db):
    db.insert_request(mk("a", igsid="ig9", dm1_sent_at=T0))
    db.insert_request(mk("b", username="bob"))
    assert db.known_igsid("SARAH") == "ig9"
    assert db.known_igsid("bob") is None
    assert db.dm1_times_since(T0 - timedelta(minutes=1)) == [T0]
    assert db.dm1_times_since(T0 + timedelta(minutes=1)) == []


def test_bot_sent_similar(db):
    db.record_sent("mid1", "ig", "c1", "hello there", T0)
    assert db.is_bot_message("mid1") and not db.is_bot_message("mid2")
    assert db.bot_sent_similar("ig", "hello there", T0 + timedelta(seconds=100))
    assert not db.bot_sent_similar("ig", "hello there", T0 + timedelta(seconds=200))
    assert not db.bot_sent_similar("other", "hello there", T0 + timedelta(seconds=100))
    assert not db.bot_sent_similar("ig", "different", T0 + timedelta(seconds=10))
    assert db.bot_sent_similar("ig", "hello there", T0 - timedelta(seconds=100))


def test_emails(db):
    kw = dict(comment_id="c1", handle="sarah", igsid="ig", email="s@x.com",
              provided_at=T0, reel_url="http://r", keyword="guide")
    i = db.add_email(**kw)
    assert db.add_email(**{**kw, "email": "other@x.com"}) == i
    j = db.add_email(**{**kw, "comment_id": "c2", "email": "s2@x.com",
                        "provided_at": T0 + timedelta(hours=1)})
    assert j != i
    assert len(db.emails()) == 2
    row = db.emails()[0]
    assert isinstance(row, EmailRow) and row.emailed_at is None and row.email == "s@x.com"
    assert db.latest_email_for("SARAH") == "s2@x.com"
    assert db.latest_email_for("nobody") is None
    assert db.mark_emailed([i], T0) == 1
    assert [e.id for e in db.emails(pending_only=True)] == [j]
    assert db.mark_emailed(None, T0 + timedelta(days=1)) == 1
    assert db.mark_emailed(None, T0 + timedelta(days=2)) == 0
    assert db.emails(pending_only=True) == []
    assert db.emails()[0].emailed_at == T0


def test_public_replies_kv_counts(db):
    for i in range(3):
        db.record_public_reply(f"r{i}", T0 + timedelta(minutes=i))
    assert db.recent_public_replies(2) == ["r2", "r1"]
    assert db.kv_get("k") is None
    db.kv_set("k", "1")
    db.kv_set("k", "2")
    assert db.kv_get("k") == "2"
    db.insert_request(mk("a"))
    db.insert_request(mk("b"))
    db.insert_request(mk("c", status=Status.DECLINED))
    assert db.status_counts() == {"queued": 2, "declined": 1}
    db.log_event(None, "boot", "x")
    assert [e[1:] for e in db.events(None)] == [("boot", "x")]


def test_file_path_creates_parent(tmp_path):
    d = Database(tmp_path / "sub" / "x.db")
    d.kv_set("a", "b")
    assert (tmp_path / "sub" / "x.db").exists()


def test_record_sent_and_update_is_atomic(db):
    db.insert_request(mk(status=Status.AWAITING_YES))
    db.record_sent_and_update("mid1", "ig1", "c1", "hello", T0, status=Status.FOLLOWUP1_SENT,
                              last_user_msg_at=T0)
    assert db.is_bot_message("mid1")
    r = db.get("c1")
    assert r.status is Status.FOLLOWUP1_SENT and r.last_user_msg_at == T0
    assert any(e[1] == "status:awaiting_yes->followup1_sent" for e in db.events("c1"))
    with pytest.raises(ValueError):
        db.record_sent_and_update("mid2", "ig1", "c1", "x", T0, bogus=1)
    assert not db.is_bot_message("mid2")
    with pytest.raises(KeyError):
        db.record_sent_and_update("mid3", "ig1", "nope", "x", T0, status=Status.DECLINED)
    assert not db.is_bot_message("mid3")


def test_second_connection_reads_while_first_open(tmp_path):
    p = tmp_path / "x.sqlite"
    a = Database(p)
    a.insert_request(mk())
    b = Database(p)
    assert b.get("c1").username == "sarah"
    assert a._c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert a._c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    a.insert_request(mk("c2"))  # writer still works while a reader is open
    assert b.has_comment("c2")


# ---- email outbox (Task 3)

def _email(db, cid="c1", **kw):
    return db.add_email(comment_id=cid, handle="sarah", igsid="u1", email="s@x.com",
                        provided_at=T0, reel_url="https://r", keyword="Tokyo", **kw)


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT, instagram_handle TEXT NOT NULL,
      ig_user_id TEXT, email TEXT NOT NULL, provided_at TEXT NOT NULL, reel_url TEXT NOT NULL,
      reel_keyword TEXT NOT NULL, comment_id TEXT NOT NULL UNIQUE, emailed_at TEXT);
    INSERT INTO emails (instagram_handle, email, provided_at, reel_url, reel_keyword, comment_id)
      VALUES ('peder', 'p@x.com', '2026-11-03T10:24:58+00:00', 'https://r', 'Tokyo', 'c9');
    """)
    c.commit(); c.close()
    db = Database(p)
    (row,) = db.emails()
    assert row.send_state == "manual" and row.attempts == 0 and row.name is None
    assert row.send_after is None


def test_add_email_without_send_after_is_manual(db):
    _email(db)
    assert db.emails()[0].send_state == "manual"


def test_add_email_with_send_after_is_queued(db):
    _email(db, name="Sam", send_after=T0 + timedelta(minutes=2))
    row = db.emails()[0]
    assert row.send_state == "queued" and row.name == "Sam"
    assert row.send_after == T0 + timedelta(minutes=2)


def test_due_emails_respects_send_after(db):
    _email(db, send_after=T0 + timedelta(minutes=2))
    assert db.due_emails(T0 + timedelta(minutes=1)) == []
    assert [e.comment_id for e in db.due_emails(T0 + timedelta(minutes=2))] == ["c1"]


def test_manual_rows_never_due(db):
    _email(db)
    assert db.due_emails(T0 + timedelta(days=1)) == []


def test_mark_sent_sets_emailed_at(db):
    i = _email(db, send_after=T0)
    db.mark_email(i, "sent", at=T0 + timedelta(minutes=3))
    row = db.emails()[0]
    assert row.send_state == "sent" and row.emailed_at == T0 + timedelta(minutes=3)
    assert db.emails(pending_only=True) == []


def test_mark_email_bumps_attempts_and_error(db):
    i = _email(db, send_after=T0)
    db.mark_email(i, "queued", error="nope", bump_attempts=True)
    row = db.emails()[0]
    assert row.attempts == 1 and row.last_error == "nope" and row.send_state == "queued"


def test_mark_emailed_sets_sent_state(db):
    _email(db)
    db.mark_emailed(None, T0)
    assert db.emails()[0].send_state == "sent"


def test_queue_now(db):
    i = _email(db)
    assert db.queue_now(i, T0) is True
    assert [e.id for e in db.due_emails(T0)] == [i]
    db.mark_email(i, "sent", at=T0)
    assert db.queue_now(i, T0) is False  # never re-queues a sent email


# ---- flags, resuming, caps (Task 5)

def test_request_migration_adds_columns(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE requests (comment_id TEXT PRIMARY KEY, reel_key TEXT NOT NULL,
      media_id TEXT NOT NULL, username TEXT NOT NULL, commenter_id TEXT, text TEXT NOT NULL,
      commented_at TEXT NOT NULL, status TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'normal',
      jev_yes REAL, dm_due_at TEXT, igsid TEXT, dm1_text TEXT, dm1_sent_at TEXT,
      last_user_msg_at TEXT, note TEXT, updated_at TEXT);
    INSERT INTO requests (comment_id, reel_key, media_id, username, text, commented_at, status)
      VALUES ('c1', 'tokyo', 'm1', 'sarah', 'Tokyo', '2026-11-03T01:00:00+00:00', 'needs_owner');
    """)
    c.commit(); c.close()
    r = Database(p).get("c1")
    assert r.asks == 0 and r.llm_replies == 0 and r.paused_from is None and r.seen_until is None
    assert r.flag_reason is None and r.flag_todo is None and r.last_their_text is None


def test_paused_from_recorded_on_flag(db):
    db.insert_request(mk(status=Status.AWAITING_EMAIL))
    db.flag("c1", "you", "complaint or upset", "reply yourself")
    r = db.get("c1")
    assert r.status is Status.NEEDS_OWNER and r.paused_from is Status.AWAITING_EMAIL
    assert r.flag_reason == "complaint or upset" and r.flag_todo == "reply yourself"
    assert r.note == "complaint or upset"


def test_paused_from_recorded_by_plain_update(db):
    db.insert_request(mk(status=Status.AWAITING_YES))
    db.update("c1", status=Status.NEEDS_OWNER, note="x")
    assert db.get("c1").paused_from is Status.AWAITING_YES


def test_paused_from_not_set_from_non_live(db):
    db.insert_request(mk(status=Status.QUEUED))
    db.update("c1", status=Status.NEEDS_OWNER)
    assert db.get("c1").paused_from is None


def test_flag_info_sets_needs_info_and_newest(db):
    db.insert_request(mk(status=Status.AWAITING_EMAIL))
    db.flag("c1", "info", "email is in their bio", "find it", newest=T0)
    r = db.get("c1")
    assert r.status is Status.NEEDS_INFO and r.last_user_msg_at == T0
    from igdm.db import FOR_OWNER
    assert Status.NEEDS_INFO in FOR_OWNER


def test_dm1_cap_ignores_hand_rows(db):
    db.insert_request(mk("h1", kind="hand", status=Status.AWAITING_YES, dm1_sent_at=T0))
    db.insert_request(mk("c2", status=Status.AWAITING_YES, dm1_sent_at=T0))
    assert db.dm1_times_since(T0 - timedelta(hours=1)) == [T0]


def test_migration_marks_already_emailed_rows_sent(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT, instagram_handle TEXT NOT NULL,
      ig_user_id TEXT, email TEXT NOT NULL, provided_at TEXT NOT NULL, reel_url TEXT NOT NULL,
      reel_keyword TEXT NOT NULL, comment_id TEXT NOT NULL UNIQUE, emailed_at TEXT);
    INSERT INTO emails (instagram_handle, email, provided_at, reel_url, reel_keyword, comment_id,
      emailed_at) VALUES ('peder', 'p@x.com', '2026-11-03T10:24:58+00:00', 'r', 'Tokyo', 'c9',
      '2026-11-03T15:00:00+00:00');
    """)
    c.commit(); c.close()
    (row,) = Database(p).emails()
    assert row.send_state == "sent"


# ---- by_owner (Reels tab)

def test_add_email_not_by_owner_by_default(db):
    assert db.email_by_id(_email(db)).by_owner is False


def test_resolve_with_email_is_by_owner(db):
    db.insert_request(mk(status=Status.NEEDS_INFO))
    i = db.resolve_with_email("c1", "s@x.com", now=T0, delay_minutes=2,
                              reel_url="https://r", keyword="Tokyo")
    assert db.email_by_id(i).by_owner is True


def test_queue_now_marks_by_owner(db):
    i = _email(db)
    assert db.queue_now(i, T0)
    row = db.email_by_id(i)
    assert row.by_owner is True and row.send_state == "queued"


def test_old_database_gets_by_owner_column(tmp_path):
    import sqlite3
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT, instagram_handle TEXT NOT NULL,
      ig_user_id TEXT, email TEXT NOT NULL, provided_at TEXT NOT NULL, reel_url TEXT NOT NULL,
      reel_keyword TEXT NOT NULL, comment_id TEXT NOT NULL UNIQUE, emailed_at TEXT);
    INSERT INTO emails (instagram_handle, email, provided_at, reel_url, reel_keyword, comment_id)
      VALUES ('peder', 'p@x.com', '2026-11-03T10:24:58+00:00', 'https://r', 'Tokyo', 'c9');
    """)
    c.commit(); c.close()
    (row,) = Database(p).emails()
    assert row.by_owner is False


def test_old_public_replies_are_matched_to_the_dm_just_before_them(tmp_path):
    p = tmp_path / "old.sqlite"
    db = Database(p)
    db._c.execute("ALTER TABLE public_replies DROP COLUMN comment_id")  # as before the upgrade
    t = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
    for cid, at in (("c1", t), ("c2", t + timedelta(minutes=3))):
        db.insert_request(Request(comment_id=cid, reel_key="tokyo", media_id="m1",
                                  username=cid, commenter_id=None, text="x", commented_at=t,
                                  status=Status.AWAITING_YES, dm1_sent_at=at))
    db._c.execute("INSERT INTO public_replies (base_text, at) VALUES (?,?)",
                  ("check dms", (t + timedelta(seconds=8)).isoformat()))
    db._c.execute("INSERT INTO public_replies (base_text, at) VALUES (?,?)",
                  ("orphan", (t + timedelta(hours=1)).isoformat()))
    db._c.close()
    rows = Database(p).rows("SELECT base_text, comment_id FROM public_replies ORDER BY id")
    assert [tuple(r) for r in rows] == [("check dms", "c1"), ("orphan", None)]


def test_emails_sent_before_the_upgrade_never_get_a_sent_dm(tmp_path):
    p = tmp_path / "old.sqlite"
    db = Database(p)
    db._c.execute("ALTER TABLE emails DROP COLUMN confirm_state")  # as before the upgrade
    i = db.add_email(comment_id="c1", handle="a", igsid="u1", email="a@x.com", provided_at=T0,
                     reel_url="u", keyword="k", send_after=T0)
    db.mark_email(i, "sent", at=T0)
    db._c.close()
    db = Database(p)
    assert db.email_by_id(i).confirm_state is None and db.emails_to_confirm() == []


def test_only_emails_marked_to_confirm_are_listed():
    db = Database(":memory:")
    i = db.add_email(comment_id="c1", handle="a", igsid="u1", email="a@x.com", provided_at=T0,
                     reel_url="u", keyword="k", send_after=T0)
    db.mark_email(i, "sent", at=T0)
    assert db.emails_to_confirm() == []
    db.set_confirm(i, "pending")
    assert [e.id for e in db.emails_to_confirm()] == [i]
    db.set_confirm(i, "sent")
    assert db.emails_to_confirm() == []


def test_set_send_after_moves_a_queued_email(db):
    i = _email(db, send_after=T0)
    db.set_send_after(i, T0 + timedelta(seconds=25))
    assert db.emails()[0].send_after == T0 + timedelta(seconds=25)


def test_set_send_after_leaves_a_sent_email_alone(db):
    i = _email(db, send_after=T0)
    db.mark_email(i, "sent", at=T0)
    db.set_send_after(i, T0 + timedelta(seconds=25))
    assert db.emails()[0].send_after == T0


# ---- database upgrades (data steps)

_RENAMED = {
    Status.LEFT_FOR_OWNER: "left_for_alex",
    Status.NEEDS_OWNER: "needs_alex",
    Status.HANDLED_BY_OWNER: "handled_by_alex",
    Status.DONE_BY_OWNER: "done_by_alex",
}


def _old_db(path):
    """A database as the bot left it before upgrade 1: *_alex statuses and emails.by_alex."""
    import sqlite3
    db = Database(path)
    for i, new in enumerate(_RENAMED, 1):
        db.insert_request(mk(cid=f"c{i}", status=Status.AWAITING_YES))
        db.update(f"c{i}", status=new)  # logs status:awaiting_yes-><new>
    db.update("c2", paused_from=Status.AWAITING_EMAIL)
    db.insert_request(mk(cid="c9", status=Status.QUEUED, paused_from=Status.NEEDS_OWNER))
    db.add_email(comment_id="c1", handle="sarah", igsid="u1", email="s@x.com", provided_at=T0,
                 reel_url="https://r", keyword="Tokyo", send_after=T0, by_owner=True)
    db.add_email(comment_id="c2", handle="bob", igsid="u2", email="b@x.com", provided_at=T0,
                 reel_url="https://r", keyword="Tokyo")
    db.log_event("c3", "dm1_failed", "detail mentions needs_owner")  # not a status event
    db._c.close()
    c = sqlite3.connect(path, isolation_level=None)
    c.execute("ALTER TABLE emails RENAME COLUMN by_owner TO by_alex")
    for new, old in _RENAMED.items():
        c.execute("UPDATE requests SET status=? WHERE status=?", (old, str(new)))
        c.execute("UPDATE requests SET paused_from=? WHERE paused_from=?", (old, str(new)))
        c.execute("UPDATE events SET event=REPLACE(event, ?, ?) WHERE event LIKE 'status:%'",
                  (str(new), old))
    c.execute("PRAGMA user_version = 0")
    c.close()
    return path


def _snapshot(db):
    return (db.rows("SELECT comment_id, status, paused_from FROM requests ORDER BY comment_id"),
            db.rows("SELECT id, event, detail FROM events ORDER BY id"),
            [(e.id, e.by_owner) for e in db.emails()])


def _user_version(db):
    return db.rows("PRAGMA user_version")[0][0]


def test_old_database_upgrades(tmp_path):
    import sqlite3
    p = _old_db(tmp_path / "old.sqlite")
    raw = sqlite3.connect(p)
    assert raw.execute("SELECT status FROM requests WHERE comment_id='c2'").fetchone() == ("needs_alex",)
    assert raw.execute("SELECT COUNT(*) FROM events WHERE event LIKE '%_alex'").fetchone() == (4,)
    raw.close()

    db = Database(p)
    assert {cid: db.get(cid).status for cid in ("c1", "c2", "c3", "c4")} == {
        "c1": Status.LEFT_FOR_OWNER, "c2": Status.NEEDS_OWNER,
        "c3": Status.HANDLED_BY_OWNER, "c4": Status.DONE_BY_OWNER}
    assert db.get("c2").paused_from is Status.AWAITING_EMAIL
    assert db.get("c9").paused_from is Status.NEEDS_OWNER
    assert [e for _, e, _ in db.events("c2")] == ["status:awaiting_yes->needs_owner"]
    assert db.rows("SELECT COUNT(*) FROM events WHERE event LIKE '%alex%'")[0][0] == 0
    assert db.events("c3")[-1][1:] == ("dm1_failed", "detail mentions needs_owner")
    assert {e.instagram_handle: e.by_owner for e in db.emails()} == {"sarah": True, "bob": False}
    assert _user_version(db) == 1


def test_database_upgrade_runs_once(tmp_path):
    p = _old_db(tmp_path / "old.sqlite")
    first = Database(p)
    after = _snapshot(first)
    again = Database(p)  # a second open, while the first is still open
    assert _user_version(again) == 1 and _snapshot(again) == after
    first._c.close(); again._c.close()
    third = Database(p)
    assert _user_version(third) == 1 and _snapshot(third) == after


def test_new_database_starts_current(tmp_path):
    from igdm.db import DATA_STEPS
    assert len(DATA_STEPS) >= 1
    assert _user_version(Database(tmp_path / "new.sqlite")) == len(DATA_STEPS)
    assert _user_version(Database(":memory:")) == len(DATA_STEPS)
