#!/usr/bin/env bash
# Move the bot from this Mac to the server. Run from the live folder, on main, after
# scripts/rehearse.sh worked. If anything fails before the server bot starts, the Mac bot is
# put back automatically. After that, scripts/rollback.sh moves it back.
set -Eeuo pipefail  # -E: failures inside helper functions also run on_fail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
STAGE=0
on_fail() {
    [ "$BASH_SUBSHELL" -gt 0 ] && return 0  # inside $(...): the failing command in the main shell handles it
    trap - ERR
    if [ "$STAGE" -ge 7 ]; then
        echo; echo "Cutover failed after the server bot started. Run scripts/rollback.sh to move back to the Mac."
    elif [ "$STAGE" -ge 3 ]; then
        echo; echo "Cutover failed before the server sent anything. Putting the Mac back..."
        remote "rm -f data/LIVE_HERE" || true
        bash "$PROJECT/scripts/install_agent.sh"
        echo "The Mac is running the bot again. Nothing changed."
    else
        echo; echo "Stopped before touching the Mac. The Mac bot is still running. Nothing changed."
    fi
}
trap on_fail ERR

say "1. checks"
[ "$(git branch --show-current)" = main ] || { echo "Run this on main."; exit 1; }
# Only changes to tracked files count. Files git doesn't track (an emails export, a leftover test
# settings copy) never reach the server, so they don't block the move.
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Commit or discard local changes first."; git status --short --untracked-files=no; exit 1; }
[ -f deploy/igdm.service ] || { echo "The server work isn't merged into main yet."; exit 1; }
[ ! -f data/MOVED_TO_SERVER ] || { echo "Already moved ($(cat data/MOVED_TO_SERVER))."; exit 1; }
! remote "test -f data/LIVE_HERE" || { echo "The server is already live."; exit 1; }
if server_has_git; then  # a creator's server takes its code from GitHub, at the version this Mac runs
    VERSIONS='Your Mac and GitHub have different versions. Say "update my bot" first, then move.'
    remote "git fetch -q origin"
    [ "$(git rev-parse HEAD)" = "$(remote "git rev-parse origin/main")" ] || { echo "$VERSIONS"; exit 1; }
    SERVER_CHANGED="$(remote "git status --porcelain --untracked-files=no" | cut -c4- | tr '\n' ',' | sed -e 's/,$//' -e 's/,/, /g')"
    if [ -n "$SERVER_CHANGED" ]; then
        echo "These files were changed on the server: $SERVER_CHANGED"
        echo "I won't move over local changes on the server. $VERSIONS"
        exit 1
    fi
    remote "git merge -q --ff-only origin/main"
    [ "$(remote "git rev-parse HEAD")" = "$(git rev-parse HEAD)" ] || { echo "$VERSIONS"; exit 1; }
    remote "$UV sync --frozen -q && if [ -f config.toml ]; then $UV run --frozen igdm upgrade; fi"
else
    bash scripts/deploy.sh
fi
NAME="$(server_name)"

STAGE=2; say "2. checking the login keys from the server"
scp -q .env config.toml "$IGDM_SERVER:$IGDM_APP_DIR/"
remember_config config.toml
ensure_dashboard_host "$NAME"
remote "$UV run --frozen igdm check"

STAGE=3; say "3. stopping the bot and dashboard on this Mac"
bash scripts/uninstall_agent.sh
for _ in $(seq 60); do pgrep -f "igdm (run|dashboard)" >/dev/null || break; sleep 1; done
! pgrep -f "igdm (run|dashboard)" >/dev/null || { echo "The Mac bot didn't stop."; false; }

scp -q config.toml "$IGDM_SERVER:$IGDM_APP_DIR/"  # again: the Mac dashboard could edit it until now
remember_config config.toml

STAGE=4; say "4. taking a clean copy of the Mac database"
SNAP="data/cutover-copy.sqlite"
rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm"
sqlite3 data/igdm.sqlite ".backup '$SNAP'"  # complete even if writes were still in the WAL
sqlite3 "$SNAP" "PRAGMA journal_mode=DELETE;" >/dev/null  # one self-contained file to copy
LOCAL_ROWS="$(sqlite3 "$SNAP" "SELECT COUNT(*) FROM requests")"

STAGE=5; say "5. clearing the server's data folder"
remote "rm -f data/igdm.sqlite data/igdm.sqlite-wal data/igdm.sqlite-shm"

STAGE=6; say "6. copying the database to the server"
scp -q "$SNAP" "$IGDM_SERVER:$IGDM_APP_DIR/data/igdm.sqlite"
if [ -f data/token.json ]; then scp -q data/token.json "$IGDM_SERVER:$IGDM_APP_DIR/data/token.json"; fi
[ "$(remote "sqlite3 data/igdm.sqlite 'PRAGMA integrity_check'")" = ok ] || { echo "The copy is damaged."; false; }
[ "$(remote "sqlite3 data/igdm.sqlite 'SELECT COUNT(*) FROM requests'")" = "$LOCAL_ROWS" ] || { echo "The copy is missing rows."; false; }

STAGE=7; say "7. locking the Mac, then starting the bot and dashboard on the server"
echo "$(date '+%Y-%m-%d %H:%M') to $NAME" > data/MOVED_TO_SERVER  # before the server starts
STARTED="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
remote "touch data/LIVE_HERE && sudo systemctl enable --now igdm igdm-dashboard"
wait_first_tick "$STARTED" || { echo "The server bot hasn't finished a check in 5 minutes."; false; }
rm -f "$SNAP" "$SNAP-wal" "$SNAP-shm"

STAGE=8; say "8. backups to this Mac"
bash scripts/install_agent.sh --backups-only

cat <<MSG

Done. The bot runs on the server now.
Dashboard: https://$NAME
To move back: scripts/rollback.sh
MSG
