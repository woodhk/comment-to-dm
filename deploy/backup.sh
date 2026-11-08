#!/usr/bin/env bash
# Nightly backup: a safe copy of the live database (fine while the bot runs) and config.toml,
# kept 14 days in data/backups/. The Mac pulls them every hour it's awake.
set -euo pipefail
APP="${IGDM_APP:-/home/igdm/app}"
OUT="$APP/data/backups"
DAY="$(date +%F)"
mkdir -p "$OUT"
sqlite3 "$APP/data/igdm.sqlite" ".backup '$OUT/igdm-$DAY.sqlite'"
cp "$APP/config.toml" "$OUT/config-$DAY.toml"
find "$OUT" \( -name 'igdm-*.sqlite' -o -name 'config-*.toml' \) -mtime +14 -delete
echo "backed up to $OUT/igdm-$DAY.sqlite"
