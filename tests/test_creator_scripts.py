"""The creator-side helper scripts (ssh_setup.sh, ask_key.sh, server_start.sh). Every outside
command (ssh-keygen, ssh, osascript, cmd.exe, launchctl, uname) is a fake on PATH, HOME is
tmp_path, and the "server" is a local folder. Nothing real is reached and no window opens."""
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_KEYGEN = r'''#!/bin/sh
echo "ssh-keygen $*" >> "$CALLS"
while [ $# -gt 0 ]; do case "$1" in -f) out="$2"; shift 2 ;; *) shift ;; esac; done
printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATEBYTES\n-----END OPENSSH PRIVATE KEY-----\n' > "$out"
chmod 600 "$out"
echo "ssh-ed25519 AAAAPUBLIC igdm" > "$out.pub"
echo "Your identification has been saved in $out"
echo "The key's randomart image is:"
'''

# Logs each argument on its own line, between markers, so tests can read the exact argv.
ARGV_LOGGER = r'''#!/bin/sh
{ echo "__CALL__ $(basename "$0")"; for a in "$@"; do printf '%s\n__ARG__\n' "$a"; done; } >> "$CALLS"
'''

FAKE_SSH = r'''#!/bin/sh
echo "ssh $*" >> "$CALLS"
while [ $# -gt 0 ]; do
    case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift  # the server name
cd "$SERVER_HOME" || exit 255
HOME="$SERVER_HOME" PATH="$SERVER_PATH" exec /bin/sh -c "$*"
'''

# On the server: the settings check fails when CHECK_FAIL is set. It records whether LIVE_HERE
# was already there when the check ran.
FAKE_UV = r'''#!/bin/sh
if [ -f data/LIVE_HERE ]; then live=yes; else live=no; fi
echo "uv $* (live=$live)" >> "$CALLS"
case "$*" in *"igdm check"*) [ -z "$CHECK_FAIL" ] || { echo "Instagram login: refused"; exit 1; } ;; esac
exit 0
'''

FAKE_SQLITE = r'''#!/bin/sh
echo "sqlite3 $*" >> "$CALLS"
echo "$TICK_END"
'''

FAKE_SUDO = r'''#!/bin/sh
echo "sudo $*" >> "$CALLS"
case "$*" in *"${SUDO_FAIL:-no such command}"*) echo "Job for igdm.service failed." >&2; exit 1 ;; esac
'''

FAKE_TAILSCALE = '#!/bin/sh\necho \'{"Self": {"DNSName": "igdm.tail1234.ts.net."}}\'\n'


def logger(name, out="", code=0):
    return f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n{out}\nexit {code}\n'


def make_bin(path, fakes):
    path.mkdir(parents=True, exist_ok=True)
    for name, body in fakes.items():
        (path / name).write_text(body)
        (path / name).chmod(0o755)
    return path


def sq(path):  # how ask_key.sh single-quotes a folder
    return "'" + str(path).replace("'", "'\\''") + "'"


def uname(out):
    return f"#!/bin/sh\necho {out}\n"


def calls_of(env):
    p = Path(env["CALLS"])
    return p.read_text() if p.exists() else ""


def argv_calls(env, name):
    """The argv lists of each call to an ARGV_LOGGER fake."""
    found = []
    for chunk in calls_of(env).split("__CALL__ ")[1:]:
        head, _, rest = chunk.partition("\n")
        if head == name:
            found.append([a[:-1] if a.endswith("\n") else a for a in rest.split("__ARG__\n")[:-1]])
    return found


@pytest.fixture
def proj(tmp_path):
    """A copy of the project in a folder whose path has a space and an apostrophe."""
    p = tmp_path / "Alex's bot folder" / "insta comment dm"
    (p / "scripts").mkdir(parents=True)
    for name in ("_server.sh", "ssh_setup.sh", "ask_key.sh", "server_start.sh"):
        src = ROOT / "scripts" / name
        if src.exists():
            shutil.copy(src, p / "scripts" / name)
    shutil.copy(ROOT / ".env.example", p / ".env.example")
    return p


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    local_bin = make_bin(tmp_path / "bin", {"ssh-keygen": FAKE_KEYGEN, "ssh": FAKE_SSH,
                                            "osascript": ARGV_LOGGER, "cmd.exe": ARGV_LOGGER,
                                            "uname": uname("Darwin"),
                                            "launchctl": logger("launchctl", code=113)})
    return {"HOME": str(home), "PATH": f"{local_bin}:/usr/bin:/bin",
            "CALLS": str(tmp_path / "calls.log"), "BIN": str(local_bin)}


def run(proj, env, script, *args, **extra):
    out = subprocess.run(["/bin/bash", str(proj / "scripts" / script), *args], cwd=proj,
                         env={k: v for k, v in {**env, **extra}.items() if k != "BIN"},
                         capture_output=True, text=True)
    out.text = out.stdout + out.stderr
    return out


def set_fake(env, name, body):
    make_bin(Path(env["BIN"]), {name: body})


# --- ssh_setup.sh key --------------------------------------------------------------------------

def test_key_made_once_and_private_never_printed(proj, env):
    key = Path(env["HOME"]) / ".ssh/igdm_ed25519"
    first = run(proj, env, "ssh_setup.sh", "key")
    assert first.returncode == 0, first.text
    assert first.stdout.strip().splitlines()[-1] == f"{key}.pub"
    assert "PRIVATE" not in first.text
    assert key.exists() and Path(f"{key}.pub").exists()
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert f'ssh-keygen -t ed25519 -N  -C igdm -f {key}' in calls_of(env)

    second = run(proj, env, "ssh_setup.sh", "key")
    assert second.returncode == 0, second.text
    assert second.stdout.strip().splitlines()[-1] == f"{key}.pub"
    assert "PRIVATE" not in second.text
    assert calls_of(env).count("ssh-keygen -t") == 1
    assert "PRIVATEBYTES" in key.read_text()  # the existing key was kept


def test_ssh_setup_without_a_command_says_how(proj, env):
    out = run(proj, env, "ssh_setup.sh")
    assert out.returncode == 1
    assert "ssh_setup.sh key" in out.text and "ssh_setup.sh host" in out.text


# --- ssh_setup.sh host -------------------------------------------------------------------------

def block(address, user="igdm"):
    return ("# igdm start\nHost igdm\n    HostName " + address + "\n    User " + user +
            "\n    IdentityFile ~/.ssh/igdm_ed25519\n    IdentitiesOnly yes\n# igdm end\n")


def test_host_block_replaced_not_duplicated(proj, env):
    ssh_dir = Path(env["HOME"]) / ".ssh"
    ssh_dir.mkdir()
    config = ssh_dir / "config"
    config.write_text("Host github.com\n    User git\n")
    config.chmod(0o644)

    first = run(proj, env, "ssh_setup.sh", "host", "203.0.113.7")
    assert first.returncode == 0, first.text
    assert config.read_text() == "Host github.com\n    User git\n\n" + block("203.0.113.7")

    config.write_text(config.read_text() + "\nHost work\n    HostName work.example.com\n")
    second = run(proj, env, "ssh_setup.sh", "host", "igdm.tail1234.ts.net")
    assert second.returncode == 0, second.text
    text = config.read_text()
    assert text == ("Host github.com\n    User git\n\n" + block("igdm.tail1234.ts.net") +
                    "\nHost work\n    HostName work.example.com\n")  # same place, others kept
    assert text.count("# igdm start") == 1 and "203.0.113.7" not in text
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_host_makes_ssh_folder_and_takes_a_user(proj, env):
    out = run(proj, env, "ssh_setup.sh", "host", "203.0.113.7", "root")
    assert out.returncode == 0, out.text
    ssh_dir = Path(env["HOME"]) / ".ssh"
    assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((ssh_dir / "config").stat().st_mode) == 0o600
    assert (ssh_dir / "config").read_text() == block("203.0.113.7", "root")


@pytest.mark.parametrize("args", [(), ("bad address",), ("203.0.113.7\nHost *",), ("1.2.3.4", "a b")])
def test_host_refuses_a_bad_address_or_user(proj, env, args):
    out = run(proj, env, "ssh_setup.sh", "host", *args)
    assert out.returncode == 1
    assert not (Path(env["HOME"]) / ".ssh/config").exists()


@pytest.mark.parametrize("text", [
    "Host a\n# igdm start\nHost igdm\n    HostName old\nHost b\n    User x\n",  # no end
    "Host a\n# igdm end\n",  # an end without a start
    "# igdm start\n# igdm end\n# igdm start\n# igdm end\n",  # two blocks
])
def test_host_refuses_unpaired_markers(proj, env, text):
    config = Path(env["HOME"]) / ".ssh/config"
    config.parent.mkdir()
    config.write_text(text)
    out = run(proj, env, "ssh_setup.sh", "host", "203.0.113.7")
    assert out.returncode == 1
    assert "don't pair up" in out.text
    assert config.read_text() == text


@pytest.mark.parametrize("line", ["Host igdm", "host  IGDM", "Host other igdm"])
def test_host_refuses_an_unmarked_igdm_entry(proj, env, line):
    config = Path(env["HOME"]) / ".ssh/config"
    config.parent.mkdir()
    text = f"{line}\n    HostName mine.example.com\n"
    config.write_text(text)
    out = run(proj, env, "ssh_setup.sh", "host", "203.0.113.7")
    assert out.returncode == 1
    assert "didn't write" in out.text
    assert config.read_text() == text


def test_host_writes_through_a_symlinked_config(proj, env, tmp_path):
    real = tmp_path / "dotfiles/ssh_config"
    real.parent.mkdir()
    real.write_text("Host github.com\n    User git\n")
    real.chmod(0o644)
    ssh_dir = Path(env["HOME"]) / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").symlink_to(real)
    out = run(proj, env, "ssh_setup.sh", "host", "203.0.113.7")
    assert out.returncode == 0, out.text
    assert (ssh_dir / "config").is_symlink()
    assert real.read_text() == "Host github.com\n    User git\n\n" + block("203.0.113.7")
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert sorted(p.name for p in ssh_dir.iterdir()) == ["config"]  # no temp file left


# --- ask_key.sh --------------------------------------------------------------------------------

BANNER = "printf '\\n  THIS IS THE PRIVATE KEY WINDOW.\\n  Paste here, not in the Claude chat.\\n\\n'; "


def applescript_command(argv):
    """The shell command the Terminal window runs, without the banner in front of it."""
    [script] = [a for a in argv if a.startswith("set w to do script ")]
    m = re.fullmatch(r'set w to do script "((?:[^"\\]|\\.)*)"', script)
    assert m, script
    command = re.sub(r"\\(.)", r"\1", m.group(1))
    assert command.startswith(BANNER), command
    return command[len(BANNER):]


def test_ask_key_mac_opens_terminal(proj, env, tmp_path):
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert out.returncode == 0, out.text
    [argv] = argv_calls(env, "osascript")
    assert argv[0::2] == ["-e"] * (len(argv) // 2)
    assert argv[1::2] == ['tell application "Terminal"', argv[3], "try",
                          'set custom title of w to "PRIVATE KEY WINDOW"',
                          "set title displays custom title of w to true", "end try",
                          "activate", "end tell"]
    command = applescript_command(argv)
    assert "igdm set-key OPENAI_API_KEY" in command
    assert command == f"cd {sq(proj)} && uv run igdm set-key OPENAI_API_KEY"
    # The command really lands in the folder (with its space and apostrophe) when a shell runs it.
    shell_bin = make_bin(tmp_path / "shell-bin", {"uv": '#!/bin/sh\necho "$(pwd)|$*"\n'})
    ran = subprocess.run(["/bin/bash", "-c", command], env={"PATH": f"{shell_bin}:/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert ran.stdout == f"{proj}|run igdm set-key OPENAI_API_KEY\n", ran.stderr
    assert not argv_calls(env, "cmd.exe")


def test_ask_key_mac_window_has_a_title_and_banner(proj, env, tmp_path):
    """Mac 2: the key window must not look like the Terminal window Claude Code runs in."""
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert out.returncode == 0, out.text
    assert "A Terminal window called PRIVATE KEY WINDOW is open." in out.stdout
    [argv] = argv_calls(env, "osascript")
    [script] = [a for a in argv if a.startswith("set w to do script ")]
    full = re.sub(r"\\(.)", r"\1", script[len('set w to do script "'):-1])
    shell_bin = make_bin(tmp_path / "shell-bin", {"uv": '#!/bin/sh\necho "prompt"\n'})
    ran = subprocess.run(["/bin/bash", "-c", full], env={"PATH": f"{shell_bin}:/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert ran.stdout == ("\n  THIS IS THE PRIVATE KEY WINDOW.\n  Paste here, not in the Claude chat."
                          "\n\nprompt\n"), ran.stderr


def test_ask_key_mac_server(proj, env):
    out = run(proj, env, "ask_key.sh", "GMAIL_APP_PASSWORD", "--server")
    assert out.returncode == 0, out.text
    [argv] = argv_calls(env, "osascript")
    assert applescript_command(argv) == \
        "ssh -t igdm 'cd app && ~/.local/bin/uv run igdm set-key GMAIL_APP_PASSWORD'"


def test_ask_key_folder_with_bang_and_apostrophe(tmp_path, env):
    proj = tmp_path / "Alex's bots!" / "it's live!!"
    (proj / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/ask_key.sh", proj / "scripts/ask_key.sh")
    shutil.copy(ROOT / ".env.example", proj / ".env.example")
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert out.returncode == 0, out.text
    [argv] = argv_calls(env, "osascript")
    command = applescript_command(argv)
    assert '"' not in command  # single quotes only, so an interactive shell won't expand the "!"
    assert command == "cd 'TMP/Alex'\\''s bots!/it'\\''s live!!' && uv run igdm set-key OPENAI_API_KEY".replace(
        "TMP", str(tmp_path))
    shell_bin = make_bin(tmp_path / "shell-bin", {"uv": '#!/bin/sh\necho "$(pwd)"\n'})
    for shell in ("/bin/bash", "/bin/zsh"):
        if not Path(shell).exists():
            continue
        ran = subprocess.run([shell, "-c", command], env={"PATH": f"{shell_bin}:/usr/bin:/bin"},
                             capture_output=True, text=True)
        assert ran.stdout == f"{proj}\n", (shell, ran.stderr)


def test_ask_key_quotes_odd_folder_names(tmp_path, env):
    proj = tmp_path / 'we"ird $HOME `x` \\ folder'
    (proj / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/ask_key.sh", proj / "scripts/ask_key.sh")
    shutil.copy(ROOT / ".env.example", proj / ".env.example")
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert out.returncode == 0, out.text
    [argv] = argv_calls(env, "osascript")
    command = applescript_command(argv)
    shell_bin = make_bin(tmp_path / "shell-bin", {"uv": '#!/bin/sh\necho "$(pwd)"\n'})
    ran = subprocess.run(["/bin/bash", "-c", command], env={"PATH": f"{shell_bin}:/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert ran.stdout == f"{proj}\n", ran.stderr


def test_ask_key_windows_uses_server(proj, env):
    set_fake(env, "uname", uname("MINGW64_NT-10.0-19045"))
    local = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert local.returncode == 1
    assert "--server" in local.text
    assert not argv_calls(env, "cmd.exe")

    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY", "--server")
    assert out.returncode == 0, out.text
    [argv] = argv_calls(env, "cmd.exe")
    assert argv == ["//c", "start", "", "powershell", "-NoExit", "-Command",
                    "ssh -t igdm 'cd app && ~/.local/bin/uv run igdm set-key OPENAI_API_KEY'"]
    assert not argv_calls(env, "osascript")


def test_ask_key_msys_counts_as_windows(proj, env):
    set_fake(env, "uname", uname("MSYS_NT-10.0"))
    assert run(proj, env, "ask_key.sh", "OPENAI_API_KEY").returncode == 1
    assert run(proj, env, "ask_key.sh", "OPENAI_API_KEY", "--server").returncode == 0
    assert len(argv_calls(env, "cmd.exe")) == 1


def test_ask_key_print_and_unknown(proj, env):
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY", "--print")
    assert out.returncode == 0, out.text
    assert out.stdout == f"cd {sq(proj)} && uv run igdm set-key OPENAI_API_KEY\n"
    served = run(proj, env, "ask_key.sh", "--print", "--server", "OPENAI_API_KEY")
    assert served.stdout == "ssh -t igdm 'cd app && ~/.local/bin/uv run igdm set-key OPENAI_API_KEY'\n"
    assert calls_of(env) == ""  # nothing opened

    for bad in ("NOT_A_KEY", "OPENAI_API_KEY; rm -rf ~", "OPENAI", "openai_api_key", "", "OPENAI_API_KEY="):
        out = run(proj, env, "ask_key.sh", bad)
        assert out.returncode == 1, bad
    assert run(proj, env, "ask_key.sh").returncode == 1
    assert calls_of(env) == ""


def test_ask_key_other_systems_refuse(proj, env):
    set_fake(env, "uname", uname("Linux"))
    out = run(proj, env, "ask_key.sh", "OPENAI_API_KEY")
    assert out.returncode == 1
    assert calls_of(env) == ""


# --- server_start.sh ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, env):
    home = tmp_path / "server"
    (home / "app/data").mkdir(parents=True)
    (home / "app/config.toml").write_text('mode = "live"\n')
    (home / "app/.env").write_text("OPENAI_API_KEY=x\n")
    server_bin = make_bin(home / "bin", {"sudo": FAKE_SUDO, "systemctl": logger("systemctl"),
                                         "tailscale": FAKE_TAILSCALE, "sqlite3": FAKE_SQLITE})
    (server_bin / "python3").symlink_to(sys.executable)
    make_bin(home / ".local/bin", {"uv": FAKE_UV})
    set_fake(env, "uname", uname("MINGW64_NT-10.0-19045"))
    env.update({"SERVER_HOME": str(home), "SERVER_PATH": f"{server_bin}:/usr/bin:/bin",
                "TICK_END": "2099-01-01T00:00:00+00:00", "IGDM_START_WAIT": "0"})
    return home / "app"


def test_server_start_refuses_when_live(proj, env, server):
    (server / "data/LIVE_HERE").touch()
    out = run(proj, env, "server_start.sh")
    assert out.returncode == 1
    assert "The bot is already running on the server." in out.text
    assert "systemctl" not in calls_of(env) and "igdm check" not in calls_of(env)


def test_server_start_refuses_mac_bot(proj, env, server):
    set_fake(env, "uname", uname("Darwin"))
    set_fake(env, "launchctl", logger("launchctl", code=0))
    out = run(proj, env, "server_start.sh")
    assert out.returncode == 1
    assert "Your Mac is running the bot. Use the move step instead (scripts/cutover.sh)." in out.text
    assert re.search(r"launchctl print gui/\d+/local.igdm", calls_of(env))
    assert not (server / "data/LIVE_HERE").exists()
    assert "systemctl" not in calls_of(env)


def test_server_start_refuses_without_settings(proj, env, server):
    (server / "config.toml").unlink()
    out = run(proj, env, "server_start.sh")
    assert out.returncode == 1
    assert "config.toml" in out.text
    assert "igdm check" not in calls_of(env) and not (server / "data/LIVE_HERE").exists()


def test_server_start_stops_when_the_server_does_not_answer(proj, env, server):
    out = run(proj, env, "server_start.sh", SERVER_HOME=str(server.parent / "missing"))
    assert out.returncode == 1
    assert "reach the server" in out.text
    assert "systemctl" not in calls_of(env)


def test_server_start_check_fails_no_live_here(proj, env, server):
    out = run(proj, env, "server_start.sh", CHECK_FAIL="1")
    assert out.returncode == 1
    assert "igdm check" in calls_of(env)
    assert not (server / "data/LIVE_HERE").exists()
    assert "systemctl" not in calls_of(env)
    assert "running on your server" not in out.text


def test_server_start_success(proj, env, server):
    out = run(proj, env, "server_start.sh")
    assert out.returncode == 0, out.text
    log = calls_of(env)
    check = log.index("uv run --frozen igdm check (live=no)")
    enable = log.index("sudo systemctl enable --now igdm igdm-dashboard")
    tick = log.index("\nsqlite3 data/igdm.sqlite")
    assert check < enable < tick
    assert (server / "data/LIVE_HERE").exists()
    assert "DASHBOARD_HOSTS=igdm.tail1234.ts.net" in (server / ".env").read_text()
    assert "launchctl" not in log  # not a Mac
    assert out.stdout.strip().endswith(
        "The bot is running on your server. Dashboard: https://igdm.tail1234.ts.net")


def test_server_start_mac_without_the_agent_goes_ahead(proj, env, server):
    set_fake(env, "uname", uname("Darwin"))
    out = run(proj, env, "server_start.sh")
    assert out.returncode == 0, out.text
    assert "launchctl print" in calls_of(env)


def test_server_start_waits_for_a_fresh_tick(proj, env, server):
    out = run(proj, env, "server_start.sh", TICK_END="2000-01-01T00:00:00+00:00", IGDM_START_POLLS="3")
    assert out.returncode == 1
    assert calls_of(env).count("\nsqlite3 data/igdm.sqlite") == 3
    assert "hasn't finished a check" in out.text
    assert "running on your server." not in out.text


def test_server_start_switches_off_when_enable_fails(proj, env, server):
    out = run(proj, env, "server_start.sh", SUDO_FAIL="enable")
    assert out.returncode == 1
    log = calls_of(env)
    assert "sudo systemctl disable --now igdm igdm-dashboard" in log
    assert not (server / "data/LIVE_HERE").exists()
    assert "The bot didn't start, so I switched it off again. Nothing is running." in out.text
    assert "ssh igdm 'journalctl -u igdm -n 50'" in out.text
    assert "running on your server." not in out.text
    again = run(proj, env, "server_start.sh")  # a rerun isn't told it's already running
    assert again.returncode == 0, again.text


def test_server_start_switches_off_when_no_tick_comes(proj, env, server):
    out = run(proj, env, "server_start.sh", TICK_END="", IGDM_START_POLLS="2")
    assert out.returncode == 1
    log = calls_of(env)
    assert log.index("sudo systemctl enable --now") < log.index("sudo systemctl disable --now igdm igdm-dashboard")
    assert not (server / "data/LIVE_HERE").exists()
    assert "hasn't finished a check" in out.text
    assert "The bot didn't start, so I switched it off again. Nothing is running." in out.text


# --- line endings ------------------------------------------------------------------------------

def test_gitattributes_keeps_scripts_unix():
    rules = [line.split() for line in (ROOT / ".gitattributes").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    assert ["*.sh", "text", "eol=lf"] in rules
    assert ["*.py", "text", "eol=lf"] in rules


# --- sync_public -------------------------------------------------------------------------------

def test_creator_scripts_are_shared():
    sync_public = pytest.importorskip("private.sync_public")  # only in the private repo
    for path in ("scripts/ssh_setup.sh", "scripts/ask_key.sh", "scripts/server_start.sh", ".gitattributes"):
        assert sync_public._is_shared(path), path
