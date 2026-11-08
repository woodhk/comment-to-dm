import random
from datetime import datetime, timedelta, timezone

from fakes import FakeClassifier, FakeInstagram, repo_config

import igdm.engine as engine
from igdm.config import load_config
from igdm.db import Database
from igdm.engine import Context, tick
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)


def make_ctx(ig):
    return Context(
        config=repo_config(), db=Database(":memory:"), ig=ig,
        jev=FakeClassifier(), now=lambda: NOW, sleep=lambda s: None, rng=random.Random(1),
        own_id="me1", own_username="alex", media_ids={"tokyo": "m1"},
        comment_poll_interval=timedelta(0),
    )


def test_tick_isolates_phase_failures():
    class BrokenIg(FakeInstagram):
        def list_comments(self, media_id):
            raise RuntimeError("boom")

    ig = BrokenIg()
    calls = []
    orig = ig.list_conversations
    ig.list_conversations = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    result = tick(make_ctx(ig))
    assert result["ingest"].startswith("error: ")
    assert "boom" in result["ingest"]
    assert calls  # process_replies still ran
    assert set(result) == {"pauses", "ingest", "dm", "hand_dms", "replies", "followups", "gotits", "email", "milestones"}


def test_tick_phase_order(monkeypatch):
    order = []
    for name, ret in [("sync_pauses", 0), ("ingest_comments", 0), ("send_due_dm", False),
                      ("process_replies", 0), ("resume_followups", 0)]:
        monkeypatch.setattr(engine, name, lambda ctx, n=name, r=ret: (order.append(n), r)[1])
    tick(make_ctx(FakeInstagram()))
    assert order == ["sync_pauses", "ingest_comments", "send_due_dm", "process_replies", "resume_followups"]


def test_tick_polls_comments_every_tick():
    t = [NOW]
    ig = FakeInstagram()
    comment_calls, conv_calls = [], []
    oc, ov = ig.list_comments, ig.list_conversations
    ig.list_comments = lambda *a: (comment_calls.append(1), oc(*a))[1]
    ig.list_conversations = lambda *a: (conv_calls.append(1), ov(*a))[1]
    ctx = make_ctx(ig)
    ctx.now = lambda: t[0]
    ctx.comment_poll_interval = engine.COMMENT_POLL_INTERVAL
    for _ in range(5):  # five 60 s ticks
        tick(ctx)
        t[0] += timedelta(seconds=60)
    assert len(conv_calls) == 5 and len(comment_calls) == 5


BLOCKED = "Cannot call API for app 1000000000000001 on behalf of user 1000000000000002"


def test_blocked_error_sets_marker_and_success_clears():
    from igdm.instagram import InstagramError
    from igdm.engine import IG_BLOCKED_KEY, process_replies

    class BlockedIg(FakeInstagram):
        blocked = True

        def list_conversations(self, own_id, since):
            if self.blocked:
                raise InstagramError(400, 200, BLOCKED)
            return super().list_conversations(own_id, since)

    ig = BlockedIg()
    ctx = make_ctx(ig)
    process_replies(ctx)
    assert ctx.db.kv_get(IG_BLOCKED_KEY) == NOW.isoformat()
    later = NOW + timedelta(minutes=5)
    ctx.now = lambda: later
    process_replies(ctx)
    assert ctx.db.kv_get(IG_BLOCKED_KEY) == NOW.isoformat()  # keeps the first time it happened
    ig.blocked = False
    process_replies(ctx)
    assert ctx.db.kv_get(IG_BLOCKED_KEY) == ""


def test_other_errors_do_not_set_marker():
    from igdm.instagram import InstagramError
    from igdm.engine import IG_BLOCKED_KEY, process_replies

    class FlakyIg(FakeInstagram):
        def list_conversations(self, own_id, since):
            raise InstagramError(500, 1, "temporary")

    ctx = make_ctx(FlakyIg())
    process_replies(ctx)
    assert not ctx.db.kv_get(IG_BLOCKED_KEY)


def test_is_access_blocked():
    from igdm.instagram import InstagramError, is_access_blocked
    assert is_access_blocked(InstagramError(400, 200, BLOCKED))
    assert is_access_blocked(InstagramError(400, 200, "API access blocked."))
    assert not is_access_blocked(InstagramError(400, 100, "bad request"))
    assert not is_access_blocked(RuntimeError("API access blocked"))


def test_tick_writes_a_heartbeat():
    import json
    from igdm.activity import HEARTBEAT_KEY
    ctx = make_ctx(FakeInstagram())
    tick(ctx)
    beat = json.loads(ctx.db.kv_get(HEARTBEAT_KEY))
    assert beat == {"start": NOW.isoformat(), "end": NOW.isoformat()}


def test_crashed_phase_is_logged_for_the_feed():
    class BrokenIg(FakeInstagram):
        def list_comments(self, media_id):
            raise RuntimeError("boom")

    ctx = make_ctx(BrokenIg())
    tick(ctx)
    assert any(e == "phase_failed" and "ingest" in d and "boom" in d
               for _, e, d in ctx.db.events(None))
