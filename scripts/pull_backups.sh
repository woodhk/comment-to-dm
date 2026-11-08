#!/usr/bin/env bash
# Copy the server's nightly backups to this Mac. Quietly does nothing when the server can't
# be reached (Mac offline). Run hourly by the igdm-backup-pull agent from ~/.igdm/ (macOS
# doesn't let background jobs read the Desktop or Documents, so this script is standalone and
# install_agent.sh --backups-only copies it there).
set -euo pipefail
IGDM_SERVER="${IGDM_SERVER:-igdm}"
IGDM_APP_DIR="${IGDM_APP_DIR:-app}"
DEST="${IGDM_BACKUP_DIR:-$HOME/igdm-backups}"
ssh -o ConnectTimeout=10 -o BatchMode=yes "$IGDM_SERVER" true >/dev/null 2>&1 || exit 0
mkdir -p "$DEST"
rsync -az "$IGDM_SERVER:$IGDM_APP_DIR/data/backups/" "$DEST/"
echo "$(date '+%F %T') backups pulled to $DEST"
