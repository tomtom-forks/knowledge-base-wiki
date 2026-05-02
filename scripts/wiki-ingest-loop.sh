#!/usr/bin/env bash
# Autonomous wiki ingestion pipeline:
#   1. If no batch-import files exist, run /wiki-ingest first.
#   2. Loop /wiki-ingest-next-batch until all batches are consumed.
#   3. Run /wiki-finalize-ingest to wrap up.
#
# Pauses 30 minutes whenever the 5-hour Claude usage is at or above the
# threshold, then retries automatically. Usage tracking is Claude-only;
# for --agent junie or --agent vibe, get_usage always returns 0 so the
# throttling loop is effectively disabled.
#
# The 5-hour usage percentage is fetched from the Anthropic API using the
# OAuth token stored in the macOS Keychain (Claude Code-credentials).
# Falls back to the HUD usage cache (~/.claude/plugins/claude-hud/.usage-cache.json)
# if the API is unreachable.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HUD_CACHE="$HOME/.claude/plugins/claude-hud/.usage-cache.json"
CACHE_TTL_SECONDS=120
THRESHOLD=80
WAIT_SECS=1800
MAX_ERRORS=5
MAX_LOOPS=25
ERROR_COUNT=0
LOOP_COUNT=0
AGENT=claude

usage() {
    cat <<EOF
Usage: $(basename "$0") [--agent AGENT] [--threshold N] [--max-errors N] [--max-loops N] [--help]

Autonomous wiki ingestion pipeline. Runs /wiki-ingest (if needed), then loops
/wiki-ingest-next-batch until all batches are done, then finalizes. Pauses
30 minutes whenever the 5-hour Claude usage is at or above the threshold.
Throttling applies only to --agent claude; for junie and vibe, usage is
reported as 0% (no throttling) since those agents don't expose a quota API.

Options:
  --agent AGENT   LLM agent command to use (default: claude).
                  Allowed values: claude (Anthropic Claude),
                                  junie  (JetBrains Junie),
                                  vibe   (Mistral Vibe).
  --threshold N   Usage percentage ceiling (default: 80). Each phase starts only
                  when current usage is strictly below this value.
  --max-errors N  Maximum number of LLM agent command errors before the script
                  exits (default: 5). Each error pauses for confirmation first.
  --max-loops N   Maximum number of batch loops to run (default: 25). The
                  script exits cleanly after this many iterations.
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
        --agent)
            case "$2" in
                claude|junie|vibe) AGENT="$2" ;;
                *) echo "Unknown agent: $2 (allowed: claude, junie, vibe)" >&2; usage >&2; exit 1 ;;
            esac
            shift 2 ;;
        --threshold)  THRESHOLD="$2";  shift 2 ;;
        --max-errors) MAX_ERRORS="$2"; shift 2 ;;
        --max-loops)  MAX_LOOPS="$2";  shift 2 ;;
        --help|-h)    usage; exit 0 ;;
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
# Junie and Vibe don't expose a comparable usage endpoint, so we report 0%
# for them — effectively disabling throttling for those agents.
get_usage() {
    if [ "$AGENT" != "claude" ]; then
        echo 0
        return 0
    fi
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

# Wait up to TIMEOUT seconds, allowing ESC to cancel the loop.
# Returns 0 to continue, 1 if the user pressed ESC.
wait_with_cancel() {
    local timeout="$1"

    [ -t 0 ] || { sleep "$timeout"; return 0; }

    local saved_tty
    saved_tty=$(stty -g 2>/dev/null) || { sleep "$timeout"; return 0; }
    stty -echo 2>/dev/null

    printf "\n  Pausing %ds before next batch — press ESC to stop the loop.\n" "$timeout"

    local elapsed=0
    local cancelled=false

    while [ "$elapsed" -lt "$timeout" ]; do
        local remaining=$(( timeout - elapsed ))
        printf "\r  Continuing in %2ds  (ESC = stop)" "$remaining"

        local ch
        IFS= read -r -s -n 1 -t 1 ch 2>/dev/null || true

        if [[ "$ch" == $'\x1b' ]]; then
            cancelled=true
            break
        fi

        elapsed=$(( elapsed + 1 ))
    done

    stty "$saved_tty" 2>/dev/null
    printf "\r%60s\r\n" ""

    if [ "$cancelled" = true ]; then
        echo "  Loop stopped by user (ESC)."
        return 1
    fi
    return 0
}

# Count unclaimed batch-import-*.txt files.
count_batch_files() {
    local -a files
    shopt -s nullglob
    files=("$PROJECT_DIR/.import"/batch-import-*.txt)
    shopt -u nullglob
    echo "${#files[@]}"
}

# Invoke the selected LLM agent with a slash-command prompt.
# Usage: run_llm "<slash-command>"
run_llm() {
    local prompt="$1"
    case "$AGENT" in
        claude) claude --dangerously-skip-permissions --output-format text -p "$prompt" ;;
        junie)  junie -p "$prompt" ;;
        vibe)   vibe -p "$prompt" ;;
    esac
}

show_plan() {
    local needs_ingest="$1"
    local batch_count="$2"

    echo ""
    echo "=== Wiki Ingest Pipeline ==="
    echo ""
    printf "LLM agent: %s\n" "$AGENT"
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
    printf "Max loops: %s  |  Max errors: %s\n" "$MAX_LOOPS" "$MAX_ERRORS"
    echo ""
}

# Prompt Y/n with Enter=Yes, Escape=No.
# Auto-advances to Yes after 60 seconds, showing a live countdown.
# Returns 0 to continue, 1 if the user declined.
confirm_yn() {
    local prompt="${1:-Continue?}"
    local timeout=60

    [ -t 0 ] || return 0   # non-interactive: default Yes

    local saved_tty
    saved_tty=$(stty -g 2>/dev/null) || { return 0; }
    stty -echo -icanon min 1 time 0 2>/dev/null

    local elapsed=0 result=0 decided=false

    while [ "$elapsed" -le "$timeout" ]; do
        local remaining=$(( timeout - elapsed ))
        printf "\r%s [Y/n] (continuing in %2ds) " "$prompt" "$remaining"

        local ch
        if IFS= read -r -s -n 1 -t 1 ch 2>/dev/null; then
            # A key was pressed
            case "$ch" in
                ''|$'\n'|$'\r'|y|Y)   # Enter or Y → Yes
                    printf "\r%s [Y/n] Yes%-30s\n" "$prompt" ""
                    result=0; decided=true
                    break
                    ;;
                n|N)
                    printf "\r%s [Y/n] No%-30s\n" "$prompt" ""
                    result=1; decided=true
                    break
                    ;;
                $'\x1b')              # Escape → No
                    printf "\r%s [Y/n] No%-30s\n" "$prompt" ""
                    result=1; decided=true
                    break
                    ;;
            esac
        fi
        # Timed out waiting for a key (or unrecognised key) — advance counter
        elapsed=$(( elapsed + 1 ))
    done

    if [ "$decided" = false ]; then
        printf "\r%s [Y/n] Yes (auto-advanced after %ds)%-10s\n" "$prompt" "$timeout" ""
        result=0
    fi

    stty "$saved_tty" 2>/dev/null
    return "$result"
}

confirm_or_exit() {
    printf "Agent: %s\n" "$AGENT"
    if ! confirm_yn "Start the wiki ingest pipeline?"; then
        echo "Stopped."
        exit 0
    fi
}

# Prompt to continue after an error; also enforces MAX_ERRORS limit.
confirm_after_error() {
    local context="$1"
    ERROR_COUNT=$(( ERROR_COUNT + 1 ))
    echo "  [Error $ERROR_COUNT of $MAX_ERRORS allowed]"
    if [ "$ERROR_COUNT" -ge "$MAX_ERRORS" ]; then
        echo ""
        echo "ERROR: Maximum error count ($MAX_ERRORS) reached after: $context" >&2
        echo "Exiting. Inspect $PROJECT_DIR/.import/ for current state." >&2
        exit 1
    fi
    if ! confirm_yn "Error in $context — continue anyway?"; then
        echo "Stopped by user after error."
        exit 1
    fi
}

run_phase_ingest() {
    echo "=== Phase 1: running /wiki-ingest ==="
    wait_for_capacity "before /wiki-ingest" > /dev/null
    echo "Starting /wiki-ingest..."
    if ! run_llm "/wiki-ingest"; then
        echo "ERROR: /wiki-ingest exited with an error.  Time: $(date '+%H:%M:%S')" >&2
        confirm_after_error "/wiki-ingest"
    fi
    echo ""
    echo "/wiki-ingest complete.  Time: $(date '+%H:%M:%S')"
}

run_phase_batches() {
    local total="$1"
    local iteration=0

    while compgen -G "$PROJECT_DIR/.import/batch-import-*.txt" > /dev/null 2>&1; do
        iteration=$(( iteration + 1 ))
        LOOP_COUNT=$(( LOOP_COUNT + 1 ))

        if [ "$LOOP_COUNT" -gt "$MAX_LOOPS" ]; then
            echo ""
            echo "INFO: Maximum loop count ($MAX_LOOPS) reached after $iteration batch iteration(s)."
            echo "Exiting cleanly. Remaining batches can be processed by re-running the script."
            return 0
        fi

        local remaining
        remaining=$(count_batch_files)
        echo ""
        echo "=== Phase 2 — batch $iteration of $total  ($remaining remaining, loop $LOOP_COUNT/$MAX_LOOPS) ==="

        local usage_before
        usage_before=$(wait_for_capacity "before batch $iteration of $total")

        echo "Starting /wiki-ingest-next-batch..."
        if ! run_llm "/wiki-ingest-next-batch"; then
            echo "ERROR: /wiki-ingest-next-batch failed on batch $iteration.  Time: $(date '+%H:%M:%S')" >&2
            echo "Check $PROJECT_DIR/.import/ for current state." >&2
            confirm_after_error "/wiki-ingest-next-batch (batch $iteration)"
        fi

        echo ""
        local usage_after
        if usage_after=$(get_usage 2>/dev/null); then
            local delta=$(( usage_after - usage_before ))
            local sign=""; [ "$delta" -ge 0 ] && sign="+"
            echo "Completed batch $iteration of $total.  5-hour usage: ${usage_after}%  (${sign}${delta}%)  Time: $(date '+%H:%M:%S')"
        else
            echo "Completed batch $iteration of $total.  Time: $(date '+%H:%M:%S')"
        fi

        if ! wait_with_cancel 30; then
            break
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
    if ! run_llm "/wiki-finalize-ingest"; then
        echo "ERROR: /wiki-finalize-ingest exited with an error.  Time: $(date '+%H:%M:%S')" >&2
        confirm_after_error "/wiki-finalize-ingest"
    fi
    echo ""
    echo "Pipeline complete.  Time: $(date '+%H:%M:%S')"
}

main() {
    cd "$PROJECT_DIR"

    local needs_ingest=false
    local batch_count
    batch_count=$(count_batch_files)

    if [ "$batch_count" -eq 0 ]; then
        needs_ingest=true
    fi

    echo "Start time: $(date '+%H:%M:%S')"
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
