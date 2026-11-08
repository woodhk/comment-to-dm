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
