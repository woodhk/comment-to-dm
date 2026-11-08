"""The Mac-side server scripts (_server.sh, deploy.sh, push_config.sh, cutover.sh), run against
a local "server" folder. A fake ssh runs the remote command in that folder and a fake scp copies
in and out of it. Nothing real is reached."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOOD = b'# my settings\nmode = "live"\n'
OLD = b'# server settings\nmode = "live"\n'

FAKE_SSH = r'''#!/bin/sh
echo "ssh $*" >> "$CALLS"
case "$*" in *"${SSH_FAIL:-no such command}"*) echo "ssh: connect to host igdm: timed out" >&2; exit 255 ;; esac
while [ $# -gt 0 ]; do
    case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift  # the server name
cd "$SERVER_HOME" || exit 255
HOME="$SERVER_HOME" PATH="$SERVER_PATH" exec /bin/sh -c "$*"
'''

FAKE_SCP = r'''#!/bin/sh
echo "scp $*" >> "$CALLS"
for a in "$@"; do  # server:path becomes a path in the server folder
    shift
    case "$a" in
        -q) ;;
        *:*) set -- "$@" "$SERVER_HOME/${a#*:}" ;;
        *) set -- "$@" "$a" ;;
    esac
done
exec /bin/cp "$@"
'''

# On the server: uv's settings check fails on a file holding BROKEN, like a real load_config.
FAKE_UV = r'''#!/bin/sh
echo "uv $*" >> "$CALLS"
case "$*" in
    *load_config*) if grep -q BROKEN config.toml.new; then echo "ValueError: mode must be one of ..."; exit 1; fi ;;
    *"igdm upgrade"*) echo "settings are already up to date (version 2)" ;;
esac
exit 0
'''

FAKE_TAILSCALE = r'''#!/bin/sh
echo '{"Self": {"DNSName": "igdm.tail1234.ts.net."}}'
'''


def logger(name, out=""):
    return f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n{out}\n'


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_bin(path, fakes):
    path.mkdir(parents=True)
    for name, body in fakes.items():
        (path / name).write_text(body)
        (path / name).chmod(0o755)
    return path


@pytest.fixture
def world(tmp_path):
    """proj: the Mac copy. server: the igdm user's home, with app/ in it."""
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    for name in ("_server.sh", "deploy.sh", "push_config.sh", "pull_config.sh", "cutover.sh"):
        shutil.copy(ROOT / "scripts" / name, proj / "scripts" / name)
    (proj / "scripts/uninstall_agent.sh").write_text('echo "uninstall_agent.sh" >> "$CALLS"\n')
    (proj / "scripts/install_agent.sh").write_text('echo "install_agent.sh $*" >> "$CALLS"\n')
    server = tmp_path / "server"
    (server / "app").mkdir(parents=True)
    calls = tmp_path / "calls.log"
    local_bin = make_bin(tmp_path / "bin", {"ssh": FAKE_SSH, "scp": FAKE_SCP,
                                            "rsync": logger("rsync"), "uv": logger("local-uv")})
    server_bin = make_bin(server / "bin", {
        "sudo": logger("sudo"), "systemctl": logger("systemctl", "echo active; echo active"),
        "tailscale": FAKE_TAILSCALE})
    make_bin(server / ".local/bin", {"uv": FAKE_UV})
    git_dir = os.path.dirname(shutil.which("git"))
    env = {"HOME": str(tmp_path), "PATH": f"{local_bin}:{git_dir}:/usr/bin:/bin", "CALLS": str(calls),
           "SERVER_HOME": str(server), "SERVER_PATH": f"{server_bin}:{git_dir}:/usr/bin:/bin",
           "GIT_CONFIG_NOSYSTEM": "1"}
    return proj, server, env, calls


def calls_of(calls):
    return calls.read_text() if calls.exists() else ""


def run(proj, env, script, **extra):
    out = subprocess.run(["/bin/bash", str(proj / "scripts" / script)], cwd=proj,
                         env={**env, **extra}, capture_output=True, text=True)
    out.text = out.stdout + out.stderr
    return out


def commit_all(proj):
    if not (proj / ".git").exists():
        git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-qm", "x")


# --- server_name -------------------------------------------------------------------------------

def test_server_name_parses_on_the_server(world, tmp_path):
    proj, server, env, calls = world
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for name in ("ssh",):
        os.symlink(tmp_path / "bin" / name, bare / name)
    os.symlink(shutil.which("dirname"), bare / "dirname")
    out = subprocess.run(["/bin/bash", "-c", f'. "{proj}/scripts/_server.sh" && server_name'],
                         env={**env, "PATH": str(bare)}, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout == "igdm.tail1234.ts.net\n"


def test_server_has_git(world):
    proj, server, env, calls = world
    check = ["/bin/bash", "-c", f'. "{proj}/scripts/_server.sh" && server_has_git']
    assert subprocess.run(check, env=env).returncode == 1
    (server / "app/.git").mkdir()
    assert subprocess.run(check, env=env).returncode == 0


# --- deploy.sh ---------------------------------------------------------------------------------

def test_deploy_refuses_git_server(world):
    proj, server, env, calls = world
    commit_all(proj)
    (server / "app/.git").mkdir()
    out = run(proj, env, "deploy.sh")
    assert out.returncode == 1
    assert ("This server gets its code from GitHub. To update it, run: "
            "ssh igdm 'cd app && bash scripts/update.sh'") in out.stdout
    assert "rsync" not in calls_of(calls)
    assert "try-restart" not in calls_of(calls)


def test_deploy_unchanged_for_rsync_server(world):
    proj, server, env, calls = world
    commit_all(proj)
    out = run(proj, env, "deploy.sh")
    assert out.returncode == 0, out.text
    log = calls_of(calls)
    assert "rsync -az --delete" in log
    assert "uv sync --frozen -q" in log and "uv run --frozen igdm upgrade" in log
    assert "sudo systemctl try-restart igdm igdm-dashboard" in log
    assert (server / "app/DEPLOYED").read_text().strip() == git(proj, "rev-parse", "--short", "HEAD")
    assert "deployed " in out.stdout


def test_deploy_stops_when_the_git_check_cannot_reach_the_server(world):
    proj, server, env, calls = world
    commit_all(proj)
    out = run(proj, env, "deploy.sh", SSH_FAIL="test -e .git")
    assert out.returncode == 1
    assert "Couldn't reach the server to check how it gets its code." in out.stdout
    assert "rsync" not in calls_of(calls)


# --- push_config.sh ----------------------------------------------------------------------------

def test_push_config_checks_on_server(world):
    proj, server, env, calls = world
    (proj / "config.toml").write_bytes(b'mode = "BROKEN"\n')
    (server / "app/config.toml").write_bytes(OLD)
    (proj / "data").mkdir()
    (proj / "data/server-config.last.toml").write_bytes(OLD)  # pulled last time
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 1
    assert "The new settings didn't pass the check, so the server keeps its old ones." in out.stdout
    assert "ValueError: mode must be one of" in out.stdout
    assert (server / "app/config.toml").read_bytes() == OLD
    assert not (server / "app/config.toml.new").exists()
    assert "local-uv" not in calls_of(calls)  # nothing is checked on this computer
    assert "try-restart" not in calls_of(calls)
    assert (proj / "data/server-config.last.toml").read_bytes() == OLD


def test_push_config_sends_a_good_config(world):
    proj, server, env, calls = world
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(OLD)
    (proj / "data").mkdir()
    (proj / "data/server-config.last.toml").write_bytes(OLD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 0, out.text
    assert (server / "app/config.toml").read_bytes() == GOOD
    assert not (server / "app/config.toml.new").exists()
    assert (proj / "data/server-config.last.toml").read_bytes() == GOOD
    log = calls_of(calls)
    assert "uv run --frozen python -c" in log and "local-uv" not in log
    assert "sudo systemctl try-restart igdm igdm-dashboard" in log


def test_push_config_first_push(world):
    proj, server, env, calls = world
    (proj / "config.toml").write_bytes(GOOD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 0, out.text
    assert (server / "app/config.toml").read_bytes() == GOOD
    assert (proj / "data/server-config.last.toml").read_bytes() == GOOD


def test_push_config_still_refuses_a_dashboard_edit(world):
    proj, server, env, calls = world
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(OLD + b"# a reel added on the dashboard\n")
    (proj / "data").mkdir()
    (proj / "data/server-config.last.toml").write_bytes(OLD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 1
    assert "Run scripts/pull_config.sh first" in out.stdout
    assert (server / "app/config.toml").read_bytes().endswith(b"dashboard\n")
    assert not (server / "app/config.toml.new").exists()


def test_push_config_without_history_refuses_an_unseen_server_copy(world):
    proj, server, env, calls = world
    (proj / "README.md").write_text("public copy, config.toml isn't tracked\n")
    commit_all(proj)
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(OLD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 1
    assert "The server's config changed since your last pull" in out.stdout
    assert "fatal" not in out.text
    assert (server / "app/config.toml").read_bytes() == OLD


def test_push_config_without_history_accepts_an_identical_server_copy(world):
    proj, server, env, calls = world
    (proj / "README.md").write_text("public copy\n")
    commit_all(proj)
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(GOOD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 0, out.text
    assert (proj / "data/server-config.last.toml").read_bytes() == GOOD


def test_push_config_says_so_when_the_server_cant_be_reached(world):
    proj, server, env, calls = world
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(OLD)
    out = run(proj, env, "push_config.sh", SSH_FAIL="test -f config.toml")
    assert out.returncode == 1
    assert "Couldn't reach the server. Check Tailscale is connected, then try again." in out.stdout
    assert (server / "app/config.toml").read_bytes() == OLD
    assert "scp" not in calls_of(calls)


def test_push_config_uses_the_committed_copy_when_never_pulled(world):
    proj, server, env, calls = world  # Alex: config.toml is tracked in his private repo
    (proj / "config.toml").write_bytes(OLD)
    commit_all(proj)
    (proj / "config.toml").write_bytes(GOOD)
    (server / "app/config.toml").write_bytes(OLD)
    out = run(proj, env, "push_config.sh")
    assert out.returncode == 0, out.text
    assert (server / "app/config.toml").read_bytes() == GOOD


# --- pull_config.sh ----------------------------------------------------------------------------

def test_pull_config_tracked_shows_the_git_diff(world):
    proj, server, env, calls = world  # Alex: config.toml is tracked in his private repo
    (proj / "config.toml").write_bytes(OLD)
    commit_all(proj)
    (server / "app/config.toml").write_bytes(GOOD)
    out = run(proj, env, "pull_config.sh")
    assert out.returncode == 0, out.text
    assert (proj / "config.toml").read_bytes() == GOOD
    assert (proj / "data/server-config.last.toml").read_bytes() == GOOD
    assert "config.toml | " in out.stdout
    assert "pulled the server's config.toml (commit it if it changed)" in out.stdout


def test_pull_config_untracked_skips_git(world):
    """Windows 18: in a creator's copy git ignores config.toml, so no git lines."""
    proj, server, env, calls = world
    (proj / ".gitignore").write_text("config.toml\ndata/\n")
    (proj / "README.md").write_text("public copy\n")
    commit_all(proj)
    (proj / "config.toml").write_bytes(OLD)
    (server / "app/config.toml").write_bytes(GOOD)
    out = run(proj, env, "pull_config.sh")
    assert out.returncode == 0, out.text
    assert (proj / "config.toml").read_bytes() == GOOD
    assert out.stdout == "Pulled config.toml from the server.\n"
    assert "commit" not in out.text


def test_pull_config_without_git_at_all(world):
    proj, server, env, calls = world  # a ZIP download: no .git
    (server / "app/config.toml").write_bytes(GOOD)
    out = run(proj, env, "pull_config.sh")
    assert out.returncode == 0, out.text
    assert out.stdout == "Pulled config.toml from the server.\n"
    assert "fatal" not in out.text


# --- cutover.sh step 1 -------------------------------------------------------------------------

@pytest.fixture
def git_server(world, tmp_path):
    """The server app is a clone of a bare 'GitHub'; the Mac is on main and clean."""
    proj, server, env, calls = world
    (proj / "deploy").mkdir()
    (proj / "deploy/igdm.service").write_text("[Unit]\n")
    (proj / ".gitignore").write_text("config.toml\n.env\ndata/\n")
    commit_all(proj)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(proj, "remote", "add", "origin", str(origin))
    git(proj, "push", "-q", "origin", "main")
    shutil.rmtree(server / "app")
    subprocess.run(["git", "clone", "-q", str(origin), str(server / "app")], check=True)
    (server / "app/data").mkdir()
    return proj, server, env, calls, origin


def test_cutover_refuses_mismatched_commit(git_server):
    proj, server, env, calls, origin = git_server
    (proj / "README.md").write_text("a newer version on GitHub\n")
    git(proj, "add", "-A")
    git(proj, "commit", "-qm", "newer")
    git(proj, "push", "-q", "origin", "main")
    git(proj, "reset", "-q", "--hard", "HEAD~1")  # the Mac is a version behind GitHub
    out = run(proj, env, "cutover.sh")
    assert out.returncode == 1
    assert 'Your Mac and GitHub have different versions. Say "update my bot" first, then move.' in out.stdout
    log = calls_of(calls)
    assert "uninstall_agent.sh" not in log
    assert "rsync" not in log
    assert "git merge" not in log
    assert not (server / "app/data/LIVE_HERE").exists()
    assert not (proj / "data/MOVED_TO_SERVER").exists()


def test_cutover_ignores_untracked_files_on_the_mac(git_server):
    """Mac 8: files git doesn't track (emails.csv, a leftover config.test.toml) don't block it."""
    proj, server, env, calls, origin = git_server
    (proj / "emails.csv").write_text("a@b.com\n")
    (proj / "config.test.toml").write_text('mode = "live"\n')
    (server / "bin/tailscale").unlink()  # stops the run straight after step 1, before stage 2
    out = run(proj, env, "cutover.sh")
    assert "Commit or discard local changes first." not in out.stdout
    assert "git fetch -q origin" in calls_of(calls)  # it went on past the local check
    assert "uninstall_agent.sh" not in calls_of(calls)


def test_cutover_still_refuses_changed_tracked_files_on_the_mac(git_server):
    proj, server, env, calls, origin = git_server
    (proj / "deploy/igdm.service").write_text("[Unit]\n# edited by hand\n")
    (proj / "emails.csv").write_text("a@b.com\n")
    out = run(proj, env, "cutover.sh")
    assert out.returncode == 1
    assert "Commit or discard local changes first." in out.stdout
    assert "deploy/igdm.service" in out.stdout
    assert "emails.csv" not in out.stdout
    assert "git fetch" not in calls_of(calls)
    assert_mac_untouched(proj, server, calls)


def test_cutover_updates_a_git_server_from_github(git_server):
    proj, server, env, calls, origin = git_server
    (proj / "README.md").write_text("a newer version\n")
    git(proj, "add", "-A")
    git(proj, "commit", "-qm", "newer")
    git(proj, "push", "-q", "origin", "main")  # GitHub and the Mac agree; the server is behind
    (server / "bin/tailscale").unlink()  # stops the run straight after step 1, before stage 2
    out = run(proj, env, "cutover.sh")
    assert out.returncode != 0
    log = calls_of(calls)
    assert "git fetch -q origin" in log
    assert "git merge -q --ff-only origin/main" in log
    assert "\nuv sync --frozen -q\n" in log
    assert "\nuv run --frozen igdm upgrade" not in log  # no config.toml on the server yet
    assert "FileNotFoundError" not in out.text  # (the Traceback here is from the missing tailscale)
    assert git(server / "app", "rev-parse", "HEAD") == git(proj, "rev-parse", "HEAD")
    assert "rsync" not in log and "uninstall_agent.sh" not in log
    assert "Stopped before touching the Mac" in out.stdout
    assert not (server / "app/data/LIVE_HERE").exists()


def test_cutover_upgrades_the_server_config_when_there_is_one(git_server):
    proj, server, env, calls, origin = git_server
    (server / "app/config.toml").write_bytes(OLD)
    (server / "bin/tailscale").unlink()
    run(proj, env, "cutover.sh")
    assert "\nuv run --frozen igdm upgrade" in calls_of(calls)


def assert_mac_untouched(proj, server, calls):
    log = calls_of(calls)
    assert "uninstall_agent.sh" not in log and "rsync" not in log
    assert not (server / "app/data/LIVE_HERE").exists()
    assert not (proj / "data/MOVED_TO_SERVER").exists()


def test_cutover_refuses_a_server_ahead_of_github(git_server):
    proj, server, env, calls, origin = git_server
    (server / "app/extra.txt").write_text("made on the server\n")
    git(server / "app", "add", "extra.txt")
    git(server / "app", "commit", "-qm", "server only")
    out = run(proj, env, "cutover.sh")
    assert out.returncode == 1
    assert 'Your Mac and GitHub have different versions. Say "update my bot" first, then move.' in out.stdout
    assert_mac_untouched(proj, server, calls)


def test_cutover_refuses_changed_files_on_the_server(git_server):
    proj, server, env, calls, origin = git_server
    (server / "app/deploy/igdm.service").write_text("[Unit]\n# edited by hand\n")
    out = run(proj, env, "cutover.sh")
    assert out.returncode == 1
    assert "These files were changed on the server: deploy/igdm.service" in out.stdout
    assert "I won't move over local changes on the server." in out.stdout
    assert "git merge" not in calls_of(calls)
    assert (server / "app/deploy/igdm.service").read_text().endswith("by hand\n")
    assert_mac_untouched(proj, server, calls)


def test_cutover_stops_when_the_git_check_cannot_reach_the_server(git_server):
    proj, server, env, calls, origin = git_server
    out = run(proj, env, "cutover.sh", SSH_FAIL="test -e .git")
    assert out.returncode == 1
    assert "Couldn't reach the server to check how it gets its code." in out.stdout
    assert_mac_untouched(proj, server, calls)
