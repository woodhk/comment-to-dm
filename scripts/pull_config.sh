#!/usr/bin/env bash
# Copy the server's live config.toml (which the dashboard's Reels tab edits) into this folder.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
scp -q "$IGDM_SERVER:$IGDM_APP_DIR/config.toml" "$PROJECT/config.toml"
remember_config "$PROJECT/config.toml"
if git -C "$PROJECT" ls-files --error-unmatch config.toml >/dev/null 2>&1; then
    # A copy that keeps config.toml in git (Alex's own): show what changed so it can be committed.
    git -C "$PROJECT" diff --stat config.toml
    echo "pulled the server's config.toml (commit it if it changed)"
else  # a creator's copy: git ignores config.toml, so there's nothing to commit
    echo "Pulled config.toml from the server."
fi
