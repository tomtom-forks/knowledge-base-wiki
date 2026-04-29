---
name: knowledge-base-ingest
description: Use when the user asks to ingest, import, or process notes; mentions a raw note file path; provides a Confluence URL or page title; or says "process new files". Covers standard ingestion, Confluence MCP fetching, and bulk batch processing.
---

# Knowledge Base - Ingest

## Standard ingestion

When user provides a source file or asks to 'ingest new raw notes':

- Read the personal info (if it exists), from `config/personal_info.md`.
- Check last ingestion date: `grep "^##* \[" wiki/log.md | tail -120`; if the last ingestion date is today, then check if notes were already added by looking at the whether the note filename is already present in the last 100 lines from the previous `grep` for the ingestion date (last 100 filename entries).
- The source files to ingest are in `raw/`, only those that were not yet ingested (required conversions before ingestion specified below).
- The source files to ingest also include attachments linked from source files (e.g. in `_resource/` directories).

Conversions before ingestion:

- For images/PDFs:
  - Convert images and PDFs (handwritten notes, whiteboards) using `mcp__claude_ai_Atlassian__fetch` or similar, to markdown.
  - Save converted files to `converted/` alongside the source.
  - The YAML frontmatter for converted files must contain: 
    - source: A link to the original source file.
    - date: The date of the note (timestamp of source file).
    - converted: The conversion date.
  - Ingest only the `.md` files.

- For `.vtt` transcripts in `raw/transcripts/`:
  - Convert `.vtt` transcripts to markdown in `raw/transcripts/converted` using:
```
python3 scripts/svtt_to_md.py --new --dir raw/transcripts --output-dir raw/transcripts/converted
```
  - Ingest only the `.md` files.

- For `.eml` emails in `raw/emails/`:
  - Convert `.eml` files to markdown in `raw/emails/converted/` with:
```
python3 scripts/eml_to_md.py --new --dir raw/emails --output-dir raw/emails/converted
```
- Ingest only the `.md` files.

Ingesting markdown files:
- Top-level wiki topics are created as subdirectories of `wiki/` (e.g. `wiki/concepts` and `wiki/systems`).
- Identify relationships to the wiki top-level topics mentioned in `config/personal_info.md`.
  - If no topics are defined (or the file does not exist) use these: competition, concepts, decisions, people, problems, projects. 
- For each of the top-level wiki topics: 
  - Create a new page or update an existing one if the ingested file has relevance to that topic.
  - Add new info, expand sections based on the ingested file.
  - Never delete or overwrite hand-curated content. 
  - Make sure new pages are always created n the subdirectory of a top-level wiki topic, and not deeper (e.g. `wiki/systems/NavSDK.md` and never `wiki/systems/Navigation/NavSDK.md`).
- For people, only create wiki pages for employees of my company, unless the person is mentioned mutliple times in different pages. 
  - Beware that people names can be followed by titles, like "John Smith, Dr.", or "Jpohn Smith, MD. PhD.". This is still 1 person (not a second person called "Dr." or "PhD.").
  - Don't create pages for people unless you are confident you have a first- and last name. There should be no wiki pages for incomplete names.
- If ingestion leads to contradictions in the page, clearly mark the contradiction (with a short explanation) and add a frontmatter tag "requires-attention".
- Cross-reference pages with `[[wikilinks]]` between related pages.
- Update `wiki/<topic>/_index.md` — add new entries (link + summary).
  - Keep the index alphabetically sorted.
- Append an entry to `wiki/log.md` per ingested file: 
```
## [YYYY-MM-DD] ingest | [[<relative path>]]` + 1–2 sentence brief.
```

Finalize:
- At the end of ingestions, present a table with all affected pages in `wiki/`.
- Present a table with contradications resulting from the ingestion.
- Then, show a multi-select menu (`AskUserQuestion` with `multiSelect: true`). Always run QMD indexing before lint:
  - **All (recommended)** — QMD text + vector embedding + lint; supersedes individual selections
  - **QMD text re-index** (`qmd update`) — fast, keywords only
  - **QMD vector embedding** (`qmd update && qmd embed`) — slow, ~2 GB models; supersedes text-only if both selected
  - **Lint** — health check: orphans, contradictions, gaps

Notes:
- Ingesting a single source file may touch many (like 5–25+) wiki pages. That is expected and desirable.
- Never re-ingest files without explicit user confirmation. Normally, only new files would be ingested.

## Bulk ingestion

- When processing multiple files: ingest in batches of 10, newest-to-oldest, using sub-agemts as much as you can to not run out of context.
- After each batch, announce topics created/updated and update `wiki/log.md`.
- Make sure all individual page references end up in the log as header-2 items ("##"), so the script can check for un-ingested notes later by looking at the header-2 lines.

**Log format:** (example with a batch of 3 notes)
```
## [2026-04-19] batch - [[raw/notes/note1]] Short explanation of page.
## [2026-04-19] batch - [[raw/notes/note2]] Another explanation.
## [2026-04-19] batch - [[raw/notes/note3]] And one more.
```

- Proceed batch-by-batch until at most 100 notes are processed.
- After processing 100 notes stop and tell the user how they can resume processing.

## Confluence ingestion

Triggered by a Confluence URL or page title:

- Fetch via `mcp__claude_ai_Atlassian__fetch`
- Save to `raw/confluence/<page-slug>.md` with frontmatter:
```yaml
---
source_url: <url>
fetched: YYYY-MM-DD
---
```
- Continue with standard ingest from step 3 above.

**Refresh:** "refresh this Confluence page" → re-fetch, overwrite cache, diff vs previous, flag changes affecting existing wiki pages.

## log.md format

`wiki/log.md` is sorted oldest-to-newest. Each entry is a `## [YYYY-MM-DD]` header followed by a markdown list. One header per ingested item — never concatenate items on one line.

```markdown
# Ingest Log

## [2026-04-20] init | wiki initialized

## [2026-04-25] ingest - [[raw/scans/converted/meeting-2026-03-01.md]] Quarterly planning meeting.
- [[more notes]] - Short description.
- Created pages:
  - [[decisions/adopt-vector-tiles]]
  - [[systems/AutoStream]]
- Updated pages:
  - [[people/Jane Smith]]
```

