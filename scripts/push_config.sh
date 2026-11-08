#!/usr/bin/env bash
# Send this folder's config.toml to the server and restart both services (if running).
# Refuses if the server's copy changed since your last pull, so a Reels-tab edit on the
# dashboard is never overwritten. The new file is checked on the server before it replaces the
# old one, so a broken edit never reaches the bot.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
ON_SERVER="$(remote "test -f config.toml && echo yes || echo no")" || {
    echo "Couldn't reach the server. Check Tailscale is connected, then try again."; exit 1; }
if [ "$ON_SERVER" = yes ]; then
    scp -q "$IGDM_SERVER:$IGDM_APP_DIR/config.toml" "$TMP"
    if [ -f "$LAST_CONFIG" ]; then
        seen() { cmp -s "$TMP" "$LAST_CONFIG"; }
    else  # never pulled or pushed from this Mac: the committed copy is what it last saw (none in the public copy)
        seen() { git show HEAD:config.toml 2>/dev/null | cmp -s "$TMP" -; }
    fi
    if ! cmp -s "$TMP" config.toml && ! seen; then
        echo "The server's config changed since your last pull (maybe a reel was added on the dashboard)."
        echo "Run scripts/pull_config.sh first, redo your edit, then push again."
        exit 1
    fi
fi  # no config.toml on the server yet: this is the first push, there's nothing to overwrite
# Checked on the server, which has the bot's Python (this computer may not have uv).
scp -q config.toml "$IGDM_SERVER:$IGDM_APP_DIR/config.toml.new"
if ! CHECK="$(remote "$UV run --frozen python -c 'from igdm.config import load_config; load_config(\"config.toml.new\")'" 2>&1)"; then
    remote "rm -f config.toml.new" || true
    echo "The new settings didn't pass the check, so the server keeps its old ones."
    echo "$CHECK"
    exit 1
fi
remote "mv config.toml.new config.toml"
remember_config config.toml
remote "sudo systemctl try-restart igdm igdm-dashboard"
echo "pushed config.toml; bot, dashboard: $(remote "systemctl is-active igdm igdm-dashboard" | tr '\n' ' ' || true)"
