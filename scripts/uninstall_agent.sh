#!/usr/bin/env bash
# Stop the igdm LaunchAgents (bot and dashboard) and remove them, including any left by an
# older version of this script under a different label.
set -euo pipefail

DOMAIN="gui/$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"

remove() {
    local label="$1"
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || echo "($label was not running)"
    rm -f "$AGENTS/$label.plist"
    echo "Uninstalled $label."
}

remove local.igdm
remove local.igdm-dashboard
for plist in "$AGENTS"/*.igdm.plist "$AGENTS"/*.igdm-dashboard.plist; do
    [ -e "$plist" ] || continue
    remove "$(basename "$plist" .plist)"
done
