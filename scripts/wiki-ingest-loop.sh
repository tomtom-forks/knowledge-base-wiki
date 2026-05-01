#!/usr/bin/env bash
# Autonomous wiki ingestion pipeline:
#   1. If no batch-import files exist, run /wiki-ingest first.
#   2. Loop /wiki-ingest-next-batch until all batches are consumed.
#   3. Run /wiki-finalize-ingest to wrap up.
#
# Pauses 30 minutes whenever the 5-hour Claude usage is at or above the
# threshold, then retries automatically.
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
WAIT_SECS=1800

usage() {
    cat <<EOF
Usage: $(basename "$0") [--threshold N] [--help]

Autonomous wiki ingestion pipeline. Runs /wiki-ingest (if needed), then loops
/wiki-ingest-next-batch until all batches are done, then finalizes. Pauses
30 minutes whenever Claude's 5-hour usage is at or above the threshold.

Options:
  --threshold N   Usage percentage ceiling (default: 85). Each phase starts only
                  when current usage is strictly below this value.
  --help          Show this help and exit.

Data sources (in order of preference):
  1. Anthropic API  https://api.anthropic.com/api/oauth/usage  (OAuth token
                    read from macOS Keychain: "Claude Code-credentials")
  2. HUD cache      ~/.claude/plugins/claude-hud/.usage-cache.json
                    (used when API is unreachable; max age: ${CACHE_TTL_SECONDS}s)

Exit codes:
  0  Full pipeline complete (ingest → batches → finalize).
  1  Interrupted or unexpected error.
EOF
}

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --threshold) THRESHOLD="$2"; shift 2 ;;
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
    HUD_CACHE_PATH="$HUD_CACHE" python3 -c "
import json, time, os
d = json.load(open(os.environ['HUD_CACHE_PATH']))
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

# Print the clock time WAIT_SECS from now.
next_attempt_time() {
    date -v +${WAIT_SECS}S '+%H:%M' 2>/dev/null \
        || date -d "+${WAIT_SECS} seconds" '+%H:%M' 2>/dev/null \
        || echo "in 30 minutes"
}

# Block until 5-hour usage is below THRESHOLD.
# Prints the usage percentage to stdout once cleared; all other output to stderr.
wait_for_capacity() {
    local context="$1"
    while true; do
        local pct
        if ! pct=$(get_usage); then
            echo "Could not retrieve usage data ($context) — retrying at $(next_attempt_time)..." >&2
            sleep "$WAIT_SECS"
            continue
        fi
        echo "5-hour usage: ${pct}%  (threshold: ${THRESHOLD}%)" >&2
        if [ "$pct" -lt "$THRESHOLD" ]; then
            echo "$pct"
            return 0
        fi
        echo "At or above ${THRESHOLD}% — waiting 30 minutes ($context). Next attempt at $(next_attempt_time)." >&2
        sleep "$WAIT_SECS"
    done
}

# Count unclaimed batch-import-*.txt files.
count_batch_files() {
    local -a files
    shopt -s nullglob
    files=("$PROJECT_DIR/.import"/batch-import-*.txt)
    shopt -u nullglob
    echo "${#files[@]}"
}

show_plan() {
    local needs_ingest="$1"
    local batch_count="$2"

    echo ""
    echo "=== Wiki Ingest Pipeline ==="
    echo ""

    if [ "$needs_ingest" = true ]; then
        echo "  Phase 1  /wiki-ingest              (no batch files — will create batches)"
        echo "  Phase 2  /wiki-ingest-next-batch   (batch count determined after phase 1)"
    else
        echo "  Phase 1  /wiki-ingest              (skipped — ${batch_count} batch file(s) already exist)"
        echo "  Phase 2  /wiki-ingest-next-batch   ${batch_count} batch(es)"
    fi

    echo "  Phase 3  /wiki-finalize-ingest"
    echo ""
    printf "Pauses 30 min if 5-hour usage ≥ %s%%.\n" "$THRESHOLD"
    echo ""
}

confirm_or_exit() {
    if [ ! -t 0 ]; then
        echo "Error: stdin is not a terminal — cannot prompt for confirmation." >&2
        exit 1
    fi
    local answer
    read -r -p "Proceed? [y/N] " answer
    echo ""
    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) echo "Aborted."; exit 0 ;;
    esac
}

run_phase_ingest() {
    echo "=== Phase 1: running /wiki-ingest ==="
    wait_for_capacity "before /wiki-ingest" > /dev/null
    echo "Starting /wiki-ingest..."
    if ! claude --dangerously-skip-permissions -p "/wiki-ingest"; then
        echo "ERROR: /wiki-ingest exited with an error." >&2
        exit 1
    fi
    echo ""
    echo "/wiki-ingest complete."
}

run_phase_batches() {
    local total="$1"
    local iteration=0

    while compgen -G "$PROJECT_DIR/.import/batch-import-*.txt" > /dev/null 2>&1; do
        iteration=$(( iteration + 1 ))
        local remaining
        remaining=$(count_batch_files)
        echo ""
        echo "=== Phase 2 — batch $iteration of $total  ($remaining remaining) ==="

        local usage_before
        usage_before=$(wait_for_capacity "before batch $iteration of $total")

        echo "Starting /wiki-ingest-next-batch..."
        if ! claude --dangerously-skip-permissions -p "/wiki-ingest-next-batch"; then
            echo "ERROR: /wiki-ingest-next-batch failed on batch $iteration." >&2
            echo "Check $PROJECT_DIR/.import/ for current state." >&2
            exit 1
        fi

        echo ""
        local usage_after
        if usage_after=$(get_usage 2>/dev/null); then
            local delta=$(( usage_after - usage_before ))
            local sign=""; [ "$delta" -ge 0 ] && sign="+"
            echo "Completed batch $iteration of $total.  5-hour usage: ${usage_after}%  (${sign}${delta}%)"
        else
            echo "Completed batch $iteration of $total."
        fi
    done

    echo ""
    echo "All $iteration batch(es) consumed."
}

run_phase_finalize() {
    echo ""
    echo "=== Phase 3: finalizing ==="
    wait_for_capacity "before /wiki-finalize-ingest" > /dev/null
    echo "Starting /wiki-finalize-ingest..."
    if ! claude --dangerously-skip-permissions -p "/wiki-finalize-ingest"; then
        echo "ERROR: /wiki-finalize-ingest exited with an error." >&2
        exit 1
    fi
    echo ""
    echo "Pipeline complete."
}

main() {
    cd "$PROJECT_DIR"

    local needs_ingest=false
    local batch_count
    batch_count=$(count_batch_files)

    if [ "$batch_count" -eq 0 ]; then
        needs_ingest=true
    fi

    show_plan "$needs_ingest" "$batch_count"
    confirm_or_exit

    if [ "$needs_ingest" = true ]; then
        run_phase_ingest
        batch_count=$(count_batch_files)
        echo "Phase 1 created $batch_count batch file(s)."
        if [ "$batch_count" -eq 0 ]; then
            echo "ERROR: /wiki-ingest completed but created no batch files. Nothing to process." >&2
            exit 1
        fi
    fi

    run_phase_batches "$batch_count"

    run_phase_finalize
}

main
