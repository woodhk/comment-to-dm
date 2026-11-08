import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts/install_agent.sh"
TARGETS = {"bot": "local.igdm", "dashboard": "local.igdm-dashboard", "backup-pull": "local.igdm-backup-pull"}


@pytest.fixture
def fake_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}, bin_dir


def render(env, target):
    return subprocess.run(["/bin/bash", str(INSTALL), "--print", target], env=env,
                          capture_output=True, text=True)


def test_bot_plist_renders_for_this_folder(fake_env):
    env, bin_dir = fake_env
    out = render(env, "bot")
    assert out.returncode == 0, out.stderr
    assert "<string>local.igdm</string>" in out.stdout
    assert str(ROOT) in out.stdout
    assert str(bin_dir / "uv") in out.stdout
    assert "<string>run</string>" in out.stdout
    assert "__" not in out.stdout


def test_print_installs_nothing(fake_env):
    env, _ = fake_env
    render(env, "bot")
    assert not (Path(env["HOME"]) / "Library").exists()


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
@pytest.mark.parametrize("target", sorted(TARGETS))
def test_plists_are_valid(fake_env, target):
    env, _ = fake_env
    out = render(env, target)
    assert out.returncode == 0, out.stderr
    assert f"<string>{TARGETS[target]}</string>" in out.stdout
    assert "__" not in out.stdout
    lint = subprocess.run(["plutil", "-lint", "-"], input=out.stdout, capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_templates_name_no_one():
    templates = sorted((ROOT / "launchd").glob("*.plist.template"))
    assert len(templates) == 3
    for t in templates:
        text = t.read_text()
        assert "/Users/" not in text and "alexwood" not in text, t.name


def test_missing_uv_says_how_to_install(tmp_path):
    out = subprocess.run(["/bin/bash", str(INSTALL), "--print", "bot"],
                         env={"HOME": str(tmp_path), "PATH": ""}, capture_output=True, text=True)
    assert out.returncode == 1
    assert "https://docs.astral.sh/uv/" in out.stdout + out.stderr


def test_uv_found_in_local_bin_when_not_on_path(tmp_path):
    uv = tmp_path / ".local/bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    out = render({"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, "dashboard")
    assert out.returncode == 0, out.stderr
    assert str(uv) in out.stdout


def test_folder_with_awkward_characters_renders_valid_plist(tmp_path, fake_env):
    env, _ = fake_env
    odd = tmp_path / "a & b | c"
    (odd / "scripts").mkdir(parents=True)
    shutil.copy(INSTALL, odd / "scripts/install_agent.sh")
    shutil.copytree(ROOT / "launchd", odd / "launchd")
    out = subprocess.run(["/bin/bash", str(odd / "scripts/install_agent.sh"), "--print", "bot"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "a &amp; b | c" in out.stdout
    if shutil.which("plutil"):
        lint = subprocess.run(["plutil", "-lint", "-"], input=out.stdout, capture_output=True, text=True)
        assert lint.returncode == 0, lint.stdout


def test_unknown_print_target_fails(fake_env):
    env, _ = fake_env
    assert render(env, "nope").returncode != 0


def test_uninstall_also_removes_agents_with_an_older_label(tmp_path):
    # a fake launchctl first on PATH, so nothing real is touched
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "launchctl.log"
    fake = bin_dir / "launchctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    fake.chmod(0o755)
    agents = tmp_path / "home/Library/LaunchAgents"
    agents.mkdir(parents=True)
    for name in ("local.igdm", "old.label.igdm", "old.label.igdm-dashboard", "old.label.igdm-backup-pull", "other.app"):
        (agents / f"{name}.plist").write_text("x")
    out = subprocess.run(["/bin/bash", str(ROOT / "scripts/uninstall_agent.sh")],
                         env={"HOME": str(tmp_path / "home"), "PATH": f"{bin_dir}:/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert sorted(p.name for p in agents.iterdir()) == ["old.label.igdm-backup-pull.plist", "other.app.plist"]
    called = log.read_text()
    assert "local.igdm-dashboard" in called and "old.label.igdm\n" in called


def test_runs_from_inside_the_scripts_folder(fake_env):
    env, _ = fake_env
    out = subprocess.run(["bash", "install_agent.sh", "--print", "backup-pull"], env=env,
                         cwd=ROOT / "scripts", capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "<string>local.igdm-backup-pull</string>" in out.stdout


# --- deploy.sh and rollback.sh, run against a throwaway copy with fake ssh and rsync ---------

@pytest.fixture
def deploy_env(tmp_path):
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    for name in ("_server.sh", "deploy.sh"):
        shutil.copy(ROOT / "scripts" / name, proj / "scripts" / name)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run([*git, "commit", "-qm", "x"], cwd=proj, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "ssh").write_text(
        '#!/bin/sh\necho "ssh $*" >> "$CALLS"\n'
        'case "$*" in\n'
        '  *"igdm upgrade"*) printf "%s\\n" "$UPGRADE_OUT" ;;\n'
        '  *is-active*) echo active; echo active ;;\n'
        'esac\n')
    (bin_dir / "rsync").write_text('#!/bin/sh\necho "rsync $*" >> "$CALLS"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(log)}
    return proj, env, log


def run_deploy(proj, env, upgrade_out):
    return subprocess.run(["/bin/bash", str(proj / "scripts/deploy.sh")], cwd=proj,
                          env={**env, "UPGRADE_OUT": upgrade_out}, capture_output=True, text=True)


def test_deploy_restarts_when_settings_already_current(deploy_env):
    proj, env, log = deploy_env
    out = run_deploy(proj, env, "settings are already up to date (version 2)")
    assert out.returncode == 0, out.stdout + out.stderr
    calls = log.read_text()
    assert "igdm upgrade" in calls and "run --frozen igdm upgrade" in calls
    assert "try-restart igdm igdm-dashboard" in calls
    assert "deployed" in out.stdout


def test_deploy_stops_without_restart_when_settings_were_upgraded(deploy_env):
    proj, env, log = deploy_env
    out = run_deploy(proj, env, "settings upgraded from version 1 to 2 (old copy saved as x)")
    assert out.returncode != 0
    assert "STOPPED" in out.stdout and "pull_config.sh" in out.stdout and "[owner]" in out.stdout
    assert "try-restart" not in log.read_text()
    assert "DEPLOYED" not in log.read_text()
    assert "\ndeployed " not in out.stdout


def test_deploy_rsync_skips_config_backups_and_config(deploy_env):
    proj, env, log = deploy_env
    run_deploy(proj, env, "settings are already up to date (version 2)")
    rsync = next(ln for ln in log.read_text().splitlines() if ln.startswith("rsync"))
    assert "--exclude /config.toml " in rsync and "--exclude /config.toml.bak-*" in rsync


def test_rollback_upgrades_the_restored_config_before_installing_the_agent():
    text = (ROOT / "scripts/rollback.sh").read_text()
    copied = text.index('cp "$CFG" config.toml')
    upgrade = text.index("uv run igdm upgrade")
    install = text.index("bash scripts/install_agent.sh")
    assert copied < upgrade < install
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/rollback.sh")]).returncode == 0
