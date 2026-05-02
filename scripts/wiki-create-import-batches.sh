#!/usr/bin/env bash
# Partitions un-ingested notes into batch files for parallel import sessions.
#
# Usage:
#   bash scripts/wiki-create-import-batches.sh [--max-size N] [--force] [--help]
#
# Options:
#   --max-size N   Maximum number of files per batch (default: 50)
#   --force        Remove existing batch/log files before running
#   --help         Print this help and exit
#
# Output files:
#   .import/batch-import-1.txt, .import/batch-import-2.txt, …
#   Each file contains one file path per line.
#
# Exit codes:
#   0  Success (including "nothing to ingest")
#   1  Invalid argument
#   2  Existing batch or log files found (use --force to override)
#
# Machine-readable summary line (always last):
#   RESULT: total=<N> batches=<N> max_size=<N> status=<ready|empty>
set -euo pipefail

usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
    exit 0
}

MAX_SIZE=50
FORCE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-size) MAX_SIZE="$2"; shift 2 ;;
        --force)    FORCE=true; shift ;;
        --help|-h)  usage ;;
        *) echo "ERROR: Unknown argument: $1" >&2; echo "Run with --help for usage." >&2; exit 1 ;;
    esac
done

NOTES_DIR="raw"
LOG="wiki/log.jsonl"
IMPORT_DIR=".import"

existing_batches=( $IMPORT_DIR/batch-import-*.txt )
existing_logs=( $IMPORT_DIR/batch-log-*.jsonl )
has_batches=false
has_logs=false
[[ -e "${existing_batches[0]}" ]] && has_batches=true
[[ -e "${existing_logs[0]}" ]]   && has_logs=true

if $has_batches || $has_logs; then
    if ! $FORCE; then
        echo "ERROR: Existing files found:" >&2
        $has_batches && printf '  %s\n' "${existing_batches[@]}" >&2
        $has_logs    && printf '  %s\n' "${existing_logs[@]}"    >&2
        echo "" >&2
        echo "Execute /wiki:ingest-next-batch to continue processing and" >&2
        echo "execute /wiki:finalize-ingest when all batches are done." >&2
        echo "Or use the option --force to remove the files and continue." >&2
        exit 2
    fi
    $has_batches && rm -f "${existing_batches[@]}"
    $has_logs    && rm -f "${existing_logs[@]}"
fi

ingested=$(grep -hoP '"file":"\K[^"]+' "$LOG" $IMPORT_DIR/batch-log-*.jsonl 2>/dev/null | sort || true)

remaining=()
while IFS= read -r line; do
    remaining+=("$line")
done < <(comm -23 \
    <(find "$NOTES_DIR" \( -name "*.md" -o -name "*.doc" -o -name "*.docx" -o -name "*.txt" -o -name "*.vtt" -o -name "*.eml" \) | sort) \
    <(echo "$ingested"))

total=${#remaining[@]}
num_batches=$(( (total + MAX_SIZE - 1) / MAX_SIZE ))
[[ $total -eq 0 ]] && num_batches=0

echo "Un-ingested notes : $total"
echo "Max files/batch   : $MAX_SIZE"
echo "Batches to create : $num_batches"

if [[ $total -eq 0 ]]; then
    echo "Nothing to ingest."
    echo "RESULT: total=0 batches=0 max_size=$MAX_SIZE status=empty"
    exit 0
fi

mkdir -p "$IMPORT_DIR"

for idx in "${!remaining[@]}"; do
    batch=$(( idx / MAX_SIZE + 1 ))
    echo "${remaining[$idx]}" >> "$IMPORT_DIR/batch-import-$batch.txt"
done

echo ""
echo "Batch breakdown:"
for ((i=1; i<=num_batches; i++)); do
    count=$(grep -c . "$IMPORT_DIR/batch-import-$i.txt" 2>/dev/null || echo 0)
    echo "  $IMPORT_DIR/batch-import-$i.txt : $count files"
done

echo ""
echo "RESULT: total=$total batches=$num_batches max_size=$MAX_SIZE status=ready"
