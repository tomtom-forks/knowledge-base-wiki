---
name: wiki-ingest
description: Use when the user asks to ingest, import, or process notes; mentions a raw note file path; provides a Confluence URL or page title; or says "process new files". Covers standard ingestion, Confluence MCP fetching, and bulk batch processing.
---

# Knowledge Base - Ingest

## Per-note ingestion (reference — applied within sessions)

For each markdown file:

- The top-level wiki topic list is: competition, concepts, decisions, people, problems, projects, systems.
- **Only use topics from that list.** Never create a `wiki/<dir>/` that is not one of those topics — not "systems", not "architecture", not anything else.
- Identify relevance to each wiki topic. For relevant topics: create a new page or update an existing one.
  - Always create pages at exactly one level deep: `wiki/<topic>/<page>.md` — never deeper (e.g. `wiki/concepts/NavSDK.md`, not `wiki/concepts/Navigation/NavSDK.md`).
  - Never delete or overwrite hand-curated content; expand and add instead.
  - For people: only create pages for confirmed employees, or people mentioned in multiple different sources. Require both first and last name. Ignore titles ("Dr.", "PhD.", "MD.") when parsing names — "John Smith, Dr." is one person named John Smith.
  - If ingestion leads to contradictions on a page, clearly mark the contradiction with a short explanation and add frontmatter tag `requires-attention`.
  - Cross-reference related pages using `[[wikilinks]]`.
  - **Wikilink rule:** Only wikilink to a page that (a) already exists in `wiki/`, or (b) you are creating/have created in this same session. If you identify a topic worth referencing but cannot fully describe it yet, create a minimal stub: frontmatter with `type` and `stub: true`, a `# Title` heading, and one italic line noting the source file. Stubs count as `pages_created` in the session log.
  - **Stub expansion rule:** Before creating a new page, check if a stub already exists at that path (frontmatter contains `stub: true`). If so, expand it into a full page — remove `stub: true`, fill in proper content, and count it as `pages_updated` (not `pages_created`) in the session log.
- Do NOT update `wiki/<topic>/_index.md` during a session (deferred to finalization).
- Append one log entry to the session log `raw/_batch-log-N.jsonl` (one JSON object per line):
```json
{"date":"YYYY-MM-DD HH:mm:ss","session":N,"file":"raw/notes/filename.md","summary":"One-sentence description.","pages_created":["wiki/concepts/NavSDK.md"],"pages_updated":["wiki/people/Jane Smith.md"]}
```

Conversions before ingestion:

- **Images/PDFs** in `raw/scans/`: convert using `mcp__claude_ai_Atlassian__fetch` or similar. Save converted markdown to `raw/scans/converted/` with frontmatter: `source` (link to original), `date` (source timestamp), `converted` (today). Ingest only `.md` files.
- **`.vtt` transcripts** in `raw/transcripts/`: run `python3 scripts/convert-vtt-to-md.py --new --dir raw/transcripts --output-dir raw/transcripts/converted`. Ingest only `.md` files.
- **`.eml` emails** in `raw/emails/`: run `python3 scripts/convert-eml-to-md.py --new --dir raw/emails --output-dir raw/emails/converted`. Ingest only `.md` files.
- Attachments linked from source files (e.g. in `_resources/` directories) are also included.

## Session 1 — coordinator

When asked to "ingest new raw notes" (or similar):

1. **Partition** (run automatically): `bash scripts/create-import-batches.sh`
   - Default max batch size is 50 files. Override with `--max-size N` (e.g. `--max-size 20`).
   - This removes any old `raw/_batch-import-*.txt` remnants and creates fresh ones.
   - If output says "Nothing to ingest", report that and stop.
   - **If the script exits with code 2**: a previous ingest was not completed. Use `AskUserQuestion` to ask the user what to do, with these options:
     - **"Ingest next batch"** — stop here and execute the "ingest next batch" flow (Sessions 2–N above) instead; do NOT re-run `create-import-batches.sh`.
     - **"Forget previous ingestion and import new notes"** — re-run `bash scripts/create-import-batches.sh --force` to wipe old batches, then continue with this flow from step 2.
     - **"Abort"** — stop immediately and do nothing.
   - Check the exit code explicitly after running the script: `bash scripts/create-import-batches.sh; echo "EXIT:$?"`  and look for `EXIT:2`.
2. **Check how many batches have content**: count non-empty `raw/_batch-import-*.txt` files (the script prints the count).
   - **If only 1 batch has content**: process it (step 3) and immediately proceed to Finalization — do NOT ask the user to open more sessions.
   - **If 2+ batches have content**: instruct the user — "Batches ready. Open N more Claude Code sessions. In each one say: `ingest next batch` (or `/wiki:ingest-next-batch`). I'll start batch 1 now. When all sessions are done, say `finalize ingest` (or `/wiki:finalize-ingest`) here." — then proceed to step 3.
3. **Process batch 1**: first claim it atomically:
   ```bash
   mv raw/_batch-import-1.txt raw/_batch-import-1.claimed.txt
   ```
   Then read `raw/_batch-import-1.claimed.txt`. For each file listed, apply per-note ingestion above. Use sub-agents and process in batches of 10 to conserve context. After finishing all files, delete `raw/_batch-import-1.claimed.txt`.
4. **If single-batch**: proceed directly to Finalization. **If multi-batch**: report notes processed/pages created/updated, then await "finalize ingest".

## Sessions 2–N

When asked to "ingest next batch":

1. **Claim a batch atomically**: run the following to find and claim the next unclaimed batch:
   ```bash
   for f in $(ls raw/_batch-import-[0-9]*.txt 2>/dev/null | grep -v '\.claimed\.' | sort -V); do
     mv "$f" "${f%.txt}.claimed.txt" 2>/dev/null && echo "${f%.txt}.claimed.txt" && break
   done
   ```
   - If the loop prints a filename (e.g. `raw/_batch-import-2.claimed.txt`) → that is your batch; proceed.
   - If nothing is printed → all batches are taken or already done; report "No unclaimed batch found" and stop.
2. For each file listed in the claimed file, apply per-note ingestion above. Use sub-agents and process in batches of 10.
3. After finishing all files, delete the `.claimed.txt` file.
4. Report: batch number claimed, number of notes processed, pages created/updated.

## Finalization (coordinator)

When asked to "finalize ingest":

1. **Merge logs**: append all `raw/_batch-log-*.jsonl` to `wiki/log.jsonl` (create `wiki/log.jsonl` if it doesn't exist). Then delete all `raw/_batch-log-*.jsonl` and any remaining `raw/_batch-import-*.txt`.
2. **Rebuild indexes**: for every topic directory in `wiki/`:
   - List all `.md` files in the directory (excluding `_index.md`).
   - For each file: extract the title (first `#` heading, or filename without extension) and a 1-sentence summary (first non-heading, non-empty paragraph).
   - Write a fresh `_index.md` using vault-relative wikilinks, sorted alphabetically: `- [[wiki/<topic>/Page Title]] — summary`.
   - Preserve (or create if missing) the standard header structure from the templates skill:
     ```markdown
     ---
     type: index
     date: YYYY-MM-DD HH:mm:ss
     ---
     # <Type> - index
     [[wiki/index|← Index]]

     <One-sentence description of what this topic type covers.>
     ```
3. **Report stubs**: scan all `wiki/**/*.md` for files with `stub: true` in frontmatter. If any exist, list them in a "Stubs still needing expansion" section so the user knows what gaps remain.
4. **Summarize**: present a table of all pages created/updated across all sessions (read from the just-merged session log data).
4. **Post-processing menu** (`AskUserQuestion` with `multiSelect: true`). Always run QMD before lint:
   - **All (recommended)** — QMD text + vector embedding + lint; supersedes individual selections
   - **QMD text re-index** (`qmd update`) — fast, keywords only
   - **QMD vector embedding** (`qmd update && qmd embed`) — slow, ~2 GB models; supersedes text-only if both selected
   - **Lint** — health check: orphans, contradictions, gaps

## Confluence ingestion

Triggered by a Confluence URL or page title:

- Fetch via `mcp__claude_ai_Atlassian__fetch`
- Save to `raw/confluence/<Page Title>.md` with frontmatter:
```yaml
---
source_url: <url>
fetched: YYYY-MM-DD HH:mm:ss
---
```
- Continue with per-note ingestion for that file (as a single-file session — write to `raw/_batch-log-1.jsonl`, then immediately finalize).

**Refresh:** "refresh this Confluence page" → re-fetch, overwrite cache, diff vs previous, flag changes affecting existing wiki pages.

## wiki/log.jsonl format

`wiki/log.jsonl` is append-only. One JSON object per line, sorted oldest-to-newest by append order.

```jsonl
{"date":"YYYY-MM-DD HH:mm:ss","session":1,"file":"raw/notes/meeting-2026-03-01.md","summary":"Quarterly planning meeting notes.","pages_created":["wiki/decisions/adopt-vector-tiles.md","wiki/projects/AutoStream.md"],"pages_updated":["wiki/people/Jane Smith.md"]}
```

Finding un-ingested notes: `jq -r '.file' wiki/log.jsonl` — lists all ingested paths.  
Fallback without jq: `grep -oP '"file":"\K[^"]+' wiki/log.jsonl`

## Notes

- A single note may touch 5–25+ wiki pages. That is expected and desirable.
- Never re-ingest a file already present in `wiki/log.jsonl` without explicit user confirmation.
- Parallel sessions writing to the same wiki page is safe: the second session reads the already-updated page and extends it further.
