---
name: wiki-ingest-next-batch
description: Use when continuing a batch import that was started in another session — when the user says "ingest next batch" or opens a parallel session during a multi-batch import. Claims and processes the next unclaimed batch file. Prompts to finalize if no batches remain but logs exist.
---

# Knowledge Base - Ingest Next Batch

This skill handles Sessions 2–N of a multi-batch import started by `wiki-ingest`.

## Step 1 — Check state

Before claiming a batch, verify the import is in progress:

```bash
ls raw/_batch-import-[0-9]*.txt 2>/dev/null | grep -v '\.claimed\.' | sort -V
ls raw/_batch-log-*.jsonl 2>/dev/null
```

- **No unclaimed `.txt` files AND no `.jsonl` log files**: nothing to do — tell the user "No batch import in progress. Use `wiki-ingest` to start a new import."
- **No unclaimed `.txt` files BUT `.jsonl` log files exist**: all batches are processed. Tell the user: "All batches appear complete. Say `finalize ingest` (or `/wiki-finalize-ingest`) to merge logs and rebuild indexes."
- **Unclaimed `.txt` files exist**: proceed to Step 2.

## Step 2 — Claim a batch atomically

Run the following to find and claim the next unclaimed batch:

```bash
for f in $(ls raw/_batch-import-[0-9]*.txt 2>/dev/null | grep -v '\.claimed\.' | sort -V); do
  mv "$f" "${f%.txt}.claimed.txt" 2>/dev/null && echo "${f%.txt}.claimed.txt" && break
done
```

- If the loop prints a filename (e.g. `raw/_batch-import-2.claimed.txt`) → that is your batch; proceed to Step 3.
- If nothing is printed → all batches are taken or already done; report "No unclaimed batch found — another session may be processing the last batch" and stop.

## Step 3 — Process the batch

For each file listed in the claimed `.claimed.txt` file, apply the per-note ingestion rules (same as in `wiki-ingest`):

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
- Append one log entry to the session log `raw/_batch-log-N.jsonl` (one JSON object per line):
```json
{"date":"YYYY-MM-DD HH:mm:ss","session":N,"file":"raw/notes/filename.md","summary":"One-sentence description.","pages_created":["wiki/concepts/NavSDK.md"],"pages_updated":["wiki/people/Jane Smith.md"]}
```

Conversions before ingestion:

- **Images/PDFs** in `raw/scans/`: convert using `mcp__claude_ai_Atlassian__fetch` or similar. Save converted markdown to `raw/scans/converted/` with frontmatter: `source` (link to original), `date` (source timestamp), `converted` (today). Ingest only `.md` files.
- **`.vtt` transcripts** in `raw/transcripts/`: run `python3 scripts/convert-vtt-to-md.py --new --dir raw/transcripts --output-dir raw/transcripts/converted`. Ingest only `.md` files.
- **`.eml` emails** in `raw/emails/`: run `python3 scripts/convert-eml-to-md.py --new --dir raw/emails --output-dir raw/emails/converted`. Ingest only `.md` files.
- Attachments linked from source files (e.g. in `_resources/` directories) are also included.

Use sub-agents and process in batches of 10 to conserve context.

## Step 4 — Finish

After processing all files, delete the `.claimed.txt` file.

Report:
- Batch number claimed
- Number of notes processed
- Pages created and updated

Then check if more unclaimed batches remain:

```bash
ls raw/_batch-import-[0-9]*.txt 2>/dev/null | grep -v '\.claimed\.'
```

- **Unclaimed batches remain**: tell the user "More batches available — other sessions can run `wiki-ingest-next-batch` to claim them."
- **No unclaimed batches remain AND `.jsonl` logs exist**: tell the user "All batches are now processed. When all parallel sessions are done, say `finalize ingest` (or `/wiki-finalize-ingest`) in the coordinator session to wrap up."
