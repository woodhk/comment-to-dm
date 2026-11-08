"""The bot picks up config.toml changes between ticks and keeps the old config if one fails."""

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fakes import ROOT, FakeClassifier, FakeInstagram

from igdm.config import load_config
from igdm.db import Database
from igdm.engine import Context
from igdm.instagram import InstagramError
from igdm.reload import CONFIG_ERROR_KEY, ConfigWatcher, apply_config_changes

NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)
NEW_REEL = '''

[[reels]]
key = "rome"
url = "https://www.instagram.com/reel/ROME1/"
keyword = "Rome"
offer = "the Rome slides"
material_url = "https://drive.google.com/x"
handle_comments_after = "2026-11-04T10:00:00"
'''


class Env:
    def __init__(self, tmp_path):
        self.path = tmp_path / "config.toml"
        self.text = (ROOT / "tests" / "data" / "config.toml").read_text()
        self.path.write_text(self.text)
        cfg = load_config(self.path)
        self.t = NOW
        self.ig = FakeInstagram()
        self.ig.media_ids["TESTREEL01"] = "m1"
        self.db = Database(":memory:")
        self.ctx = Context(
            config=cfg, db=self.db, ig=self.ig, jev=FakeClassifier(), now=lambda: self.t,
            sleep=lambda s: None, rng=random.Random(1), own_id="me1", own_username="alex",
            media_ids={"tokyo": "m1"})
        self.watcher = ConfigWatcher(self.path, cfg)

    def write(self, text):
        self.path.write_text(text)

    def apply(self):
        return apply_config_changes(self.ctx, self.watcher)

    def error(self):
        return self.db.kv_get(CONFIG_ERROR_KEY) or ""


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_unchanged_file_does_nothing(env):
    assert env.apply() is False
    assert env.ig.media_lookups == []


def test_new_reel_applied_and_resolved(env):
    env.ig.media_ids["ROME1"] = "m2"
    env.write(env.text + NEW_REEL)
    assert env.apply() is True
    assert [r.key for r in env.ctx.config.reels] == ["tokyo", "rome"]
    assert env.ctx.media_ids == {"tokyo": "m1", "rome": "m2"}
    assert env.apply() is False  # applied once


def test_existing_media_ids_not_looked_up_again(env):
    env.ig.media_ids["ROME1"] = "m2"
    env.write(env.text + NEW_REEL)
    env.apply()
    assert env.ig.media_lookups == ["ROME1"]


def test_bad_toml_keeps_old_config_and_sets_error(env):
    old = env.ctx.config
    env.write(env.text + "\n[[reels]\n")
    assert env.apply() is False
    assert env.ctx.config is old
    assert "config.toml" in env.error()


def test_unresolved_reel_left_out_and_sets_error(env):
    env.write(env.text + NEW_REEL)
    env.apply()
    assert [r.key for r in env.ctx.config.reels] == ["tokyo"]
    assert env.ctx.media_ids == {"tokyo": "m1"}
    assert '"rome"' in env.error() and "didn't match any of your posts" in env.error()


def test_instagram_error_keeps_old_config(env):
    def boom(shortcode):
        raise InstagramError(500, 1, "down")
    env.ig.find_media_id = boom
    env.write(env.text + NEW_REEL)
    env.apply()
    assert [r.key for r in env.ctx.config.reels] == ["tokyo"]
    assert "couldn't check" in env.error() and "status=" not in env.error()


def test_failed_state_retried_only_after_five_minutes(env):
    env.write(env.text + NEW_REEL)
    env.apply()
    env.t += timedelta(minutes=1)
    env.apply()
    assert env.ig.media_lookups == ["ROME1"]
    env.t += timedelta(minutes=5)
    env.apply()
    assert env.ig.media_lookups == ["ROME1", "ROME1"]


def test_success_clears_error(env):
    env.write(env.text + NEW_REEL)
    env.apply()
    assert env.error()
    env.ig.media_ids["ROME1"] = "m2"
    env.t += timedelta(minutes=6)
    assert env.apply() is True
    assert env.error() == ""


def test_db_path_kept_across_reload(env, tmp_path):
    env.ctx.config = replace(env.ctx.config, db_path=tmp_path / "dry.sqlite")
    env.write(env.text.replace('key = "tokyo"', 'key = "tokyo"\npaused = true'))
    assert env.apply() is True
    assert env.ctx.config.db_path == tmp_path / "dry.sqlite"


def test_paused_edit_takes_effect(env):
    env.write(env.text.replace('key = "tokyo"', 'key = "tokyo"\npaused = true'))
    assert env.apply() is True
    assert env.ctx.config.reels[0].paused is True
    assert env.ig.media_lookups == []


def test_fresh_watcher_applies_on_first_check(env):
    env.write(env.text.replace('key = "tokyo"', 'key = "tokyo"\npaused = true'))
    env.watcher = ConfigWatcher(env.path, env.ctx.config, applied=False)  # file state taken after the edit
    assert env.apply() is True
    assert env.ctx.config.reels[0].paused is True and env.ig.media_lookups == []


def test_pause_applies_even_when_another_reel_fails(env):
    env.write(env.text.replace('key = "tokyo"', 'key = "tokyo"\npaused = true') + NEW_REEL)
    env.apply()
    assert [r.key for r in env.ctx.config.reels] == ["tokyo"]
    assert env.ctx.config.reels[0].paused is True
    assert '"rome"' in env.error()


def test_failing_link_change_keeps_old_link_but_new_pause(env):
    env.write(env.text.replace("TESTREEL01", "NEWLINK1").replace('key = "tokyo"', 'key = "tokyo"\npaused = true'))
    env.apply()
    sz = env.ctx.config.reels[0]
    assert sz.shortcode == "TESTREEL01" and sz.paused is True
    assert env.ctx.media_ids == {"tokyo": "m1"}
