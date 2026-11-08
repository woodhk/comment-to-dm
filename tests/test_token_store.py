import json
from datetime import datetime, timedelta, timezone

from igdm.token_store import load_token, needs_refresh, record_attempt, save_token

NOW = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)


def test_never_refreshed(tmp_path):
    assert needs_refresh(tmp_path / "token.json", NOW) is True


def test_refreshed_3_days_ago(tmp_path):
    p = tmp_path / "token.json"
    save_token(p, "t", NOW - timedelta(days=3), "env")
    assert needs_refresh(p, NOW) is False


def test_refreshed_8_days_ago(tmp_path):
    p = tmp_path / "token.json"
    save_token(p, "t", NOW - timedelta(days=8), "env")
    assert needs_refresh(p, NOW) is True


def test_load_token_file_wins_when_derived_from_current_env(tmp_path):
    p = tmp_path / "data" / "token.json"
    assert load_token("env-tok", p) == "env-tok"
    save_token(p, "file-tok", NOW, "env-tok")
    assert load_token("env-tok", p) == "file-tok"
    d = json.loads(p.read_text())
    assert d["token"] == "file-tok" and "env-tok" not in p.read_text()


def test_changed_env_token_ignores_stale_file(tmp_path):
    p = tmp_path / "token.json"
    save_token(p, "file-tok", NOW, "old-env")
    assert load_token("new-env", p) == "new-env"
    # a stale file also counts as never refreshed
    assert needs_refresh(p, NOW, env_token="new-env") is True


def test_corrupt_file_falls_back(tmp_path):
    p = tmp_path / "token.json"
    p.write_text("not json")
    assert load_token("env-tok", p) == "env-tok"
    assert needs_refresh(p, NOW) is True


def test_failed_refresh_backs_off_six_hours(tmp_path):
    p = tmp_path / "token.json"
    save_token(p, "t", NOW - timedelta(days=8), "env")
    record_attempt(p, NOW, "env")
    assert needs_refresh(p, NOW + timedelta(hours=5), env_token="env") is False
    assert needs_refresh(p, NOW + timedelta(hours=6, minutes=1), env_token="env") is True
    assert load_token("env", p) == "t"  # attempt record keeps the saved token
