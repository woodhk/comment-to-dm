#!/usr/bin/env bash
# Move the bot back from the server to this Mac, bringing every lead, DM and reel the server
# handled, so nobody is messaged twice.
#   scripts/rollback.sh                       normal case
#   scripts/rollback.sh --server-unreachable  the server is dead: use the newest nightly backup
#                                             on this Mac (anything after it is unknown to the Mac)
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
BACKUPS="${IGDM_BACKUP_DIR:-$HOME/igdm-backups}"
if [ ! -f data/MOVED_TO_SERVER ]; then
    echo "This Mac isn't handed over to the server (no data/MOVED_TO_SERVER). Nothing to roll back."
    exit 1
fi
if pgrep -f "igdm (run|dashboard)" >/dev/null; then
    echo "An igdm bot or dashboard is running on this Mac. Stop it first; nothing changed."
    exit 1
fi
STAMP="$(date +%Y%m%d-%H%M%S)"
[ -f data/igdm.sqlite ] && cp data/igdm.sqlite "data/igdm.sqlite.before-rollback-$STAMP"
rm -f data/igdm.sqlite-wal data/igdm.sqlite-shm

if [ "${1:-}" = --server-unreachable ]; then
    cat <<'MSG'
The server can't be reached, but its bot may still be running (often it's only this Mac's
Tailscale that's off). Two bots would DM people twice, so first switch the server off:
  console.hetzner.cloud > your project > the igdm server > Power > Power off
Leave it off afterwards: switched back on, it would start its bot again. Ask Claude first.
MSG
    read -r -p "Type OFF once the server is powered off: " ANSWER
    [ "$ANSWER" = OFF ] || { echo "Not rolled back."; exit 1; }
    DB="$(ls -t "$BACKUPS"/igdm-*.sqlite 2>/dev/null | head -1 || true)"
    CFG="$(ls -t "$BACKUPS"/config-*.toml 2>/dev/null | head -1 || true)"
    [ -n "$DB" ] || { echo "No backups in $BACKUPS."; exit 1; }
    say "using the newest backup: $DB"
    echo "Comments the server handled after this backup may get a second DM."
    cp "$DB" data/igdm.sqlite
    if [ -n "$CFG" ]; then
        cp "$CFG" config.toml
        # The backup may hold an older settings version than this code accepts.
        say "bringing the settings up to this code's version"
        uv run igdm upgrade || { echo "Couldn't upgrade the settings copied from $CFG. Fix config.toml, then run this again."; exit 1; }
    fi
else
    server_reachable || { echo "Can't reach the server. If it's dead, run: scripts/rollback.sh --server-unreachable"; exit 1; }
    say "1. stopping the bot and dashboard on the server"
    remote "sudo systemctl disable --now igdm igdm-dashboard && rm -f data/LIVE_HERE" ||
        { echo "Couldn't stop the server bot. Nothing changed on this Mac."; exit 1; }
    STATE="$(remote "systemctl is-active igdm igdm-dashboard" || true)"
    if echo "$STATE" | grep -Eqx "active|activating|deactivating|reloading"; then
        echo "The server bot didn't stop ($STATE). Nothing changed on this Mac."
        exit 1
    fi
    say "2. copying the server's database and config back"
    remote "rm -f data/rollback-copy.sqlite* && sqlite3 data/igdm.sqlite \".backup 'data/rollback-copy.sqlite'\" && sqlite3 data/rollback-copy.sqlite 'PRAGMA journal_mode=DELETE;' >/dev/null"
    scp -q "$IGDM_SERVER:$IGDM_APP_DIR/data/rollback-copy.sqlite" data/igdm.sqlite
    scp -q "$IGDM_SERVER:$IGDM_APP_DIR/config.toml" config.toml
    remember_config config.toml
    [ "$(sqlite3 data/igdm.sqlite 'PRAGMA integrity_check')" = ok ] || { echo "The copy is damaged; the old one is data/igdm.sqlite.before-rollback-$STAMP"; exit 1; }
fi
git diff --stat config.toml || true

say "3. starting the bot and dashboard on this Mac"
rm -f data/MOVED_TO_SERVER
bash scripts/install_agent.sh
echo
echo "The Mac is running the bot again. (If config.toml changed above, commit it.)"
