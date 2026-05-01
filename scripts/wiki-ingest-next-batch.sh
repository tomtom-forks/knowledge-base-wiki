#!/usr/bin/env bash
# Run /wiki-ingest-next-batch if Claude 5-hour usage is below a threshold.
#
# The 5-hour usage percentage is fetched from the Anthropic API using the
# OAuth token stored in the macOS Keychain (Claude Code-credentials).
# Falls back to the HUD usage cache (~/.claude/plugins/claude-hud/.usage-cache.json)
# if the API is unreachable.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HUD_CACHE="$HOME/.claude/plugins/claude-hud/.usage-cache.json"
CACHE_TTL_SECONDS=120
THRESHOLD=85
DRY_RUN=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [--threshold N] [--dry-run] [--help]

Run /wiki-ingest-next-batch if Claude's 5-hour usage is below a threshold.
Reports usage before and after the run.

Options:
  --threshold N   Usage percentage ceiling (default: 85). Ingest runs only
                  when current usage is strictly below this value.
  --dry-run       Check usage and report what would happen, but do not run
                  the ingest.
  --help          Show this help and exit.

Data sources (in order of preference):
  1. Anthropic API  https://api.anthropic.com/api/oauth/usage  (OAuth token
                    read from macOS Keychain: "Claude Code-credentials")
  2. HUD cache      ~/.claude/plugins/claude-hud/.usage-cache.json
                    (used when API is unreachable; max age: ${CACHE_TTL_SECONDS}s)

Exit codes:
  0  Ingest ran (and completed).
  1  Usage data could not be retrieved.
  2  Halted — usage was at or above the threshold.
  3  No batch-import-*.txt files found — run wiki-ingest first.
EOF
}

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --threshold) THRESHOLD="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        --help|-h)   usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

# Fetch utilization % from Anthropic API using the keychain OAuth token.
# Prints an integer 0-100 on success, or returns non-zero on failure.
fetch_from_api() {
    local keychain_json
    keychain_json=$(/usr/bin/security find-generic-password \
        -s "Claude Code-credentials" -w 2>/dev/null) || return 1

    local access_token
    access_token=$(echo "$keychain_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
token = d.get('claudeAiOauth', {}).get('accessToken', '')
if not token:
    raise SystemExit(1)
print(token)
") || return 1

    local response
    response=$(curl -sf --max-time 8 \
        -H "Authorization: Bearer $access_token" \
        -H "anthropic-beta: oauth-2025-04-20" \
        "https://api.anthropic.com/api/oauth/usage") || return 1

    echo "$response" | python3 -c "
import json, sys
d = json.load(sys.stdin)
util = d.get('five_hour', {}).get('utilization')
if util is None:
    raise SystemExit(1)
print(round(max(0, min(100, float(util)))))
"
}

# Read utilization % from the HUD file cache if fresh enough.
# Prints an integer 0-100 on success, or returns non-zero if stale/missing.
read_from_cache() {
    [ -f "$HUD_CACHE" ] || return 1
    python3 -c "
import json, time
d = json.load(open('$HUD_CACHE'))
age_sec = (time.time() * 1000 - d['timestamp']) / 1000
if age_sec > $CACHE_TTL_SECONDS:
    raise SystemExit(1)
v = d.get('data', {}).get('fiveHour')
if v is None:
    raise SystemExit(1)
print(int(v))
"
}

# Resolve current 5-hour usage %, preferring a fresh API call.
get_usage() {
    local pct
    if pct=$(fetch_from_api 2>/dev/null); then
        echo "$pct"
        return 0
    fi
    echo "API unavailable, checking cache..." >&2
    if pct=$(read_from_cache 2>/dev/null); then
        echo "$pct"
        return 0
    fi
    return 1
}

main() {
    # Abort early if no batch import files exist (wiki-ingest has not been run yet)
    local import_dir="$PROJECT_DIR/.import"
    if ! compgen -G "$import_dir/batch-import-*.txt" > /dev/null 2>&1; then
        echo "Error: no batch-import-*.txt files found in $import_dir" >&2
        echo "Run the wiki-ingest skill first to generate the import batches." >&2
        exit 3
    fi

    echo "Checking Claude 5-hour usage (threshold: ${THRESHOLD}%)..."

    local usage_before
    if ! usage_before=$(get_usage); then
        echo "Error: could not retrieve usage data — skipping ingest" >&2
        exit 1
    fi

    echo "5-hour usage before: ${usage_before}%"

    if [ "$usage_before" -ge "$THRESHOLD" ]; then
        echo "At or above ${THRESHOLD}% — skipping ingest"
        exit 2
    fi

    if [ "$DRY_RUN" = true ]; then
        echo "Dry run: would start /wiki-ingest-next-batch in $PROJECT_DIR"
        exit 0
    fi

    echo "Below ${THRESHOLD}% — starting /wiki-ingest-next-batch in $PROJECT_DIR"
    cd "$PROJECT_DIR"
    claude --dangerously-skip-permissions -p "/wiki-ingest-next-batch"

    echo ""
    echo "Ingest complete. Fetching updated usage..."
    local usage_after
    if usage_after=$(get_usage 2>/dev/null); then
        local delta=$(( usage_after - usage_before ))
        local sign=""
        [ "$delta" -ge 0 ] && sign="+"
        echo "5-hour usage after:  ${usage_after}%  (${sign}${delta}%)"
    else
        echo "Could not retrieve post-run usage."
    fi
}

main
