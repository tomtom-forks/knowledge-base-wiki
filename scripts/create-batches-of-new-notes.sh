#!/usr/bin/env bash
# Usage: bash scripts/create-batches-of-new-notes.sh [N=5]
set -euo pipefail

N=${1:-5}
NOTES_DIR="raw/notes"
LOG="wiki/log.jsonl"

rm -f raw/import-batch-*.txt

ingested=$(grep -hoP '"file":"\K[^"]+' "$LOG" raw/.session-*.jsonl 2>/dev/null | sort || true)

mapfile -t remaining < <(comm -23 \
    <(find "$NOTES_DIR" -name "*.md" | sort) \
    <(echo "$ingested"))

total=${#remaining[@]}
echo "Found $total un-ingested notes → splitting into $N batches"

if [[ $total -eq 0 ]]; then
    echo "Nothing to ingest."
    exit 0
fi

for idx in "${!remaining[@]}"; do
    batch=$(( (idx % N) + 1 ))
    echo "${remaining[$idx]}" >> "raw/import-batch-$batch.txt"
done

for ((i=1; i<=N; i++)); do
    count=$(grep -c . "raw/import-batch-$i.txt" 2>/dev/null || echo 0)
    echo "  import-batch-$i: $count notes"
done
