---
name: wiki-ingest
description: Use when the user asks to ingest, import, or process notes; mentions a raw note file path; provides a Confluence URL or page title; or says "process new files". Covers standard ingestion, Confluence MCP fetching, and starting a bulk batch import.
---

# Knowledge Base - Ingest

## Session 1 — coordinator

When asked to "ingest new raw notes" (or similar):

1. **Partition** (run automatically): `bash scripts/create-import-batches.sh`
   - Default max batch size is 50 files. Override with `--max-size N` (e.g. `--max-size 20`).
   - This removes any old `.import/batch-import-*.txt` remnants and creates fresh ones.
   - If output says "Nothing to ingest", report that and stop.
   - **If the script exits with code 2**: a previous ingest was not completed. Use `AskUserQuestion` to ask the user what to do, with these options:
     - **"Ingest next batch"** — stop here and tell the user: "Use `wiki-ingest-next-batch` (or say `ingest next batch`) in a new session to continue."; do NOT re-run `create-import-batches.sh`.
     - **"Abort previous ingestion and restart importing new notes"** — re-run `bash scripts/create-import-batches.sh --force` to wipe old batches, then continue with this flow from step 2.
     - **"Abort"** — stop immediately and do nothing.
   - Check the exit code explicitly after running the script: `bash scripts/create-import-batches.sh; echo "EXIT:$?"` and look for `EXIT:2`.
2. **Check how many batches have content**: count non-empty `.import/batch-import-*.txt` files (the script prints the count).
   - **If only 1 batch has content**: process it (step 3) and immediately proceed to Finalization — say "Batch done. Say `finalize ingest` (or `/wiki-finalize-ingest`) to wrap up."
   - **If 2+ batches have content**: instruct the user — "Batches ready. Open N more Claude Code sessions. In each one say: `ingest next batch` (or `/wiki-ingest-next-batch`). I'll start batch 1 now. When all sessions are done, say `finalize ingest` (or `/wiki-finalize-ingest`) here." — then proceed to step 3.
3. **Process batch 1**: first claim it atomically:
   ```bash
   mv .import/batch-import-1.txt .import/batch-import-1.claimed.txt
   ```
   Then read `.import/batch-import-1.claimed.txt`. For each file listed, apply per-note ingestion above. Use sub-agents and process in batches of 10 to conserve context. After finishing all files, delete `.import/batch-import-1.claimed.txt`.
4. **If single-batch**: tell the user to run `finalize ingest`. **If multi-batch**: report notes processed/pages created/updated, then await "finalize ingest".

## Per-note ingestion (reference — applied within sessions)

For each markdown file:

- The top-level wiki topic list is: competition, concepts, decisions, people, problems, projects, systems.
- **Only use topics from that list.** Never create a `wiki/<dir>/` that is not one of those topics — not "systems", not "architecture", not anything else.
- Identify relevance to each wiki topic. For relevant topics: create a new page or update an existing one.
  - Always create pages at exactly one level deep: `wiki/<topic>/<page>.md` — never deeper (e.g. `wiki/concepts/NavSDK.md`, not `wiki/concepts/Navigation/NavSDK.md`).
  - Never delete or overwrite hand-curated content; expand and add instead.
  - For people: only create pages for confirmed employees, or people mentioned in multiple different sources. Require both first and last name. Ignore titles ("Dr.", "PhD.", "MD.") when parsing names — "John Smith, Dr." is one person named John Smith.
  - Check if the ingestion leads to a contradiction on the page. If ingestion leads to contradictions on a page, clearly mark the contradiction with a short explanation and add frontmatter tag `contradiction: true`.
  - Cross-reference related pages using `[[wikilinks]]`.
  - **Wikilink rule:** Only wikilink to a page that (a) already exists in `wiki/`, or (b) you are creating/have created in this same session. If you identify a topic worth referencing but cannot fully describe it yet, create a minimal stub: frontmatter with `type` and `stub: true`, a `# Title` heading, and one italic line noting the source file. Stubs count as `pages_created` in the session log.
  - **Stub expansion rule:** Before creating a new page, check if a stub already exists at that path (frontmatter contains `stub: true`). If so, expand it into a full page — remove `stub: true`, fill in proper content, and count it as `pages_updated` (not `pages_created`) in the session log.
- Do NOT update `wiki/<topic>/_index.md` during a session (deferred to finalization).
- Append one log entry to the session log `.import/batch-log-N.jsonl` (one JSON object per line):
```json
{"date":"YYYY-MM-DD HH:mm:ss","session":N,"file":"raw/notes/filename.md","summary":"One-sentence description.","pages_created":["wiki/concepts/NavSDK.md"],"pages_updated":["wiki/people/Jane Smith.md"]}
```

Conversions before ingestion:

- **Images/PDFs** in `raw/scans/`: convert using `mcp__claude_ai_Atlassian__fetch` or similar. Save converted markdown to `raw/scans/converted/` with frontmatter: `source` (link to original), `date` (source timestamp), `converted` (today). Ingest only `.md` files.
- **`.vtt` transcripts** in `raw/transcripts/`: run `python3 scripts/convert-vtt-to-md.py --new --dir raw/transcripts --output-dir raw/transcripts/converted`. Ingest only `.md` files.
- **`.eml` emails** in `raw/emails/`: run `python3 scripts/convert-eml-to-md.py --new --dir raw/emails --output-dir raw/emails/converted`. Ingest only `.md` files.
- Attachments linked from source files (e.g. in `_resources/` directories) are also included.

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
- Continue with per-note ingestion for that file (as a single-file session — write to `.import/batch-log-1.jsonl`, then immediately tell the user to run `finalize ingest`).

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
