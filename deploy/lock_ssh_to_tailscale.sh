#!/usr/bin/env bash
# Optional, last: only allow SSH over Tailscale. Run as root ONLY after `ssh igdm@igdm`
# (over Tailscale) has been confirmed to work, or you can lock yourself out (the Hetzner
# web console still gets you in).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
ufw allow in on tailscale0 to any port 22 proto tcp
ufw delete allow OpenSSH
ufw status verbose
