#!/usr/bin/env bash
# Removes all QMD collections and wipes the index database.
set -euo pipefail

QMD_DB_DIR="${HOME}/.cache/qmd"

echo "This will remove all QMD collections and delete the index database at:"
echo "  ${QMD_DB_DIR}/index.sqlite"
echo ""
read -r -p "Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

echo ""
echo "=== Removing collections ==="
collections=$(qmd collection list 2>/dev/null | awk '/^[^ ]/ && NR>1 {print $1}')

if [[ -z "$collections" ]]; then
  echo "  No collections registered."
else
  while IFS= read -r name; do
    echo "  [remove] $name"
    qmd collection remove "$name"
  done <<< "$collections"
fi

echo ""
echo "=== Deleting index database ==="
for f in "${QMD_DB_DIR}/index.sqlite" \
          "${QMD_DB_DIR}/index.sqlite-shm" \
          "${QMD_DB_DIR}/index.sqlite-wal"; do
  if [[ -f "$f" ]]; then
    rm "$f"
    echo "  deleted: $f"
  fi
done

echo ""
echo "Done. Run scripts/qmd-sync-collections.sh to re-register collections."
