#!/usr/bin/env bash
# The first start of the bot on a creator's server, when no computer is running it (a Windows
# creator's setup). A Mac that runs the bot moves it with scripts/cutover.sh instead.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"

if [ "$(uname -s)" = Darwin ] && launchctl print "gui/$(id -u)/local.igdm" >/dev/null 2>&1; then
    echo "Your Mac is running the bot. Use the move step instead (scripts/cutover.sh)."
    exit 1
fi

say "1. looking at the server"
STATE="$(remote "if [ -f data/LIVE_HERE ]; then echo live; elif [ -f config.toml ]; then echo ready; else echo no-settings; fi" || true)"
case "$STATE" in
    live) echo "The bot is already running on the server."; exit 1 ;;
    no-settings) echo "The server has no settings file (config.toml) yet. Send the settings first."; exit 1 ;;
    ready) ;;
    *) echo "Couldn't reach the server. Check it's switched on and that \"ssh igdm\" works."; exit 1 ;;
esac

say "2. checking the login keys from the server"
if ! remote "$UV run --frozen igdm check"; then
    echo "The check failed, so I didn't start the bot. Fix what it says above, then run this again."
    exit 1
fi
NAME="$(server_name)"
[ -n "$NAME" ] || { echo "Couldn't find the server's Tailscale name. Is Tailscale on there?"; exit 1; }
ensure_dashboard_host "$NAME"

say "3. starting the bot and dashboard on the server"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
switch_off() {  # after LIVE_HERE: put the server back to "not running", so a rerun can start it
    if remote "sudo systemctl disable --now igdm igdm-dashboard; rm -f data/LIVE_HERE"; then
        echo "The bot didn't start, so I switched it off again. Nothing is running. Ask Claude to look at: ssh igdm 'journalctl -u igdm -n 50'"
    else
        echo "The bot didn't start, and I couldn't reach the server to switch it off. Ask Claude to look at: ssh igdm 'journalctl -u igdm -n 50'"
    fi
    exit 1
}
remote "touch data/LIVE_HERE && sudo systemctl enable --now igdm igdm-dashboard" || switch_off
if ! wait_first_tick "$STARTED"; then
    echo "The server bot hasn't finished a check in 5 minutes."
    switch_off
fi

echo
echo "The bot is running on your server. Dashboard: https://$NAME"
