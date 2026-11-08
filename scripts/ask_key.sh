#!/usr/bin/env bash
# Opens a new terminal window where the creator types one secret into `igdm set-key NAME`, so
# the secret never passes through this script or the chat.
#   scripts/ask_key.sh NAME             on a Mac: saves it in this folder's .env
#   scripts/ask_key.sh NAME --server    saves it in the server's .env, over ssh igdm (Mac or Windows)
#   --print                             prints the command for the window instead of opening it
# NAME must be one of the keys in .env.example.
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { echo "Use scripts/ask_key.sh NAME [--server] [--print], with NAME from .env.example."; exit 1; }

NAME=""; SERVER=no; PRINT=no; GOT_NAME=no
for arg in "$@"; do
    case "$arg" in
        --server) SERVER=yes ;;
        --print) PRINT=yes ;;
        -*) usage ;;
        *) [ "$GOT_NAME" = no ] || usage; NAME="$arg"; GOT_NAME=yes ;;
    esac
done
[ "$GOT_NAME" = yes ] || usage

# Only a plain key name that .env.example lists. This also keeps anything else out of the command.
known=no
case "$NAME" in
    "" | *[!ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]*) ;;  # spelled out: [A-Z] can match a-z in some locales
    *) if cut -d= -f1 "$PROJECT/.env.example" | grep -qxF "$NAME"; then known=yes; fi ;;
esac
[ "$known" = yes ] || { echo "\"$NAME\" isn't one of the bot's keys (see .env.example)."; exit 1; }

sh_quote() {  # the text in single quotes for a shell: nothing inside is expanded, not even "!"
    printf "'%s'" "$(printf '%s' "$1" | LC_ALL=C sed "s/'/'\\\\''/g")"
}
applescript_quote() {  # the text, safe inside "..." in AppleScript
    printf '%s' "$1" | LC_ALL=C sed 's/[\\"]/\\&/g'
}

if [ "$SERVER" = yes ]; then
    COMMAND="ssh -t igdm 'cd app && ~/.local/bin/uv run igdm set-key $NAME'"
else
    COMMAND="cd $(sh_quote "$PROJECT") && uv run igdm set-key $NAME"
fi

SYSTEM="$(uname -s)"
case "$SYSTEM" in
    MINGW* | MSYS*)
        [ "$SERVER" = yes ] || {
            echo "On Windows the bot runs on the server, so use scripts/ask_key.sh $NAME --server."
            exit 1
        } ;;
    Darwin) ;;
    *) [ "$PRINT" = yes ] || { echo "I can only open a window on a Mac or on Windows."; exit 1; } ;;
esac

if [ "$PRINT" = yes ]; then
    printf '%s\n' "$COMMAND"
    exit 0
fi

# On a Mac the key window looks like any other Terminal window, so it gets its own title and a
# banner first, and the creator can tell it apart from the window Claude Code runs in.
TITLE="PRIVATE KEY WINDOW"
BANNER="printf '\\n  THIS IS THE PRIVATE KEY WINDOW.\\n  Paste here, not in the Claude chat.\\n\\n'"

case "$SYSTEM" in
    Darwin)
        osascript -e 'tell application "Terminal"' \
                  -e "set w to do script \"$(applescript_quote "$BANNER; $COMMAND")\"" \
                  -e "try" -e "set custom title of w to \"$TITLE\"" -e "set title displays custom title of w to true" -e "end try" \
                  -e 'activate' -e 'end tell' >/dev/null
        WINDOW="A Terminal window called $TITLE" ;;
    *)
        cmd.exe //c start "" powershell -NoExit -Command "$COMMAND"
        WINDOW="A PowerShell window" ;;
esac
echo "$WINDOW is open. Type or paste your $NAME there and press Enter. It stays hidden while you type."
