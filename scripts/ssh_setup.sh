#!/usr/bin/env bash
# The ssh key and the "igdm" name for a creator's server. Runs on a Mac and in Git Bash on Windows.
#   scripts/ssh_setup.sh key                      make ~/.ssh/igdm_ed25519 (once) and print where
#                                                 its public half is
#   scripts/ssh_setup.sh host <address> [<user>]  point "ssh igdm" at the server (user igdm unless
#                                                 given), replacing the earlier entry
# The private key is never printed.
set -euo pipefail

SSH_DIR="$HOME/.ssh"
KEY="$SSH_DIR/igdm_ed25519"

usage() {
    echo "Use scripts/ssh_setup.sh key, or scripts/ssh_setup.sh host <address> [<user>]."
    exit 1
}

ssh_dir() { mkdir -p "$SSH_DIR" && chmod 700 "$SSH_DIR"; }

plain() {  # letters, digits, dots, dashes, underscores and colons only: nothing that could add a line
    case "$1" in "" | *[!A-Za-z0-9._:-]*) return 1 ;; esac
}

case "${1:-}" in
    key)
        ssh_dir
        if [ ! -f "$KEY" ]; then
            ssh-keygen -t ed25519 -N "" -C igdm -f "$KEY" >/dev/null
        elif [ ! -f "$KEY.pub" ]; then
            ssh-keygen -y -f "$KEY" > "$KEY.pub"
        fi
        echo "$KEY.pub"
        ;;
    host)
        ADDRESS="${2:-}"
        USER_NAME="${3:-igdm}"
        plain "$ADDRESS" || { echo "That server address doesn't look right: \"$ADDRESS\"."; exit 1; }
        plain "$USER_NAME" || { echo "That user name doesn't look right: \"$USER_NAME\"."; exit 1; }
        ssh_dir
        CONFIG="$SSH_DIR/config"
        [ -f "$CONFIG" ] || : > "$CONFIG"
        IGDM_BLOCK="# igdm start
Host igdm
    HostName $ADDRESS
    User $USER_NAME
    IdentityFile ~/.ssh/igdm_ed25519
    IdentitiesOnly yes
# igdm end"
        export IGDM_BLOCK
        # Before changing anything: the markers must come as one start-end pair (or not at all),
        # and no other "Host igdm" entry may be there, since ssh would use whichever comes first.
        problem=0
        awk '
            $0 == "# igdm start" { if (inside || seen) bad = 1; inside = 1; seen = 1; next }
            $0 == "# igdm end" { if (!inside) bad = 1; inside = 0; next }
            !inside && tolower($1) == "host" { for (i = 2; i <= NF; i++) if (tolower($i) == "igdm") other = 1 }
            END { if (inside) bad = 1; if (bad) exit 2; if (other) exit 3 }
        ' "$CONFIG" || problem=$?
        case "$problem" in
            0) ;;
            2) echo "The igdm entry in ~/.ssh/config is broken (its \"# igdm start\" and \"# igdm end\" lines don't pair up). I left the file as it is. Fix or remove those lines, then run this again."; exit 1 ;;
            3) echo "~/.ssh/config already has a \"Host igdm\" entry that this script didn't write. I left the file as it is. Remove or rename that entry, then run this again."; exit 1 ;;
            *) echo "Couldn't read ~/.ssh/config."; exit 1 ;;
        esac
        TMP="$SSH_DIR/config.igdm-new"
        trap 'rm -f "$TMP"' EXIT
        # The marked block is replaced where it is. Without one, it goes at the end.
        (umask 077 && awk '
            $0 == "# igdm start" { print ENVIRON["IGDM_BLOCK"]; done = 1; skip = 1; next }
            skip { if ($0 == "# igdm end") skip = 0; next }
            { print; lines++ }
            END { if (!done) { if (lines) print ""; print ENVIRON["IGDM_BLOCK"] } }
        ' "$CONFIG" > "$TMP")
        # Written into the file, not moved over it, so a symlinked ~/.ssh/config stays a symlink.
        cat "$TMP" > "$CONFIG"
        chmod 600 "$CONFIG"
        echo "\"ssh igdm\" now goes to $USER_NAME@$ADDRESS."
        ;;
    *) usage ;;
esac
