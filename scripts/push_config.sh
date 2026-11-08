#!/usr/bin/env bash
# Send this folder's config.toml to the server and restart both services (if running).
# Refuses if the server's copy changed since your last pull, so a Reels-tab edit on the
# dashboard is never overwritten.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
scp -q "$IGDM_SERVER:$IGDM_APP_DIR/config.toml" "$TMP"
if [ -f "$LAST_CONFIG" ]; then
    seen() { cmp -s "$TMP" "$LAST_CONFIG"; }
else  # never pulled or pushed from this Mac: the committed copy is what it last saw
    seen() { git show HEAD:config.toml | cmp -s "$TMP" -; }
fi
if ! cmp -s "$TMP" config.toml && ! seen; then
    echo "The server's config changed since your last pull (maybe a reel was added on the dashboard)."
    echo "Run scripts/pull_config.sh first, redo your edit, then push again."
    exit 1
fi
uv run python -c "from igdm.config import load_config; load_config('config.toml')"
scp -q config.toml "$IGDM_SERVER:$IGDM_APP_DIR/config.toml"
remember_config config.toml
remote "sudo systemctl try-restart igdm igdm-dashboard"
echo "pushed config.toml; bot, dashboard: $(remote "systemctl is-active igdm igdm-dashboard" | tr '\n' ' ' || true)"
