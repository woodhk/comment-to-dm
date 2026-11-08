# Shared settings for the Mac-side server scripts. Sourced, not run.
# IGDM_SERVER: the ssh name of the server (default "igdm", the Host block in ~/.ssh/config
# that points at the Tailscale name with the igdm user and key). IGDM_APP_DIR: the app folder
# on the server, relative to the igdm user's home (default "app").
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$PROJECT/scripts/server.env" ] && . "$PROJECT/scripts/server.env"
IGDM_SERVER="${IGDM_SERVER:-igdm}"
IGDM_APP_DIR="${IGDM_APP_DIR:-app}"
UV='~/.local/bin/uv'

remote() {  # run a command on the server, inside the app folder
    ssh -o ConnectTimeout=15 "$IGDM_SERVER" "cd $IGDM_APP_DIR && $*"
}

server_reachable() {
    ssh -o ConnectTimeout=10 -o BatchMode=yes "$IGDM_SERVER" true >/dev/null 2>&1
}

server_name() {  # the server's Tailscale name, e.g. igdm.tail1234.ts.net (parsed there: this computer may have no python3)
    remote "tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"Self\"][\"DNSName\"].rstrip(\".\"))'"
}

server_has_git() {  # is the server's app folder a git clone (a creator's server, updated from GitHub)?
    # Never guesses: if the server doesn't answer, the script stops rather than treat a clone
    # as a server without git (deploy.sh would copy over it).
    local answer
    answer="$(remote "test -e .git && echo yes || echo no" || true)"
    case "$answer" in
        yes) return 0 ;;
        no) return 1 ;;
        *) echo "Couldn't reach the server to check how it gets its code."; exit 1 ;;
    esac
}

ensure_dashboard_host() {  # the server .env gets DASHBOARD_HOSTS=<its Tailscale name>
    local name="$1"
    remote "chmod 600 .env && (grep -q '^DASHBOARD_HOSTS=' .env || printf '\nDASHBOARD_HOSTS=%s\n' '$name' >> .env)"
}

wait_first_tick() {  # did the server bot finish a check after $1 (UTC, ISO)? Polls for up to 5 minutes.
    # The times are compared on the server: this computer may have no python3.
    local started="$1" _
    echo "waiting for the server bot's first check (up to 5 minutes)..."
    for _ in $(seq "${IGDM_START_POLLS:-30}"); do
        sleep "${IGDM_START_WAIT:-10}"
        if remote "sqlite3 data/igdm.sqlite \"SELECT json_extract(value, '\$.end') FROM kv WHERE key='last_tick'\" | python3 -c 'import sys; from datetime import datetime as d; e = sys.stdin.read().strip(); sys.exit(0 if e and d.fromisoformat(e) >= d.fromisoformat(sys.argv[1]) else 1)' '$started'"; then
            return 0
        fi
    done
    return 1
}

# The server's config.toml as this Mac last saw it (pulled, pushed or sent at cutover), so
# push_config.sh can tell whether the dashboard changed it since.
LAST_CONFIG="$PROJECT/data/server-config.last.toml"
remember_config() { mkdir -p "$PROJECT/data" && cp "$1" "$LAST_CONFIG"; }

settings_were_upgraded() {  # reads the output of `igdm upgrade`: did it change the settings file?
    grep -q "upgraded from version"
}

say() { printf '\n== %s\n' "$*"; }
