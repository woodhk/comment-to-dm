import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from igdm.cli import main
from igdm.config import CONFIG_VERSION, DEFAULT_STYLE, load_config
from igdm.reelfile import add_reel, set_paused, write_config
from igdm.upgrade import STEPS, upgrade_file, upgrade_text

ROOT = Path(__file__).resolve().parent.parent
OLD = ROOT / "tests" / "data" / "old_configs"
NOW = datetime(2026, 11, 8, 9, 30, 15)
NEW_REEL = {
    "key": "rome",
    "url": "https://www.instagram.com/reel/ROME1/",
    "keyword": "Rome",
    "offer": "the Rome slides",
    "material_url": "https://drive.google.com/x",
    "handle_comments_after": "2026-11-04T10:00:00",
}


def _v1(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text((OLD / "v1.toml").read_text())
    return p


def test_every_version_has_a_step():
    assert set(STEPS) == set(range(2, CONFIG_VERSION + 1))


def test_old_versions_upgrade_and_load(tmp_path):
    for old in sorted(OLD.glob("v*.toml")):
        p = tmp_path / "config.toml"
        p.write_text(old.read_text())
        frm, to, backup = upgrade_file(p, NOW)
        assert to == CONFIG_VERSION and backup.read_text() == old.read_text()
        load_config(p)  # loads under today's code


def test_load_refuses_old_settings(tmp_path):
    with pytest.raises(ValueError, match="Run: uv run igdm upgrade"):
        load_config(_v1(tmp_path))


def test_load_refuses_newer_settings(tmp_path):
    p = _v1(tmp_path)
    p.write_text(f"config_version = {CONFIG_VERSION + 1}\n" + p.read_text())
    with pytest.raises(ValueError, match="Update the bot's code first"):
        load_config(p)


@pytest.mark.parametrize("bad", ['"2"', "2.0", "true"])
def test_config_version_must_be_a_whole_number(tmp_path, bad):
    p = _v1(tmp_path)
    p.write_text(f"config_version = {bad}\n" + p.read_text())
    with pytest.raises(ValueError, match="config_version must be a whole number"):
        load_config(p)
    with pytest.raises(ValueError, match="config_version must be a whole number"):
        upgrade_text(p.read_text())


def test_upgrade_keeps_comments_and_reels(tmp_path):
    old = (OLD / "v1.toml").read_text()
    new, frm, to = upgrade_text(old)
    assert (frm, to) == (1, CONFIG_VERSION)
    for line in old.splitlines():
        if line.lstrip().startswith("#"):
            assert line in new.splitlines()
    raw = tomllib.loads(new)
    assert raw["config_version"] == 2  # top level, not inside a table
    assert raw["owner"] == {"name": "Sam", "about": "", "style": DEFAULT_STYLE}
    p = tmp_path / "config.toml"
    p.write_text(new)
    assert tomllib.loads(old)["reels"] == raw["reels"]
    assert load_config(p).reels  # and they load


def test_upgrade_twice_changes_nothing(tmp_path):
    p = _v1(tmp_path)
    upgrade_file(p, NOW)
    text = p.read_text()
    assert upgrade_file(p, NOW) == (2, 2, None) and p.read_text() == text
    assert not list(tmp_path.glob("config.toml.bak-*"))[1:]  # only the first backup


def test_backup_name_uses_the_time(tmp_path):
    p = _v1(tmp_path)
    _, _, backup = upgrade_file(p, NOW)
    assert backup.name == "config.toml.bak-20261108-093015"


def test_owner_name_falls_back_to_me(tmp_path):
    old = (OLD / "v1.toml").read_text().replace('from_name = "Sam Lee"', 'from_name = ""')
    new, _, _ = upgrade_text(old)
    assert tomllib.loads(new)["owner"]["name"] == "me"


def test_upgraded_settings_survive_reels_tab(tmp_path):
    p = _v1(tmp_path)
    upgrade_file(p, NOW)
    key = load_config(p).reels[0].key
    write_config(p, set_paused(add_reel(p.read_text(), NEW_REEL), key, True))
    raw = tomllib.loads(p.read_text())
    assert raw["config_version"] == 2 and raw["owner"]["name"] == "Sam"


def test_cli_upgrade_on_old_settings(tmp_path, capsys):
    p = _v1(tmp_path)
    assert main(["--config", str(p), "upgrade"]) == 0
    out = capsys.readouterr().out
    assert "settings upgraded from version 1 to 2 (old copy saved as config.toml.bak-" in out
    assert main(["--config", str(p), "upgrade"]) == 0
    assert "settings are already up to date (version 2)" in capsys.readouterr().out
