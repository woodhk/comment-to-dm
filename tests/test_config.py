import re
import tomllib
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

from igdm.cli import CONNECT_ENV, FB_ENV, SECRET_ENV
from igdm.config import CONFIG_VERSION, DEFAULT_STYLE, Owner, load_config

ROOT = Path(__file__).resolve().parent.parent


def test_loads_repo_config():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    r = cfg.reels[0]
    assert r.shortcode == "TESTREEL01"
    assert r.handle_comments_after == datetime(2026, 11, 3, 6, 0, tzinfo=UTC)  # 14:00 SGT
    assert cfg.pacing.max_dms_per_hour == 25
    assert cfg.pacing.quiet_start == time(2, 0) and cfg.pacing.quiet_end == time(8, 30)
    assert len(cfg.messages.public_replies) == 40
    assert cfg.mode == "full"
    assert cfg.db_path == ROOT / "tests" / "data" / "data" / "igdm.sqlite"


def test_live_config_loads():
    """The real config.toml (however many reels the owner has added) is valid."""
    live = ROOT / "config.toml"
    if not live.exists():
        pytest.skip("no config.toml here (the public repo has only config.example.toml)")
    cfg = load_config(live)
    # A creator with no reels yet is still a valid setup, so an update must not fail on it.
    assert all(r.keyword and r.url and r.material_url for r in cfg.reels)
    assert len({r.key for r in cfg.reels}) == len(cfg.reels)


def test_example_config_loads():
    path = ROOT / "config.example.toml"
    cfg = load_config(path)
    assert tomllib.loads(path.read_text())["config_version"] == CONFIG_VERSION
    assert cfg.replies.enabled is False
    assert cfg.owner.name == "Your name" and cfg.owner.style == DEFAULT_STYLE
    assert [r.key for r in cfg.reels] == ["first"]


PERSONAL_FILE = ROOT / "tests" / "data" / "personal.txt"  # gitignored: the owner's own details


def _personal() -> list[str]:
    """The owner's private strings (one per line), or [] when the local file isn't there."""
    if not PERSONAL_FILE.exists():
        return []
    return [ln.strip() for ln in PERSONAL_FILE.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("name", ["config.example.toml", ".env.example"])
def test_example_config_names_no_one(name):
    text = (ROOT / name).read_text()
    from test_neutral_names import ALEX_MARKERS

    assert ALEX_MARKERS.search(text) is None
    assert re.search(r"(?i)\balex\b|drive\.google", text) is None
    assert "\u2014" not in text and "\u2013" not in text


@pytest.mark.parametrize("name", ["config.example.toml", ".env.example"])
def test_example_config_has_no_personal_details(name):
    personal = _personal()
    if not personal:
        pytest.skip("tests/data/personal.txt is not here (it is gitignored, owner only)")
    text = (ROOT / name).read_text()
    assert [p for p in personal if p in text] == []


def test_personal_file_missing_means_no_personal_strings(monkeypatch, tmp_path):
    monkeypatch.setitem(globals(), "PERSONAL_FILE", tmp_path / "nope.txt")
    assert _personal() == []
    (tmp_path / "p.txt").write_text("one\n\n  two \n")
    monkeypatch.setitem(globals(), "PERSONAL_FILE", tmp_path / "p.txt")
    assert _personal() == ["one", "two"]


def test_example_env_lists_every_key():
    text = (ROOT / ".env.example").read_text()
    for name in (*SECRET_ENV, *CONNECT_ENV, *FB_ENV, "ALERT_EMAIL"):
        assert name in text, name


def _write(tmp_path, **repl):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    for a, b in repl.items():
        key = {"mode": 'mode = "full"', "url": "https://www.instagram.com/reel/TESTREEL01/"}[a]
        assert key in text
        text = text.replace(key, b)
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_rejects_bad_mode(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, mode='mode = "bogus"'))


def test_rejects_url_without_shortcode(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, url="https://www.instagram.com/someone/"))


def test_rejects_missing_reel_field(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text((ROOT / "tests" / "data" / "config.toml").read_text().replace('keyword = "Tokyo"\n', ""))
    with pytest.raises(ValueError):
        load_config(p)


def test_rejects_duplicate_reel_keys(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    p = tmp_path / "c.toml"
    p.write_text(text + "\n" + text[text.index("[[reels]]"):])
    with pytest.raises(ValueError):
        load_config(p)


# ---- platform change: Facebook Login
def _cfg_text(tmp_path, old, new):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    assert old in text
    p = tmp_path / "c.toml"
    p.write_text(text.replace(old, new))
    return p


def test_repo_config_uses_facebook_login_and_page_node():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    assert cfg.api == "facebook_login"
    assert cfg.messaging_node == "page"


def test_api_and_node_default_when_absent(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    text = "\n".join(l for l in text.splitlines()
                     if not l.startswith(("api =", "messaging_node =")))
    p = tmp_path / "c.toml"
    p.write_text(text)
    cfg = load_config(p)
    assert cfg.api == "facebook_login" and cfg.messaging_node == "page"


def test_email_threshold_default(tmp_path):
    assert load_config(ROOT / "tests" / "data" / "config.toml").email_threshold == 0.5
    p = _cfg_text(tmp_path, "reply_threshold = 0.8", "reply_threshold = 0.8\nemail_threshold = 0.3")
    assert load_config(p).email_threshold == 0.3


def test_instagram_login_and_ig_node_accepted(tmp_path):
    p = _cfg_text(tmp_path, 'api = "facebook_login"', 'api = "instagram_login"')
    assert load_config(p).api == "instagram_login"
    p = _cfg_text(tmp_path, 'messaging_node = "page"', 'messaging_node = "ig"')
    assert load_config(p).messaging_node == "ig"


@pytest.mark.parametrize("old,new", [
    ('api = "facebook_login"', 'api = "bogus"'),
    ('messaging_node = "page"', 'messaging_node = "me"'),
])
def test_rejects_bad_api_or_node(tmp_path, old, new):
    with pytest.raises(ValueError):
        load_config(_cfg_text(tmp_path, old, new))


def test_email_section():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    assert cfg.email.from_address == "owner@example.com"
    assert cfg.email.delay_minutes == 2
    assert cfg.email.enabled is True
    assert cfg.email.smtp_host == "smtp.gmail.com" and cfg.email.smtp_port == 465


def test_reel_email_template_overrides_messages(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    extra = '''
[[reels]]
key = "other"
url = "https://www.instagram.com/reel/ABC123/"
keyword = "Other"
offer = "x"
material_url = "https://example.com"
handle_comments_after = "2026-11-03T14:00:00"
'''
    p = tmp_path / "c.toml"
    p.write_text(text + extra)
    cfg = load_config(p)
    tokyo, other = cfg.reels
    assert other.email_subject == cfg.messages.email_subject
    assert other.email_body == cfg.messages.email_body
    assert other.hand_dm_phrases == ()
    assert tokyo.hand_dm_phrases == ("from that Tokyo reel",)


def test_reel_own_subject(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text().replace(
        'included with the guide."\nemail_subject = "your {keyword} guide"',
        'since the guide is mostly photos."\nemail_subject = "s {keyword}"')
    p = tmp_path / "c.toml"
    p.write_text(text)
    assert load_config(p).reels[0].email_subject == "s {keyword}"


def test_repo_bank_loads():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    ids = [e.id for e in cfg.replies.bank]
    assert "yes" in ids and len(ids) == len(set(ids))
    assert cfg.replies.max_asks == 3 and cfg.replies.max_llm_replies == 2
    assert cfg.replies.enabled is True  # Alex reviewed the wording on 4 Nov 2026


def test_repo_bank_follows_voice_rules():
    from igdm.voice import problems
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    for e in cfg.replies.bank:
        for text in e.replies:
            assert problems(text, cfg.replies.deny_words) == [], (e.id, text)


def _bank_cfg(tmp_path, bank):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    text = text[:text.index("[replies]")] + "[replies]\nenabled = true\n" + bank + "\n" + \
        text[text.index("[[reels]]"):]
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_bank_requires_yes(tmp_path):
    p = _bank_cfg(tmp_path, '[[replies.bank]]\nid = "free"\nstage = "any"\nwhen = "x"\nreplies = ["y"]\n')
    with pytest.raises(ValueError, match="yes"):
        load_config(p)


def test_bank_rejects_builtin_id(tmp_path):
    p = _bank_cfg(tmp_path, '[[replies.bank]]\nid = "yes"\nstage = "offer"\nwhen = "x"\n'
                            '[[replies.bank]]\nid = "custom"\nstage = "any"\nwhen = "x"\nreplies = ["y"]\n')
    with pytest.raises(ValueError, match="custom"):
        load_config(p)


def test_bank_entry_needs_replies(tmp_path):
    p = _bank_cfg(tmp_path, '[[replies.bank]]\nid = "yes"\nstage = "offer"\nwhen = "x"\n'
                            '[[replies.bank]]\nid = "free"\nstage = "any"\nwhen = "x"\n')
    with pytest.raises(ValueError, match="free"):
        load_config(p)


def _with_paused(tmp_path, value):
    text = (ROOT / "tests" / "data" / "config.toml").read_text().replace('key = "tokyo"', f'key = "tokyo"\npaused = {value}')
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_reel_not_paused_by_default():
    assert load_config(ROOT / "tests" / "data" / "config.toml").reels[0].paused is False


def test_reel_paused_parsed(tmp_path):
    assert load_config(_with_paused(tmp_path, "true")).reels[0].paused is True


def test_reel_paused_must_be_bool(tmp_path):
    with pytest.raises(ValueError, match="paused"):
        load_config(_with_paused(tmp_path, '"yes"'))


def _pacing_variant(tmp_path, old, new):
    from fakes import ROOT
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    assert old in text
    p = tmp_path / "c.toml"
    p.write_text(text.replace(old, new))
    return load_config(p).pacing


def _pacing_without(tmp_path, keys, extra=""):
    """The repo config with these [pacing] keys removed and `extra` lines added."""
    from fakes import ROOT
    lines = [ln for ln in (ROOT / "tests" / "data" / "config.toml").read_text().splitlines()
             if not any(ln.startswith(k + " ") for k in keys)]
    text = "\n".join(lines).replace("[pacing]\n", "[pacing]\n" + extra, 1)
    p = tmp_path / "c.toml"
    p.write_text(text)
    return load_config(p).pacing


DELAYS = ("dm_delay_seconds", "dm_delay_minutes")
CHAT = ("chat_window_seconds", "chat_poll_seconds", "chat_watch_seconds", "chat_settle_seconds")


def test_dm_delay_is_in_seconds(tmp_path):
    pc = _pacing_without(tmp_path, DELAYS, "dm_delay_seconds = [40, 80]\n")
    assert pc.dm_delay_seconds == (40.0, 80.0)


def test_old_dm_delay_minutes_still_loads(tmp_path):
    pc = _pacing_without(tmp_path, DELAYS, "dm_delay_minutes = [30, 43]\n")
    assert pc.dm_delay_seconds == (1800.0, 2580.0)


def test_dm_delay_seconds_wins_over_minutes(tmp_path):
    pc = _pacing_without(tmp_path, DELAYS, "dm_delay_seconds = [50, 70]\ndm_delay_minutes = [30, 43]\n")
    assert pc.dm_delay_seconds == (50.0, 70.0)


def test_missing_dm_delay_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="dm_delay_seconds"):
        _pacing_without(tmp_path, DELAYS)


def test_chat_settings_load(tmp_path):
    pc = _pacing_without(tmp_path, CHAT, "chat_window_seconds = 60\nchat_poll_seconds = [5, 8]\n"
                                        "chat_watch_seconds = 90\nchat_settle_seconds = 3\n")
    assert (pc.chat_window_seconds, pc.chat_watch_seconds, pc.chat_settle_seconds) == (60, 90, 3)
    assert pc.chat_poll_seconds == (5.0, 8.0)


def test_chat_settings_default_when_left_out(tmp_path):
    pc = _pacing_without(tmp_path, CHAT)
    assert (pc.chat_window_seconds, pc.chat_watch_seconds, pc.chat_settle_seconds) == (120, 180, 5)
    assert pc.chat_poll_seconds == (10.0, 15.0)


def test_email_first_settings_load(tmp_path):
    from fakes import ROOT as R
    text = (R / "tests" / "data" / "config.toml").read_text()
    text = text.replace('[messages]\n', '[messages]\nask_email_first = true\n'
                        'sent_dm = ["just sent it to {email}, check your spam :)"]\n', 1)
    text = text.replace('keyword = "Tokyo"\n', 'keyword = "Tokyo"\ndm_offer = "the Tokyo slides"\n', 1)
    p = tmp_path / "c.toml"
    p.write_text(text)
    cfg = load_config(p)
    assert cfg.messages.ask_email_first is True
    assert cfg.messages.sent_dm == ["just sent it to {email}, check your spam :)"]
    assert cfg.reels[0].dm_offer == "the Tokyo slides"


def test_email_first_is_off_and_sent_dm_empty_by_default():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    assert cfg.messages.ask_email_first is False and cfg.messages.sent_dm == []
    assert cfg.reels[0].dm_offer == ""


@pytest.mark.parametrize("line", ['sent_dm = ["sent to {name}"]', 'ask_email_first = "yes"'])
def test_bad_email_first_settings_are_refused(tmp_path, line):
    text = (ROOT / "tests" / "data" / "config.toml").read_text().replace("[messages]\n", f"[messages]\n{line}\n", 1)
    p = tmp_path / "c.toml"
    p.write_text(text)
    with pytest.raises(ValueError):
        load_config(p)


def test_after_got_it_seconds_is_off_by_default():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    assert cfg.email.after_got_it_seconds is None


def test_after_got_it_seconds_loads(tmp_path):
    cfg = load_config(_cfg_text(tmp_path, "delay_minutes = 2",
                                "delay_minutes = 2\nafter_got_it_seconds = [20, 30]"))
    assert cfg.email.after_got_it_seconds == (20.0, 30.0)


@pytest.mark.parametrize("bad", ["[30, 20]", "[-1, 5]", "[20]", '"20"'])
def test_after_got_it_seconds_rejects_bad_values(tmp_path, bad):
    with pytest.raises(ValueError):
        load_config(_cfg_text(tmp_path, "delay_minutes = 2",
                              f"delay_minutes = 2\nafter_got_it_seconds = {bad}"))


# ---- [owner]
def test_owner_parsed():
    cfg = load_config(ROOT / "tests" / "data" / "config.toml")
    assert cfg.owner == Owner("Sam", "", DEFAULT_STYLE)


def test_owner_name_required(tmp_path):
    p = _cfg_text(tmp_path, 'name = "Sam"', 'name = "  "')
    with pytest.raises(ValueError, match="owner.name can't be empty"):
        load_config(p)


def test_owner_table_required(tmp_path):
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    start = text.index("[owner]")
    p = tmp_path / "c.toml"
    p.write_text(text[:start] + text[text.index("[jev]"):])
    with pytest.raises(ValueError, match="owner"):
        load_config(p)


def test_owner_name_is_stripped(tmp_path):
    assert load_config(_cfg_text(tmp_path, 'name = "Sam"', 'name = "  Sam "')).owner.name == "Sam"


@pytest.mark.parametrize("style_line", ["", 'style = ""', 'style = "   "'])
def test_owner_style_defaults(tmp_path, style_line):
    p = _cfg_text(tmp_path, f'style = "{DEFAULT_STYLE}"', style_line)
    assert load_config(p).owner.style == DEFAULT_STYLE
