"""Editing [[reels]] blocks in config.toml without disturbing anything else."""

import difflib
import tomllib

import pytest
from fakes import ROOT

from igdm.reelfile import add_reel, set_paused, update_reel, write_config

REPO = (ROOT / "tests" / "data" / "config.toml").read_text()
NEW = {"key": "rome", "url": "https://www.instagram.com/reel/ABC123/", "keyword": "Rome",
       "offer": "the Rome slides", "material_url": "https://drive.google.com/x",
       "handle_comments_after": "2026-11-04T10:00:00", "hand_dm_phrases": [], "ai_notes": "",
       "email_subject": "the {keyword} slides", "email_body": "Hey {name},\n\nhere 👇\n\n{material_url}\n"}


def _removed(a: str, b: str) -> list[str]:
    return [l for l in difflib.ndiff(a.splitlines(), b.splitlines()) if l.startswith("- ")]


def _tokyo_span(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l == 'key = "tokyo"') - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    return start, end


def test_round_trip_untouched():
    assert update_reel(REPO, "tokyo", {}) == REPO


def test_add_reel_only_adds_lines():
    out = add_reel(REPO, NEW)
    assert _removed(REPO, out) == []
    reels = tomllib.loads(out)["reels"]
    assert len(reels) == 2
    assert reels[1] == {k: v for k, v in NEW.items() if v not in ("", [])}
    assert list(reels[1]) == ["key", "url", "keyword", "offer", "material_url",
                              "handle_comments_after", "email_subject", "email_body"]
    assert "\n\n[[reels]]\nkey = \"rome\"" in out


def test_add_reel_round_trips_tricky_text():
    body = 'Hey {name},\n\nshe said "hi" \\o/ and """ three quotes\n\ttab and 👇 {material_url}\n'
    out = add_reel(REPO, {**NEW, "email_body": body, "offer": 'a "quoted" \\ offer'})
    r = tomllib.loads(out)["reels"][1]
    assert r["email_body"] == body and r["offer"] == 'a "quoted" \\ offer'


def test_update_changes_only_that_block():
    out = update_reel(REPO, "tokyo", {"offer": "new offer"})
    assert tomllib.loads(out)["reels"][0]["offer"] == "new offer"
    a, b = _tokyo_span(REPO)
    before, after = REPO.splitlines(), out.splitlines()
    assert before[:a] == after[:a]
    assert len(before) == len(after) and before[b:] == after[b:]


def test_update_removes_empty_optional():
    out = update_reel(REPO, "tokyo", {"ai_notes": "", "hand_dm_phrases": []})
    r = tomllib.loads(out)["reels"][0]
    assert "ai_notes" not in r and "hand_dm_phrases" not in r


def test_update_unknown_key_raises():
    with pytest.raises(KeyError):
        update_reel(REPO, "nope", {"offer": "x"})


def test_set_paused_and_unpause():
    out = set_paused(REPO, "tokyo", True)
    assert tomllib.loads(out)["reels"][0]["paused"] is True
    assert set_paused(out, "tokyo", False) == REPO
    assert set_paused(REPO, "tokyo", False) == REPO


def test_write_config_replaces_atomically(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(REPO)
    p.chmod(0o640)
    cfg = write_config(p, add_reel(REPO, NEW))
    assert [r.key for r in cfg.reels] == ["tokyo", "rome"]
    assert tomllib.loads(p.read_text())["reels"][1]["key"] == "rome"
    assert p.stat().st_mode & 0o777 == 0o640
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml"]


def test_write_config_refuses_bad_text(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(REPO)
    bad = add_reel(REPO, {**NEW, "key": "tokyo"})  # duplicate key
    with pytest.raises(ValueError):
        write_config(p, bad)
    assert p.read_text() == REPO
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml"]


TWO = REPO.rstrip("\n") + '''

# Rome reel, posted 4 Nov
[[reels]]
key = "rome"
url = "https://www.instagram.com/reel/ABC123/"
keyword = "Rome"
offer = "o"
material_url = "https://x.example/m"
handle_comments_after = "2026-11-04T10:00:00"
'''


def test_set_paused_lands_inside_its_own_block():
    lines = set_paused(TWO, "tokyo", True).splitlines()
    i = lines.index("paused = true")
    assert lines[i - 1] == "email_body = \"Hi {name}, here is the guide you asked for:\\n{material_url}\"" and lines[i + 2] == "# Rome reel, posted 4 Nov"
    assert set_paused(set_paused(TWO, "tokyo", True), "tokyo", False) == TWO


def test_update_new_key_lands_inside_its_own_block():
    out = update_reel(TWO, "tokyo", {"ai_notes": "", "hand_dm_phrases": []})
    lines = update_reel(out, "tokyo", {"ai_notes": "fact"}).splitlines()
    i = lines.index('ai_notes = "fact"')
    assert lines[i + 2] == "# Rome reel, posted 4 Nov"
