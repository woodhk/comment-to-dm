#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for igdm. Safe to run again.
# From the Mac:   scp -r deploy root@<server-ip>:  &&  ssh -t root@<server-ip> bash deploy/setup_server.sh <timezone>
# A creator's server adds the repo url:  bash deploy/setup_server.sh <timezone> <repo url>
# Then the app folder is a git clone of it (scripts/update.sh keeps it up to date). Without the
# url the app folder stays empty, for scripts/deploy.sh to fill.
# The timezone is yours, for example Europe/London. It sets the server clock, so the quiet hours
# and the nightly backup (03:30) follow it.
#
# It never starts or enables the bot or the dashboard: only scripts/cutover.sh does that.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
TZNAME="${1:-}"
REPO="${2:-}"
[ -n "$TZNAME" ] || { echo "Give the timezone, like: bash deploy/setup_server.sh Europe/London"; exit 1; }
# Read the list first: under pipefail, grep -q closing the pipe early can make a valid zone look wrong.
ZONES="$(timedatectl list-timezones)"
printf '%s\n' "$ZONES" | grep -x -- "$TZNAME" >/dev/null || { echo "Unknown timezone: $TZNAME"; exit 1; }
timedatectl set-timezone "$TZNAME"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${IGDM_APP_ROOT:-/home/igdm/app}"  # IGDM_APP_ROOT: for the tests only
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install sqlite3 rsync ufw unattended-upgrades curl python3 git

echo "== user igdm (same SSH key as root)"
id igdm >/dev/null 2>&1 || adduser --disabled-password --gecos "" igdm
install -d -m 700 -o igdm -g igdm /home/igdm/.ssh
install -m 600 -o igdm -g igdm /root/.ssh/authorized_keys /home/igdm/.ssh/authorized_keys
app_is_empty() {  # nothing in the app folder but (maybe) empty data/ and logs/ from an earlier run
    local entry
    for entry in "$APP"/* "$APP"/.[!.]* "$APP"/..?*; do
        [ -e "$entry" ] || continue
        case "${entry##*/}" in
            data|logs) if [ -d "$entry" ] && [ -z "$(ls -A "$entry")" ]; then continue; fi ;;
        esac
        return 1
    done
    return 0
}
if [ -n "$REPO" ]; then
    if [ -e "$APP/.git" ]; then
        echo "The app folder is already a git clone. Leaving it as it is."
    elif app_is_empty; then
        echo "== the bot's code from $REPO"
        rmdir "$APP/data" "$APP/logs" 2>/dev/null || true  # git clones only into an empty folder
        install -d -o igdm -g igdm "$APP"
        sudo -u igdm -H git clone -q "$REPO" "$APP"
    else
        echo "The app folder already has files that didn't come from git. Not changing it."
        exit 1
    fi
fi
install -d -o igdm -g igdm "$APP" "$APP/data" "$APP/logs"

echo "== automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "== SSH: keys only"
# sshd keeps the FIRST value it reads, so this file must sort before 50-cloud-init.conf
rm -f /etc/ssh/sshd_config.d/99-igdm.conf
cat > /etc/ssh/sshd_config.d/01-igdm.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
mkdir -p /run/sshd  # 24.04 starts sshd on demand; the checks need this folder
sshd -t
sshd -T | grep -qx "passwordauthentication no" || { echo "password logins are still on"; exit 1; }
sshd -T | grep -qx "permitrootlogin without-password" || { echo "root login isn't key-only"; exit 1; }
systemctl reload ssh 2>/dev/null || systemctl restart ssh

echo "== firewall: nothing in except SSH"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "== logs: journal capped at 200 MB"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/igdm.conf
systemctl restart systemd-journald

echo "== uv (Python tooling) for igdm"
[ -x /home/igdm/.local/bin/uv ] || sudo -u igdm -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
if [ -n "$REPO" ]; then  # so rehearse.sh, igdm check and server_start.sh work straight away
    echo "== the bot's Python packages"
    sudo -u igdm -H bash -c 'cd "$1" && ~/.local/bin/uv sync --frozen -q' _ "$APP"
fi

echo "== services (installed, NOT started: cutover.sh or server_start.sh starts them)"
install -m 644 "$HERE/igdm.service" "$HERE/igdm-dashboard.service" "$HERE/igdm-backup.service" \
    "$HERE/igdm-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now igdm-backup.timer  # its service waits for data/LIVE_HERE

echo "== igdm may manage only its own services"
cat > /etc/sudoers.d/igdm <<'EOF'
Cmnd_Alias IGDM = /usr/bin/systemctl try-restart igdm igdm-dashboard, \
                  /usr/bin/systemctl enable --now igdm igdm-dashboard, \
                  /usr/bin/systemctl disable --now igdm igdm-dashboard, \
                  /usr/bin/systemctl start igdm, /usr/bin/systemctl stop igdm, \
                  /usr/bin/systemctl start igdm-backup
igdm ALL=(root) NOPASSWD: IGDM
EOF
chmod 440 /etc/sudoers.d/igdm
visudo -cf /etc/sudoers.d/igdm

echo "== Tailscale (private network)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! tailscale status >/dev/null 2>&1; then
    echo ">>> Open the login link below in your browser and approve this server."
    tailscale up --hostname=igdm
fi
echo ">>> If this asks you to enable HTTPS, open its link and click Enable, then run this script again."
tailscale serve --bg --https=443 http://127.0.0.1:8787
NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"

echo
echo "Done. Dashboard address: https://$NAME"
echo "DASHBOARD_HOSTS=$NAME"
echo "The bot and dashboard are installed but NOT running. Only scripts/cutover.sh (Mac) or scripts/server_start.sh (Windows) starts them."
