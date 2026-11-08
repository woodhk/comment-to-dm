#!/usr/bin/env bash
# Put the committed code on the server and restart the bot and dashboard IF they're running.
# Never starts a stopped service (only cutover.sh does), and never overwrites the server's
# config.toml, .env, data/ or logs/ (the code copy skips them).
# It does run `igdm upgrade` on the server's config.toml. If that finds an older settings
# version and upgrades it, the server's [owner] section holds generic default wording, not
# yours, so deploy stops there: nothing is restarted, it warns, and it exits non-zero. Fix
# it with pull_config.sh, check [owner], push_config.sh, then run deploy.sh again.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
cd "$PROJECT"
DIRTY="$(git status --porcelain | grep -v ' config.toml$' || true)"
if [ -n "$DIRTY" ]; then
    echo "Commit these changes first (config.toml is fine, it isn't deployed):"
    echo "$DIRTY"
    exit 1
fi
COMMIT="$(git rev-parse --short HEAD)"
say "copying code ($COMMIT) to $IGDM_SERVER"
rsync -az --delete \
    --exclude /config.toml --exclude '/config.toml.bak-*' --exclude /.env --exclude /data/ --exclude /logs/ --exclude /.venv/ \
    --exclude /.git --exclude /.worktrees/ --exclude /.superpowers/ --exclude /scripts/server.env \
    --exclude __pycache__/ --exclude .pytest_cache/ \
    ./ "$IGDM_SERVER:$IGDM_APP_DIR/"
say "installing dependencies"
remote "$UV sync --frozen -q"
say "checking the server's settings version"
UPGRADE_OUT="$(remote "$UV run --frozen igdm upgrade")"
echo "$UPGRADE_OUT"
if echo "$UPGRADE_OUT" | settings_were_upgraded; then
    cat <<'MSG'

STOPPED. The code is on the server, but nothing was restarted.
The server's settings file was an older version, so it was just upgraded with DEFAULT
owner wording (your name, about and writing style are not in it). Restarting now would
make the live bot write in the wrong voice. Do this, then run deploy.sh again:
  1. scripts/pull_config.sh
  2. open config.toml and check the [owner] section (name, about, style)
  3. scripts/push_config.sh
MSG
    exit 1
fi
remote "echo $COMMIT > DEPLOYED && sudo systemctl try-restart igdm igdm-dashboard"
STATE="$(remote "systemctl is-active igdm igdm-dashboard" | tr '\n' ' ' || true)"
echo "bot, dashboard: $STATE"
case "$STATE" in
    *inactive*) echo "Code is on the server. The bot isn't running there yet, and only scripts/cutover.sh starts it." ;;
esac
echo "deployed $COMMIT"
