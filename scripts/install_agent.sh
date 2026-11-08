#!/usr/bin/env bash
# Install and start the igdm LaunchAgents: the bot (`igdm run`, restarts on crash) and the
# dashboard (`igdm dashboard`, http://127.0.0.1:8787). The plists are rendered from
# launchd/*.plist.template for whoever runs this and wherever this folder lives.
#   --backups-only         install just the hourly server-backup pull (used after the move)
#   --print <bot|dashboard|backup-pull>
#                          print that plist as it would be installed, and install nothing
set -euo pipefail

usage() {
    echo "Usage: install_agent.sh [--backups-only | --print <bot|dashboard|backup-pull>]" >&2
    exit 2
}

MODE=install
TARGET=""
case "${1:-}" in
    "") ;;
    --backups-only) MODE=backups ;;
    --print)
        MODE=print
        TARGET="${2:-}"
        case "$TARGET" in bot | dashboard | backup-pull) ;; *) usage ;; esac
        ;;
    *) usage ;;
esac

# Find uv first (before anything else), unless this run only needs the backup job.
UV=""
if [ "$MODE" = backups ] || { [ "$MODE" = print ] && [ "$TARGET" = backup-pull ]; }; then
    :
elif UV="$(command -v uv)" && [ -n "$UV" ]; then
    :
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
else
    echo "uv isn't installed. Install it from https://docs.astral.sh/uv/ and run this again." >&2
    exit 1
fi

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"

# Make a path safe to drop into the plist (XML) and into a sed replacement (\ | & are special).
safe() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/[\\|&]/\\&/g'
}

# render <name>: the plist for launchd/<name>.plist.template, printed to stdout
render() {
    local name="$1"
    sed -e "s|__LABEL__|local.$name|g" \
        -e "s|__PROJECT__|$(safe "$PROJECT")|g" \
        -e "s|__UV__|$(safe "$UV")|g" \
        -e "s|__HOME__|$(safe "$HOME")|g" \
        "$PROJECT/launchd/$name.plist.template"
}

install_one() {
    local name="$1" label="local.$1" dest="$HOME/Library/LaunchAgents/local.$1.plist"
    # Render to a temp file first so a failed render never leaves a half-written plist.
    local tmp
    tmp="$(mktemp "$dest.XXXXXX")"
    render "$name" > "$tmp" || { rm -f "$tmp"; exit 1; }
    mv "$tmp" "$dest"
    # If already loaded, unload first so a re-install picks up changes.
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    # bootout can take a moment to finish; bootstrap fails ("Input/output error") until it has.
    for attempt in 1 2 3 4 5; do
        if launchctl bootstrap "$DOMAIN" "$dest" 2>/dev/null; then
            echo "Installed and started $label. Restart it with: launchctl kickstart -k $DOMAIN/$label"
            return 0
        fi
        sleep 2
    done
    launchctl bootstrap "$DOMAIN" "$dest"  # last try, showing the error
    echo "Installed and started $label. Restart it with: launchctl kickstart -k $DOMAIN/$label"
}

case "$MODE" in
    print)
        case "$TARGET" in
            bot) render igdm ;;
            dashboard) render igdm-dashboard ;;
            backup-pull) render igdm-backup-pull ;;
        esac
        exit 0
        ;;
    backups)
        mkdir -p "$HOME/Library/LaunchAgents"
        # Background jobs can't read the Desktop, so the job runs a copy kept in ~/.igdm
        mkdir -p "$HOME/.igdm"
        cp "$PROJECT/scripts/pull_backups.sh" "$HOME/.igdm/pull_backups.sh"
        chmod +x "$HOME/.igdm/pull_backups.sh"
        install_one igdm-backup-pull
        exit 0
        ;;
esac

MOVED="$PROJECT/data/MOVED_TO_SERVER"
if [ -f "$MOVED" ] && [ "${FORCE:-}" != 1 ]; then
    echo "This Mac handed the bot to the server on $(cat "$MOVED"). Not starting, so people don't get two DMs. To move back, run scripts/rollback.sh." >&2
    exit 1
fi

mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
install_one igdm
install_one igdm-dashboard
echo "Watch the bot: tail -f $PROJECT/logs/igdm.log"
echo "Dashboard: http://127.0.0.1:8787"
