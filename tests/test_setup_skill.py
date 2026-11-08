"""Guards for the setup and update-my-bot skills: their key promises stay in the files."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
SETUP = SKILLS / "setup"
UPDATE = SKILLS / "update-my-bot"
CHAPTERS = SETUP / "chapters"
REFERENCE = SETUP / "reference"

CHAPTER_FILES = (
    "01-get-ready.md",
    "02-instagram.md",
    "03-facebook-page.md",
    "04-meta-app.md",
    "05-connect.md",
    "06-jev.md",
    "07-ai-key.md",
    "08-gmail.md",
    "09-make-it-yours.md",
    "10-check-and-dry-run.md",
    "11-live-test.md",
    "12-run-always.md",
    "13-cloud.md",
    "everyday.md",
)
REFERENCE_FILES = ("voice.md", "who-does-what.md", "chrome.md", "stuck.md", "progress.md",
                   "meta-notes.md")
SHAPE = ("## Why this matters", "## Steps", "## Check", "## If it doesn't work")
# Built in parts: the sync refuses if this text is left in any file it copies, this one included.
PLACEHOLDER = "{{" + "BMC_USERNAME}}"


def _skill_files() -> list[Path]:
    return sorted(p for p in SKILLS.rglob("*.md") if p.is_file())


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    block = text[4:text.index("\n---", 4)]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_skill_frontmatter():
    setup, update = _frontmatter(SETUP / "SKILL.md"), _frontmatter(UPDATE / "SKILL.md")
    for fields in (setup, update):
        assert fields.get("name")
        assert fields.get("description")
        assert len(fields["description"]) <= 1536
    assert setup["name"] == "setup" and update["name"] == "update-my-bot"
    assert "set me up" in setup["description"]
    assert "update my bot" in update["description"]


def test_every_chapter_exists_and_has_shape():
    skill = (SETUP / "SKILL.md").read_text(encoding="utf-8")
    for name in CHAPTER_FILES:
        path = CHAPTERS / name
        assert path.is_file(), name
        assert f"(chapters/{name})" in skill, name
        if name[:2].isdigit():
            lines = path.read_text(encoding="utf-8").splitlines()
            for heading in SHAPE:
                assert heading in lines, f"{name} has no {heading}"
    for name in REFERENCE_FILES:
        assert (REFERENCE / name).is_file(), name
        assert f"(reference/{name})" in skill, name


@pytest.mark.parametrize("name", ["05-connect.md", "07-ai-key.md", "08-gmail.md",
                                  "10-check-and-dry-run.md", "11-live-test.md"])
def test_windows_forms(name):
    text = (CHAPTERS / name).read_text(encoding="utf-8")
    assert "## On Windows" in text.splitlines()
    assert "ssh igdm" in text


def test_live_test_protects_followers():
    text = (CHAPTERS / "11-live-test.md").read_text(encoding="utf-8")
    assert "livetest.sqlite" in text
    assert "handle_comments_after" in text
    assert "dm_delay_seconds = [0, 0]" in text


def test_secrets_never_in_chat():
    for path in _skill_files():
        text = path.read_text(encoding="utf-8").lower()
        for phrase in ("cat .env", "paste it here", "paste it in the chat", "send me the"):
            assert phrase not in text, f"{path.relative_to(ROOT)} says {phrase!r}"
    assert "scripts/ask_key.sh" in (REFERENCE / "who-does-what.md").read_text(encoding="utf-8")


def _gitignored(gitignore: Path, path: str, repo: Path) -> bool:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_bytes(gitignore.read_bytes())
    done = subprocess.run(["git", "-C", str(repo), "check-ignore", "--no-index", "-q", path],
                          check=False)
    return done.returncode == 0


def _public_gitignore() -> Path:
    """The public copy's .gitignore: public/.gitignore here, the root one in the public copy itself."""
    overlay = ROOT / "public"
    return overlay / ".gitignore" if overlay.is_dir() else ROOT / ".gitignore"


def test_progress_file_resume(tmp_path):
    head = (SETUP / "SKILL.md").read_text(encoding="utf-8").splitlines()[:40]
    assert any(".setup-progress.json" in line for line in head)
    assert '"done"' in (REFERENCE / "progress.md").read_text(encoding="utf-8")
    assert _gitignored(ROOT / ".gitignore", ".setup-progress.json", tmp_path / "here")
    assert _gitignored(_public_gitignore(), ".setup-progress.json", tmp_path / "public")


def test_public_copy_ignores_the_setup_scratch_files(tmp_path):
    """Mac 8: the skill's test copies and an emails export never count as local changes."""
    for i, name in enumerate(("config.test.toml", "config.dryrun.toml", "emails.csv")):
        assert _gitignored(_public_gitignore(), name, tmp_path / str(i)), name


def test_first_real_dm_note_written_before_the_wait():
    """If Claude Code closes before the first real DM, the next session must still look for it."""
    waiting = '"12-run-always": "running, first real DM not seen yet"'
    text = (CHAPTERS / "12-run-always.md").read_text(encoding="utf-8")
    check = text[text.index("## Check"):text.index("### The first real DM")]
    windows = text[text.index("## On Windows"):]
    assert waiting in check and waiting in windows
    assert "running since" not in text
    assert "first real DM celebrated" in windows
    for path in (SETUP / "SKILL.md", REFERENCE / "progress.md"):
        assert "hasn't been seen yet" in " ".join(path.read_text(encoding="utf-8").split())


def test_update_skill_routes_to_server():
    text = (UPDATE / "SKILL.md").read_text(encoding="utf-8")
    assert "MOVED_TO_SERVER" in text
    assert "ssh igdm 'cd app && bash scripts/update.sh'" in text


def test_skill_voice():
    docs_link = re.compile(r"\]\((?:\.\./)*docs/|(?<![\w./-])docs/")
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        assert "—" not in text, f"{rel} has an em dash"
        assert "–" not in text, f"{rel} has an en dash"
        assert not docs_link.search(text), f"{rel} links to docs/"


def test_coffee_only_in_chapter_12():
    """The coffee placeholder lives only in chapter 12. In the public copy it's filled or dropped,
    so the exact count is checked in the private overlay tests."""
    found = {p.relative_to(SKILLS).as_posix() for p in _skill_files()
             if PLACEHOLDER in p.read_text(encoding="utf-8")}
    assert found <= {"setup/chapters/12-run-always.md"}
