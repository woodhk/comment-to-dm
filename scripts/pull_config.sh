#!/usr/bin/env bash
# Copy the server's live config.toml (which the dashboard's Reels tab edits) into this folder.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_server.sh"
scp -q "$IGDM_SERVER:$IGDM_APP_DIR/config.toml" "$PROJECT/config.toml"
remember_config "$PROJECT/config.toml"
git -C "$PROJECT" diff --stat config.toml
echo "pulled the server's config.toml (commit it if it changed)"
