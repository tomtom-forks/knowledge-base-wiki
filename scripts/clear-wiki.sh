#!/usr/bin/env bash
set -euo pipefail

FORCE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--force] [--help]

Clears all content from wiki/ and .import/, preserving .gitkeep files.

Options:
  --force   Skip confirmation prompt
  --help    Show this help message
EOF
}

for arg in "$@"; do
  case "$arg" in
    --help) usage; exit 0 ;;
    --force) FORCE=true ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$FORCE" != true ]]; then
  printf "This will delete all files in wiki/ and .import/ (keeping .gitkeep). Continue? [y/N] "
  read -r -n 1 answer
  echo
  if [[ "$answer" != "y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

find "$ROOT/wiki" -mindepth 1 ! -name ".gitkeep" -delete
find "$ROOT/.import" -mindepth 1 ! -name ".gitkeep" -delete

echo "Done. wiki/ and .import/ cleared."
echo "Run prompt '/wiki-ingest' in Claude to recreate the wiki."
