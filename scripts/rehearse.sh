#!/usr/bin/env bash
# Dress rehearsal, with the Mac bot still running: check the login keys and send a test alert
# FROM the server. Changes nothing on the Mac, and never touches a server that's already live.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
if remote "test -f data/LIVE_HERE"; then
    echo "The server is already running the bot (data/LIVE_HERE exists). Not rehearsing."
    exit 1
fi
NAME="$(server_name)"
say "copying .env and config.toml to the server (no database)"
scp -q .env config.toml "$IGDM_SERVER:$IGDM_APP_DIR/"
remember_config config.toml
ensure_dashboard_host "$NAME"
say "checking Instagram, Jev and the reels from the server (read only)"
remote "$UV run --frozen igdm check"
say "sending a test alert from the server"
remote "$UV run --frozen igdm test-alert"
remote "rm -f data/igdm.sqlite data/igdm.sqlite-wal data/igdm.sqlite-shm"
cat <<MSG

Check your phone and inbox for the test alert, sent from the server. If Meta or Google
complained above, sort that out before cutover. The Mac bot is still running.
MSG
