#!/usr/bin/env bash
# Update this copy of the bot to the newest version on GitHub, safely.
# It backs up your settings and database, updates, upgrades your settings, runs the tests and
# the check, and puts everything back as it was if any of that fails.
# Works on a Mac (the local agent) and on a server (the igdm systemd services).
# Exit codes: 0 already up to date or updated, 1 refused (nothing changed),
#             2 the update failed and the old version was put back.
# Written for macOS's bash 3.2.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

DB="data/igdm.sqlite"
SERVER=0
if [ "${IGDM_FORCE_SERVER:-}" = 1 ]; then  # for the tests only
    SERVER=1
elif [ "$(uname)" = Linux ] && [ -f /etc/systemd/system/igdm.service ]; then
    SERVER=1
fi

# Printing never stops the script, even if the terminal has gone away.
say() { printf '%s\n' "$*" 2>/dev/null || true; }
refuse() { say "$@"; exit 1; }

# 0. On Windows the bot only ever runs on the server, so there's nothing to update on this computer.
case "$(uname -s 2>/dev/null || true)" in
    MINGW* | MSYS*)
        refuse "On Windows your bot runs on your server, not on this computer, so there's nothing to update here. Ask Claude to \"update my bot\" and it updates the server. Nothing was changed." ;;
esac

# 1. Only a git download can be updated this way.
if [ ! -e .git ] || ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
    refuse "Updates need the project downloaded with git. Ask Claude to set that up."
fi

# 2. Never update over files someone has changed.
CHANGED="$(git status --porcelain --untracked-files=no | cut -c4- | tr '\n' ',' | sed -e 's/,$//' -e 's/,/, /g')"
if [ -n "$CHANGED" ]; then
    refuse "You've changed these files, so I won't update over them: $CHANGED"
fi

if [ "$SERVER" = 0 ] && [ -f data/MOVED_TO_SERVER ]; then
    refuse "This Mac handed the bot to your server, so there's nothing to update here. Update the server instead."
fi

[ -f config.toml ] || refuse "I can't find your settings file (config.toml) in $PROJECT, so I won't update. Ask Claude to help."

if ! command -v uv >/dev/null 2>&1; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        PATH="$HOME/.local/bin:$PATH"
    else
        refuse "uv isn't installed, so I can't update. Install it from https://docs.astral.sh/uv/ and run this again."
    fi
fi
if [ -f "$DB" ] && ! command -v sqlite3 >/dev/null 2>&1; then
    refuse "sqlite3 isn't installed, so I can't back up your database. Ask Claude to install it, then run this again."
fi

# 3. Is there anything new?
say "Checking for a new version..."
if ! GIT_TERMINAL_PROMPT=0 git fetch -q origin >/dev/null 2>&1; then  # never wait on a password prompt
    refuse "I couldn't reach GitHub to check for a new version. Check your internet connection and run this again. Nothing was changed."
fi
if ! NEW="$(git rev-parse -q --verify 'origin/main^{commit}' 2>/dev/null)"; then
    refuse "I couldn't find the main version on GitHub, so I won't update. Ask Claude to look at it. Nothing was changed."
fi
OLD="$(git rev-parse HEAD)"
if [ "$OLD" = "$NEW" ]; then
    say "Already up to date."
    exit 0
fi
if ! git merge-base --is-ancestor HEAD "$NEW"; then
    refuse "This copy has its own changes saved in git that aren't on GitHub, so I won't update over them. Ask Claude to help. Nothing was changed."
fi

# 4. A backup folder for this update.
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="data/backups/update-$STAMP"  # relative, so no odd folder names reach sqlite3
mkdir -p data/backups
if ! mkdir "$BACKUP" 2>/dev/null; then
    BACKUP="$BACKUP-$$"
    mkdir "$BACKUP" || refuse "I couldn't make a backup folder in $PROJECT/data/backups, so I won't update. Nothing was changed."
fi
LOG="$BACKUP/update.log"
SHOWN="$PROJECT/$BACKUP"
: > "$LOG"

# run_step <command...>: run it with all its output in the update log.
run_step() {
    {
        echo
        echo "== $*"
    } >>"$LOG" 2>&1
    "$@" >>"$LOG" 2>&1
}

stop_bot() {
    if [ "$SERVER" = 1 ]; then
        run_step sudo systemctl stop igdm
    else
        run_step bash scripts/uninstall_agent.sh
    fi
}

start_bot() {
    if [ "$SERVER" = 1 ]; then
        run_step sudo systemctl start igdm && run_step sudo systemctl try-restart igdm igdm-dashboard
    else
        run_step bash scripts/install_agent.sh
    fi
}

CONFIG_SAVED=0
DB_SAVED=0
NEW_STARTED=0  # set once the new version may have run, and so may have changed the database
RESTORING=0
SAVED_WHAT="settings"
[ -f "$DB" ] && SAVED_WHAT="settings and database"

# db_check: how the live database's data version compares with the backup's.
# Returns 0 when it changed, 1 when it's the same, 2 when either can't be read.
db_check() {
    local live old
    live="$(sqlite3 "$DB" "PRAGMA user_version;" 2>>"$LOG")" || live=""
    old="$(sqlite3 "$BACKUP/igdm.sqlite" "PRAGMA user_version;" 2>>"$LOG")" || old=""
    if [ -z "$live" ] || [ -z "$old" ]; then
        echo "== couldn't read the database versions (live '$live', backup '$old')" >>"$LOG"
        return 2
    fi
    echo "== database version: live $live, backup $old" >>"$LOG"
    [ "$live" != "$old" ] && return 0
    return 1
}

# db_needs_restore: true when the live database should go back to the backup. A changed data
# version always means yes: the dashboard can restart mid-update on the new code and run a data
# step, even before the bot is started. An unreadable version means yes only once the new
# version was started; before that, the live database is left alone.
db_needs_restore() {
    local result=0
    db_check || result=$?
    case "$result" in
        0) return 0 ;;
        2) if [ "$NEW_STARTED" = 1 ]; then
               echo "== the new version was started, so the backup goes back" >>"$LOG"
               return 0
           fi
           echo "== the new version was never started, so the live database is kept" >>"$LOG"
           return 1 ;;
        *) return 1 ;;
    esac
}

# Put the old version, settings and database back, start the bot again, and exit 2.
restore_and_exit() {
    set +e
    trap '' INT TERM HUP
    if [ "$RESTORING" = 1 ]; then return 0; fi
    RESTORING=1
    say "Something went wrong, so I'm putting your bot back as it was..."
    # The new version may already be running (step 8), so stop it before putting anything back.
    stop_bot || true
    local ok=1
    run_step git reset -q --hard "$OLD" || ok=0
    if [ "$CONFIG_SAVED" = 1 ]; then
        run_step cp "$BACKUP/config.toml" config.toml || ok=0
    fi
    # The live database is kept (with everything the bot and the dashboard recorded, so nobody
    # gets a second DM). It is only put back if the new code ran a data upgrade the old code can't
    # read (see db_needs_restore). The backup stays in the folder either way.
    local lost="Nothing was lost."
    if [ "$DB_SAVED" = 1 ] && db_needs_restore; then
        # keep what the new version recorded, then put the old database back
        if run_step sqlite3 "$DB" ".backup '$BACKUP/igdm-new-version.sqlite'"; then
            lost="Nothing was lost. Your database is back to the copy from before the update, and what the new version recorded is saved in $SHOWN/igdm-new-version.sqlite."
        else
            lost="Your database is back to the copy from before the update, and I couldn't keep a copy of what the new version recorded in the meantime."
        fi
        # .restore goes through SQLite, so it is safe even if something still has the database open
        run_step sqlite3 "$DB" ".restore '$BACKUP/igdm.sqlite'" || ok=0
    fi
    run_step uv sync --frozen -q || ok=0
    local started=1
    say "Starting your bot again..."
    start_bot || started=0
    if [ "$ok" = 1 ] && [ "$started" = 1 ]; then
        say "The update didn't work, so I put your bot back as it was. $lost Ask Claude to look at: $SHOWN/update.log"
    elif [ "$ok" = 1 ]; then
        say "The update didn't work, so I put your bot back as it was. $lost But your bot didn't start again, so it isn't running now. Ask Claude to start it and to look at: $SHOWN/update.log"
    else
        say "The update didn't work, and I couldn't put everything back by myself. Your $SAVED_WHAT are safe in $SHOWN. Ask Claude to look at: $SHOWN/update.log"
        if [ "$started" = 0 ]; then
            say "Your bot isn't running now. Ask Claude to start it."
        fi
    fi
    exit 2
}

# From here the bot gets stopped, so an interrupted run (Ctrl-C, closed window) puts it back too.
trap 'say ""; say "The update was interrupted."; restore_and_exit' INT TERM HUP

# 5. Stop the bot.
say "Stopping your bot for the update..."
if ! stop_bot; then
    say "I couldn't stop your bot, so I won't update."
    start_bot || say "Your bot may not be running now. Ask Claude to start it and to look at: $SHOWN/update.log"
    say "Nothing was changed. Ask Claude to look at: $SHOWN/update.log"
    exit 1
fi

# 6. Back up the settings and the database.
say "Backing up your $SAVED_WHAT..."
backup_failed() {
    trap - INT TERM HUP
    say "I couldn't back up your $SAVED_WHAT, so I won't update. Starting your bot again..."
    if start_bot; then
        say "Your bot is running as before. Nothing was changed. Ask Claude to look at: $SHOWN/update.log"
    else
        say "Nothing was changed, but your bot didn't start again. Ask Claude to start it and to look at: $SHOWN/update.log"
    fi
    exit 1
}
if [ -f "$DB" ]; then
    run_step sqlite3 "$DB" ".backup '$BACKUP/igdm.sqlite'" && [ -s "$BACKUP/igdm.sqlite" ] || backup_failed
    DB_SAVED=1
fi
run_step cp config.toml "$BACKUP/config.toml" && cmp -s config.toml "$BACKUP/config.toml" || backup_failed
CONFIG_SAVED=1

# 7. Update, upgrade the settings, test and check. Any failure puts everything back.
say "Downloading the new version..."
run_step git merge -q --ff-only "$NEW" || restore_and_exit
say "Installing what the new version needs..."
run_step uv sync --frozen -q || restore_and_exit
say "Upgrading your settings..."
run_step uv run --frozen igdm upgrade || restore_and_exit
say "Running the tests (this can take a minute)..."
run_step uv run --frozen pytest -q || restore_and_exit
say "Checking your keys and settings..."
run_step uv run --frozen igdm check || restore_and_exit

# 8. Start the bot again.
say "Starting your bot again..."
NEW_STARTED=1
start_bot || restore_and_exit
trap - INT TERM HUP

say "Updated to $(git rev-parse --short HEAD). What's new:"
git log --oneline "$OLD..HEAD" 2>/dev/null || true
say "Your old $SAVED_WHAT are saved in $SHOWN"
exit 0
