---
name: wiki-finalize-ingest
description: Use when the user asks to finalize an ingest, merge batch logs, or rebuild wiki indexes after a batch import. Merges all session logs into wiki/log.jsonl, rebuilds _index.md files, reports stubs, and optionally runs QMD and lint. Warns if no logs or batches are found.
---

# Knowledge Base - Finalize Ingest

## Step 0 — Check state

Before doing anything, verify there is something to finalize:

```bash
ls .import/batch-log-*.jsonl 2>/dev/null
ls .import/batch-import-*.txt 2>/dev/null
```

- **No `batch-log-*.jsonl` files AND no `batch-import-*.txt` files**: nothing to finalize. Tell the user: "No batch import logs or files found. Nothing to finalize — run `wiki-ingest` to start a new import."
- **Unclaimed `batch-import-*.txt` files still exist** (not `.claimed.`): warn the user: "Some batches have not been processed yet. Make sure all `wiki-ingest-next-batch` sessions have finished before finalizing." Use `AskUserQuestion` to ask: "Proceed anyway (partial finalization) or abort?"
- **Only `batch-log-*.jsonl` files exist**: all batches are done — proceed to Step 1.

## Step 1 — Merge logs

Append all `.import/batch-log-*.jsonl` to `wiki/log.jsonl` (create `wiki/log.jsonl` if it doesn't exist). 
Then delete all `.import/batch-log-*.jsonl` and any remaining `.import/batch-import-*.txt`.

## Step 2 — Rebuild indexes

For every topic directory in `wiki/`:

- List all `.md` files in the directory (excluding `_index.md`).
- For each file: extract the title (first `#` heading, or filename without extension) and a 1-sentence summary (first non-heading, non-empty paragraph).
- Write a fresh `_index.md` using vault-relative wikilinks, sorted alphabetically: `- [[wiki/<topic>/Page Title]] — summary`.
- Preserve (or create if missing) the standard header structure:
  ```markdown
  ---
  type: index
  date: YYYY-MM-DD HH:mm:ss
  ---
  # <Type> - index
  [[wiki/index|← Index]]

  <One-sentence description of what this topic type covers.>
  ```

## Step 3 — Report stubs

Use this command to scan markdown files for stubs:
```bash
find wiki -name "*.md" -exec awk '/^---/{p++} p==1{print FILENAME": "$0} p==2{p=0; nextfile}' {} + | grep "stub:.*true"
```
If any exist, list them in a "Stubs still needing expansion" section so the user knows what gaps remain.

## Step 4 — Summarize

Present a table of all pages created/updated across all sessions (read from the just-merged session log data). 

## Step 5 — Post-processing menu

Use `AskUserQuestion` with `multiSelect: true`. Always run QMD before lint:

- **All (recommended)** — lint + QMD text + vector embedding; supersedes individual selections
- **Lint** — health check: orphans, contradictions, gaps
- **QMD text re-index** (`qmd update`) — fast, keywords only
- **QMD vector embedding** (`qmd update && qmd embed`) — slow, ~2 GB models; supersedes text-only if both selected

## Step 6 - End message

After running the lint check or QMD do not suggest to run finalize again. Do propose to run `scripts/lint-wiki-pages.py --interactive` if any problems were found during the lint check.
