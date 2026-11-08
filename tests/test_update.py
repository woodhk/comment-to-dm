"""scripts/update.sh, run against a throwaway clone with fake uv, launchctl, sqlite3, sudo and
systemctl. Nothing real is stopped, started or installed."""
import os
import signal
import time
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UPDATE = ROOT / "scripts/update.sh"
SCRIPTS = ("update.sh", "install_agent.sh", "uninstall_agent.sh")
CONFIG = b'# my settings\ndb_path = "data/igdm.sqlite"\n'
DB = b"SQLite format 3\x00 my leads"

FAKE_UV = r'''#!/bin/sh
echo "uv $*" >> "$CALLS"
case "$*" in
    *"igdm upgrade"*) echo "# upgraded" >> config.toml
                      [ ! -f data/igdm.sqlite ] || echo "dashboard write" >> data/igdm.sqlite  # like the server's dashboard
                      # a dashboard that restarted on the new code and ran a data step
                      [ -z "${UPGRADE_DB_VERSION:-}" ] || echo "$UPGRADE_DB_VERSION" > data/igdm.sqlite.version ;;
esac
case "$*" in *pytest*) [ -n "${UV_SLOW:-}" ] && sleep 1 ;; esac
if [ -n "${UV_FAIL:-}" ]; then
    case "$*" in *"$UV_FAIL"*) echo "Traceback: boom"; exit 1 ;; esac
fi
exit 0
'''

FAKE_SQLITE = r'''#!/bin/sh
echo "sqlite3 $*" >> "$CALLS"
db="$1"
path=$(printf '%s' "$2" | sed -e 's/^\.[a-z]* //' -e "s/^'//" -e "s/'\$//")
# the data version lives in "<db>.version" (1 when there's no such file)
case "$2" in
    .backup*) cp "$db" "$path"; [ ! -f "$db.version" ] || cp "$db.version" "$path.version" ;;
    .restore*) cp "$path" "$db"; rm -f "$db.version"; [ ! -f "$path.version" ] || cp "$path.version" "$db.version" ;;
    PRAGMA*user_version*) [ -z "${PRAGMA_FAIL:-}" ] || exit 1; cat "$db.version" 2>/dev/null || echo 1 ;;
esac
'''

# The first "start igdm" is the new version starting: it records a DM, and with NEW_DB_VERSION
# it also runs a data upgrade. Later starts (the old version again) record nothing.
FAKE_NEW_BOT = r'''case "$*" in
    "systemctl start igdm") if [ ! -f "$CALLS.started" ]; then
        touch "$CALLS.started"; echo "new bot DM" >> data/igdm.sqlite
        [ -z "${NEW_DB_VERSION:-}" ] || echo "$NEW_DB_VERSION" > data/igdm.sqlite.version
    fi ;;
esac
'''


def logger(name):
    return f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n'


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    fakes = {"uv": FAKE_UV, "sqlite3": FAKE_SQLITE, "launchctl": logger("launchctl"),
             "sudo": logger("sudo") + FAKE_NEW_BOT + 'case "$*" in *"${SUDO_FAIL:-none}"*) exit 1 ;; esac\n', "systemctl": logger("systemctl")}
    for name, body in fakes.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    git_dir = os.path.dirname(shutil.which("git"))
    return {
        "HOME": str(tmp_path), "PATH": f"{bin_dir}:{git_dir}:/usr/bin:/bin", "CALLS": str(calls),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
    }


@pytest.fixture
def repos(tmp_path, env, monkeypatch):
    """A bare origin, a 'seed' clone Alex publishes from, and the creator's clone."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    origin, seed, clone = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "scripts").mkdir()
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, seed / "scripts" / name)
    shutil.copytree(ROOT / "launchd", seed / "launchd")
    (seed / "README.md").write_text("hello\n")
    (seed / ".gitignore").write_text("config.toml\ndata/\nlogs/\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "First version")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    (clone / "config.toml").write_bytes(CONFIG)
    (clone / "data").mkdir()
    (clone / "data/igdm.sqlite").write_bytes(DB)
    return origin, seed, clone


def publish(seed, subject="Add a friendly greeting"):
    (seed / "greeting.txt").write_text("hi\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", subject)
    git(seed, "push", "-q", "origin", "main")


def run(clone, env, **extra):
    out = subprocess.run(["/bin/bash", str(clone / "scripts/update.sh")], cwd=clone,
                         env={**env, **extra}, capture_output=True, text=True)
    out.text = out.stdout + out.stderr
    return out


def calls(env):
    path = Path(env["CALLS"])
    return path.read_text().splitlines() if path.exists() else []


def index_of(lines, needle):
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not in {lines}")


def test_script_is_executable_and_parses_in_bash_3():
    assert UPDATE.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["/bin/bash", "-n", str(UPDATE)]).returncode == 0
    assert "—" not in UPDATE.read_text()


def test_not_a_git_checkout_refuses(tmp_path, env):
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, proj / "scripts" / name)
    (proj / "config.toml").write_bytes(CONFIG)
    out = run(proj, env)
    assert out.returncode == 1, out.text
    assert "Updates need the project downloaded with git. Ask Claude to set that up." in out.text
    assert calls(env) == []


def test_local_changes_refuse(repos, env):
    _, seed, clone = repos
    publish(seed)
    (clone / "README.md").write_text("my edit\n")
    out = run(clone, env)
    assert out.returncode == 1, out.text
    assert "You've changed these files, so I won't update over them:" in out.text
    assert "README.md" in out.text
    assert not any("launchctl" in c for c in calls(env))


def test_already_up_to_date(repos, env):
    _, _, clone = repos
    out = run(clone, env)
    assert out.returncode == 0, out.text
    assert "Already up to date." in out.text
    assert calls(env) == []


def test_offline_refuses_without_stopping_anything(repos, env):
    origin, seed, clone = repos
    publish(seed)
    shutil.rmtree(origin)
    out = run(clone, env)
    assert out.returncode == 1, out.text
    assert "Traceback" not in out.text and "fatal" not in out.text
    assert calls(env) == []


def test_local_commits_refuse_without_stopping_anything(repos, env):
    _, seed, clone = repos
    publish(seed)
    (clone / "mine.txt").write_text("x\n")
    git(clone, "add", "mine.txt")
    git(clone, "commit", "-qm", "my own change")
    head = git(clone, "rev-parse", "HEAD")
    out = run(clone, env)
    assert out.returncode == 1, out.text
    assert calls(env) == []
    assert git(clone, "rev-parse", "HEAD") == head


def test_missing_config_refuses_without_stopping_anything(repos, env):
    _, seed, clone = repos
    publish(seed)
    (clone / "config.toml").unlink()
    out = run(clone, env)
    assert out.returncode == 1, out.text
    assert "config.toml" in out.text
    assert calls(env) == []


def test_update_success_mac(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env)
    assert out.returncode == 0, out.text
    assert git(clone, "rev-parse", "HEAD") == git(clone, "rev-parse", "origin/main")
    lines = calls(env)
    order = [index_of(lines, n) for n in ("launchctl bootout", ".backup", "uv run --frozen igdm upgrade",
                                          "uv run --frozen pytest -q", "uv run --frozen igdm check",
                                          "launchctl bootstrap")]
    assert order == sorted(order), lines
    assert "Add a friendly greeting" in out.text
    assert "Updated to " in out.text
    backups = list((clone / "data/backups").glob("update-*"))
    assert len(backups) == 1
    assert (backups[0] / "config.toml").read_bytes() == CONFIG
    assert (backups[0] / "igdm.sqlite").read_bytes() == DB
    assert (backups[0] / "update.log").exists()


def test_failed_update_puts_everything_back(repos, env):
    _, seed, clone = repos
    publish(seed)
    old = git(clone, "rev-parse", "HEAD")
    out = run(clone, env, UV_FAIL="pytest")
    assert out.returncode == 2, out.text
    assert git(clone, "rev-parse", "HEAD") == old
    assert (clone / "config.toml").read_bytes() == CONFIG
    # the new version never ran, so the live database is left alone, write after the backup included
    assert (clone / "data/igdm.sqlite").read_bytes() == DB + b"dashboard write\n"
    lines = calls(env)
    assert not any(".restore" in c for c in lines), lines
    failed = index_of(lines, "uv run --frozen pytest -q")
    assert index_of(lines, "PRAGMA user_version") > failed  # the versions were compared
    assert any("launchctl bootstrap" in c for c in lines[failed:]), lines
    assert any(c.startswith("uv sync") for c in lines[failed:]), lines
    assert "put your bot back" in out.text
    assert "update.log" in out.text
    assert "Traceback" not in out.text  # the tool output went to the log, not the screen
    log = next((clone / "data/backups").glob("update-*/update.log"))
    assert "Traceback: boom" in log.read_text()


def test_failed_tests_restore_the_database_when_its_version_changed(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env, UV_FAIL="pytest", UPGRADE_DB_VERSION="2")
    assert out.returncode == 2, out.text
    # the bot was never started, but the data version moved, so the old code gets its copy back
    assert (clone / "data/igdm.sqlite").read_bytes() == DB
    assert not (clone / "data/igdm.sqlite.version").exists()
    lines = calls(env)
    failed = index_of(lines, "uv run --frozen pytest -q")
    assert index_of(lines, "PRAGMA user_version") > failed
    kept = next((clone / "data/backups").glob("update-*/igdm-new-version.sqlite"))
    assert kept.read_bytes().endswith(b"dashboard write\n")
    assert "igdm-new-version.sqlite" in out.text


def test_failed_tests_keep_the_database_when_its_version_cant_be_read(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env, UV_FAIL="pytest", PRAGMA_FAIL="1")
    assert out.returncode == 2, out.text
    # the new version was never started, so an unreadable version is no reason to roll data back
    assert (clone / "data/igdm.sqlite").read_bytes() == DB + b"dashboard write\n"
    assert not any(".restore" in c for c in calls(env))
    log = next((clone / "data/backups").glob("update-*/update.log")).read_text()
    assert "couldn't read the database versions" in log
    assert "live database is kept" in log
    assert "Nothing was lost." in out.text


def test_failed_merge_also_puts_everything_back(repos, env):
    _, seed, clone = repos
    publish(seed)
    old = git(clone, "rev-parse", "HEAD")
    # an untracked file in the way makes the fast-forward fail
    (clone / "greeting.txt").write_text("mine\n")
    out = run(clone, env)
    assert out.returncode == 2, out.text
    assert git(clone, "rev-parse", "HEAD") == old
    assert (clone / "greeting.txt").read_text() == "mine\n"
    assert any("launchctl bootstrap" in c for c in calls(env))


def test_server_uses_systemctl(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env, IGDM_FORCE_SERVER="1")
    assert out.returncode == 0, out.text
    lines = calls(env)
    stop = index_of(lines, "sudo systemctl stop igdm")
    start = index_of(lines, "sudo systemctl start igdm")
    restart = index_of(lines, "sudo systemctl try-restart igdm igdm-dashboard")
    assert stop < index_of(lines, "uv run --frozen igdm check") < start < restart
    assert not any("launchctl" in c for c in lines)


def test_server_failure_starts_the_bot_again(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env, IGDM_FORCE_SERVER="1", UV_FAIL="igdm check")
    assert out.returncode == 2, out.text
    lines = calls(env)
    failed = index_of(lines, "uv run --frozen igdm check")
    assert any("sudo systemctl start igdm" in c for c in lines[failed:]), lines


def test_restore_that_fails_still_starts_the_bot(repos, env):
    _, seed, clone = repos
    publish(seed)
    out = run(clone, env, UV_FAIL="sync --frozen")
    assert out.returncode == 2, out.text
    assert "couldn't put everything back" in out.text
    assert "safe in" in out.text
    assert (clone / "config.toml").read_bytes() == CONFIG
    assert any("launchctl bootstrap" in c for c in calls(env))


def test_server_that_wont_stop_refuses_and_changes_nothing(repos, env):
    _, seed, clone = repos
    publish(seed)
    old = git(clone, "rev-parse", "HEAD")
    out = run(clone, env, IGDM_FORCE_SERVER="1", SUDO_FAIL="stop igdm")
    assert out.returncode == 1, out.text
    assert git(clone, "rev-parse", "HEAD") == old
    lines = calls(env)
    assert not any(c.startswith("uv ") for c in lines), lines
    assert any("sudo systemctl start igdm" in c for c in lines), lines


def test_interrupted_update_puts_everything_back(repos, env):
    _, seed, clone = repos
    publish(seed)
    old = git(clone, "rev-parse", "HEAD")
    proc = subprocess.Popen(["/bin/bash", str(clone / "scripts/update.sh")], cwd=clone,
                            env={**env, "UV_SLOW": "1"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 20
    while not any("pytest" in c for c in calls(env)):
        assert time.time() < deadline, "pytest never started"
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)  # like a closed window or a Ctrl-C
    text, _ = proc.communicate(timeout=30)
    assert proc.returncode == 2, text
    assert "interrupted" in text and "put your bot back" in text
    assert git(clone, "rev-parse", "HEAD") == old
    assert (clone / "config.toml").read_bytes() == CONFIG
    assert (clone / "data/igdm.sqlite").read_bytes() == DB + b"dashboard write\n"
    lines = calls(env)
    assert any("launchctl bootstrap" in c for c in lines[index_of(lines, "pytest"):]), lines


def test_mac_that_moved_to_the_server_refuses(repos, env):
    _, seed, clone = repos
    publish(seed)
    (clone / "data/MOVED_TO_SERVER").write_text("1 Nov\n")
    out = run(clone, env)
    assert out.returncode == 1, out.text
    assert "Update the server instead" in out.text
    assert calls(env) == []


def failure_after_start(repos, env, **extra):
    _, seed, clone = repos
    publish(seed)
    old = git(clone, "rev-parse", "HEAD")
    out = run(clone, env, IGDM_FORCE_SERVER="1", SUDO_FAIL="try-restart", **extra)
    assert out.returncode == 2, out.text
    assert git(clone, "rev-parse", "HEAD") == old
    assert (clone / "config.toml").read_bytes() == CONFIG
    assert "put your bot back" in out.text
    lines = calls(env)
    after = lines[index_of(lines, "sudo systemctl try-restart") + 1:]
    stop, start = index_of(after, "sudo systemctl stop igdm"), index_of(after, "sudo systemctl start igdm")
    assert stop < index_of(after, "PRAGMA user_version") < start, lines
    return clone, out, after, (stop, start)


def test_failure_after_start_keeps_the_live_database_when_its_version_is_the_same(repos, env):
    clone, out, after, _ = failure_after_start(repos, env)
    # what the new bot recorded (a DM) survives, so the old bot won't send it again
    assert (clone / "data/igdm.sqlite").read_bytes() == DB + b"dashboard write\nnew bot DM\n"
    assert not any(".restore" in c for c in after), after
    assert "Nothing was lost." in out.text


def test_failure_after_start_restores_the_database_when_the_new_version_upgraded_it(repos, env):
    clone, out, after, (stop, start) = failure_after_start(repos, env, NEW_DB_VERSION="2")
    assert (clone / "data/igdm.sqlite").read_bytes() == DB  # byte for byte
    assert not (clone / "data/igdm.sqlite.version").exists()  # back to version 1
    assert stop < index_of(after, ".restore") < start, after
    kept = next((clone / "data/backups").glob("update-*/igdm-new-version.sqlite"))
    assert kept.read_bytes().endswith(b"new bot DM\n")
    assert "igdm-new-version.sqlite" in out.text


def test_failure_after_start_restores_the_database_when_the_version_cant_be_read(repos, env):
    clone, _, after, _ = failure_after_start(repos, env, PRAGMA_FAIL="1")
    assert (clone / "data/igdm.sqlite").read_bytes() == DB
    log = next((clone / "data/backups").glob("update-*/update.log")).read_text()
    assert "couldn't read the database versions" in log
    assert "the new version was started, so the backup goes back" in log


def test_no_database_is_not_called_saved(repos, env):
    _, seed, clone = repos
    publish(seed)
    (clone / "data/igdm.sqlite").unlink()
    out = run(clone, env)
    assert out.returncode == 0, out.text
    assert "settings and database" not in out.text
    assert "Backing up your settings..." in out.text and "Your old settings are saved in" in out.text
    assert not any("sqlite3" in c for c in calls(env))
