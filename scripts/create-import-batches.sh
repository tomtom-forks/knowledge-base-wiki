#!/usr/bin/env bash
# Partitions un-ingested notes into batch files for parallel import sessions.
#
# Usage:
#   bash scripts/create-import-batches.sh [--max-size N] [--help]
#
# Options:
#   --max-size N   Maximum number of files per batch (default: 50)
#   --help         Print this help and exit
#
# Output files:
#   raw/_import-batch-1.txt, raw/_import-batch-2.txt, …
#   Each file contains one file path per line.
#
# Exit codes:
#   0  Success (including "nothing to ingest")
#   1  Invalid argument
#
# Machine-readable summary line (always last):
#   RESULT: total=<N> batches=<N> max_size=<N> status=<ready|empty>
set -euo pipefail

usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
    exit 0
}

MAX_SIZE=50
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-size) MAX_SIZE="$2"; shift 2 ;;
        --help|-h)  usage ;;
        *) echo "ERROR: Unknown argument: $1" >&2; echo "Run with --help for usage." >&2; exit 1 ;;
    esac
done

NOTES_DIR="raw/notes"
LOG="wiki/log.jsonl"

rm -f raw/_import-batch-*.txt

ingested=$(grep -hoP '"file":"\K[^"]+' "$LOG" raw/.session-*.jsonl 2>/dev/null | sort || true)

remaining=()
while IFS= read -r line; do
    remaining+=("$line")
done < <(comm -23 \
    <(find "$NOTES_DIR" -name "*.md" | sort) \
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

for idx in "${!remaining[@]}"; do
    batch=$(( idx / MAX_SIZE + 1 ))
    echo "${remaining[$idx]}" >> "raw/_import-batch-$batch.txt"
done

echo ""
echo "Batch breakdown:"
for ((i=1; i<=num_batches; i++)); do
    count=$(grep -c . "raw/_import-batch-$i.txt" 2>/dev/null || echo 0)
    echo "  raw/_import-batch-$i.txt : $count files"
done

echo ""
echo "RESULT: total=$total batches=$num_batches max_size=$MAX_SIZE status=ready"
