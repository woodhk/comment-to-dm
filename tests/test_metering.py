from datetime import datetime, timezone

import pytest
from fakes import FakeClassifier, FakeWriter, repo_config

from igdm.config import Price
from igdm.db import Database
from igdm.jev import JevError
from igdm.llm import LlmReply
from igdm.metering import MeteredClassifier, MeteredWriter, cost

T = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    return Database(":memory:")


def test_metered_classifier_logs_each_call(db):
    inner = FakeClassifier(default_comment=0.9)
    inner.model = "jev-latest"
    m = MeteredClassifier(inner, db)
    reel = repo_config().reels[0]
    assert m.comment_wants_material("Tokyo", reel) == 0.9
    m.reply_intent("hi", "yes")
    m.route_reply([], "yes", {"yes": "a", "custom": "b"})
    rows = db.usage_rows()
    assert [(r["provider"], r["model"], r["purpose"], r["calls"]) for r in rows] == [
        ("typesafe", "jev-latest", "comment", 1), ("typesafe", "jev-latest", "intent", 1),
        ("typesafe", "jev-latest", "route", 1)]
    assert m.model == "jev-latest"


def test_failed_call_not_logged(db):
    inner = FakeClassifier()
    inner.model = "jev-latest"
    inner.raise_on.add("x")
    with pytest.raises(JevError):
        MeteredClassifier(inner, db).reply_intent("hi", "x")
    assert db.usage_rows() == []


def test_metered_writer_logs_tokens(db):
    w = MeteredWriter(FakeWriter([LlmReply("hey", True, False, 1200, 80)]), db)
    w.write([], repo_config().reels[0])
    (row,) = db.usage_rows()
    assert (row["provider"], row["model"], row["purpose"]) == ("fake", "fake-model", "reply")
    assert row["input_tokens"] == 1200 and row["output_tokens"] == 80


def test_cost_from_prices(db):
    db.log_usage("openai", "gpt-6-luna", "reply", input_tokens=1_000_000, output_tokens=0)
    (row,) = db.usage_rows()
    assert cost(row, {"gpt-6-luna": Price(per_m_input=0.5)}) == pytest.approx(0.5)


def test_cost_per_call(db):
    db.log_usage("typesafe", "jev-latest", "route", calls=1)
    (row,) = db.usage_rows()
    assert cost(row, {"jev-latest": Price(per_call=0.002)}) == pytest.approx(0.002)


def test_cost_none_without_price(db):
    db.log_usage("openai", "gpt-6-luna", "reply", input_tokens=10)
    assert cost(db.usage_rows()[0], {}) is None


def test_override_wins(db):
    db.log_usage("manual", "gpt-6-luna", "one-off", usd_override=1.25, note="translation")
    assert cost(db.usage_rows()[0], {"gpt-6-luna": Price(per_m_input=99)}) == 1.25


def test_usage_rows_since(db):
    db.log_usage("openai", "m", "reply", at=T)
    db.log_usage("openai", "m", "reply", at=T.replace(day=1))
    assert len(db.usage_rows(since=T.replace(day=2))) == 1


def test_prices_parsed(tmp_path):
    from fakes import ROOT

    from igdm.config import load_config
    p = tmp_path / "c.toml"
    p.write_text((ROOT / "tests" / "data" / "config.toml").read_text() +
                 '\n[prices."test-model"]\nper_m_input = 0.2\nper_m_output = 0.8\n')
    prices = load_config(p).prices
    assert prices["test-model"] == Price(0.0, 0.2, 0.8)
    assert prices["jev-latest"].per_call == 0.0001  # the real config's own prices still load


def test_metered_email_check_logged(db):
    inner = FakeClassifier()
    inner.model = "jev-latest"
    assert MeteredClassifier(inner, db).email_looks_real("a@b.com", "a@b.com") == 1.0
    (row,) = db.usage_rows()
    assert (row["provider"], row["purpose"], row["calls"]) == ("typesafe", "email", 1)


def test_metered_openai_classifier_logs_provider_and_tokens(db):
    import httpx

    from igdm.openai_classifier import OpenAIClassifier

    def h(req):
        return httpx.Response(200, json={
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"true": 0.9, "false": 0.1}'}]}],
            "usage": {"input_tokens": 120, "output_tokens": 8}})

    inner = OpenAIClassifier("k", "gpt-6-luna", owner_name="Sam", transport=httpx.MockTransport(h))
    m = MeteredClassifier(inner, db)
    assert m.comment_wants_material("hi", repo_config().reels[0]) == pytest.approx(0.9)
    (row,) = db.usage_rows()
    assert (row["provider"], row["model"], row["purpose"]) == ("openai", "gpt-6-luna", "comment")
    assert (row["input_tokens"], row["output_tokens"]) == (120, 8)


def test_metered_cheeky_logged_as_its_own_purpose(db):
    inner = FakeClassifier()
    inner.cheeky_probs["Tokyo clown"] = 0.9
    inner.model = "jev-latest"
    m = MeteredClassifier(inner, db)
    assert m.comment_is_cheeky("Tokyo clown", repo_config().reels[0]) == 0.9
    assert [r["purpose"] for r in db.usage_rows()] == ["cheeky"]
