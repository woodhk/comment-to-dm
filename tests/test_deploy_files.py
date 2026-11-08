import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SETUP = DEPLOY / "setup_server.sh"


def test_no_timezone_hardcoded():
    for path in DEPLOY.iterdir():
        if path.is_file():
            text = path.read_text()
            assert "Hong_Kong" not in text, path.name
            assert "Asia/" not in text, path.name


def test_backup_timer_uses_server_time():
    text = (DEPLOY / "igdm-backup.timer").read_text()
    assert "OnCalendar=*-*-* 03:30:00\n" in text
    assert "igdm nightly backup at 03:30 server time" in text


@pytest.fixture
def fake_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def fake(name, body):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    fake("id", "echo 0")
    fake("timedatectl", 'if [ "$1" = list-timezones ]; then echo Europe/London; fi')
    fake("apt-get", f"touch {tmp_path}/apt-ran")
    return {"PATH": f"{bin_dir}:/usr/bin:/bin"}, tmp_path


def run_setup(env, *args):
    return subprocess.run(["/bin/bash", str(SETUP), *args], env=env,
                          capture_output=True, text=True)


def test_setup_needs_a_timezone(fake_env):
    env, tmp_path = fake_env
    out = run_setup(env)
    assert out.returncode == 1
    assert "Give the timezone, like: bash deploy/setup_server.sh Europe/London" in out.stdout + out.stderr
    assert not (tmp_path / "apt-ran").exists()


def test_setup_rejects_unknown_timezone(fake_env):
    env, tmp_path = fake_env
    out = run_setup(env, "Mars/Base")
    assert out.returncode == 1
    assert "Unknown timezone: Mars/Base" in out.stdout + out.stderr
    assert not (tmp_path / "apt-ran").exists()


def test_setup_accepts_a_valid_timezone(fake_env):
    env, tmp_path = fake_env
    bin_dir = tmp_path / "bin"
    log = tmp_path / "timedatectl.log"
    for name, body in {
        "timedatectl": f'echo "$@" >> {log}\nif [ "$1" = list-timezones ]; then echo Europe/London; echo Europe/Paris; fi',
        "apt-get": f"touch {tmp_path}/apt-ran; exit 1",  # stops the script right after the check
    }.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n")
    out = run_setup(env, "Europe/London")
    assert "Unknown timezone" not in out.stdout + out.stderr
    assert "set-timezone Europe/London" in log.read_text()
    assert (tmp_path / "apt-ran").exists()


REPO = "https://github.com/woodhk/comment-to-dm.git"


@pytest.fixture
def setup_fakes(fake_env):
    """Fakes for the steps up to the app folder. The run stops soon after it, at the first
    write into /etc, which doesn't exist on this Mac."""
    env, tmp_path = fake_env
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    for name, body in {
        "install": f'echo "install $*" >> {log}',
        "adduser": f'echo "adduser $*" >> {log}',
        "git": f'echo "git $*" >> {log}',
        "sudo": f'echo "sudo $*" >> {log}\nshift 3\nexec "$@"',  # sudo -u igdm -H <cmd>
    }.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_dir / name).chmod(0o755)
    return env, tmp_path, log


def test_setup_installs_git():
    assert "apt-get -y -q install sqlite3 rsync ufw unattended-upgrades curl python3 git\n" in SETUP.read_text()


def test_setup_clones_when_given_a_repo(setup_fakes):
    env, tmp_path, log = setup_fakes
    run_setup(env, "Europe/London", REPO)
    calls = log.read_text()
    assert f"sudo -u igdm -H git clone -q {REPO} /home/igdm/app\n" in calls
    clone = calls.index("git clone")
    assert calls.index("install -d -o igdm -g igdm /home/igdm/app /home/igdm/app/data /home/igdm/app/logs") > clone


def test_setup_leaves_an_existing_clone_alone(setup_fakes):
    env, tmp_path, log = setup_fakes
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    (app / "README.md").write_text("mine\n")
    run_setup({**env, "IGDM_APP_ROOT": str(app)}, "Europe/London", REPO)
    calls = log.read_text()
    assert "git clone" not in calls
    assert f"install -d -o igdm -g igdm {app} {app}/data {app}/logs" in calls


def test_setup_refuses_app_folder_without_git(setup_fakes):
    env, tmp_path, log = setup_fakes
    app = tmp_path / "app"
    app.mkdir()
    (app / "README.md").write_text("copied by hand\n")
    out = run_setup({**env, "IGDM_APP_ROOT": str(app)}, "Europe/London", REPO)
    assert out.returncode == 1
    assert "The app folder already has files that didn't come from git. Not changing it." in out.stdout
    assert "git clone" not in log.read_text()
    assert (app / "README.md").read_text() == "copied by hand\n"


def test_setup_without_a_repo_never_clones(setup_fakes):
    env, tmp_path, log = setup_fakes
    run_setup(env, "Europe/London")
    calls = log.read_text()
    assert "git " not in calls and "sudo" not in calls
    assert "install -d -o igdm -g igdm /home/igdm/app /home/igdm/app/data /home/igdm/app/logs" in calls


def test_setup_installs_packages_only_for_a_clone():
    text = SETUP.read_text()
    uv_install = text.index("astral.sh/uv/install.sh")
    sync = text.index("uv sync --frozen -q")
    services = text.index("== services")
    assert uv_install < sync < services
    assert 'if [ -n "$REPO" ]' in text[text.rindex("== uv", 0, sync):sync]


def test_setup_clones_into_empty_data_and_logs_folders(setup_fakes):
    env, tmp_path, log = setup_fakes  # left by an earlier run without the url
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    (app / "logs").mkdir()
    out = run_setup({**env, "IGDM_APP_ROOT": str(app)}, "Europe/London", REPO)
    assert "didn't come from git" not in out.stdout
    assert f"sudo -u igdm -H git clone -q {REPO} {app}\n" in log.read_text()


def test_setup_refuses_data_with_files_in_it(setup_fakes):
    env, tmp_path, log = setup_fakes
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    (app / "data/igdm.sqlite").write_text("leads\n")
    out = run_setup({**env, "IGDM_APP_ROOT": str(app)}, "Europe/London", REPO)
    assert out.returncode == 1
    assert "The app folder already has files that didn't come from git. Not changing it." in out.stdout
    assert "git clone" not in log.read_text()
    assert (app / "data/igdm.sqlite").read_text() == "leads\n"


def test_setup_closing_lines_fit_mac_and_windows():
    """Windows 14: a Windows creator has no move, and starts the bot with server_start.sh."""
    text = SETUP.read_text()
    assert 'echo "Done. Dashboard address: https://$NAME"' in text
    assert "Only scripts/cutover.sh (Mac) or scripts/server_start.sh (Windows) starts them." in text
    assert "after the move" not in text
    assert "NOT started: cutover.sh or server_start.sh starts them" in text
