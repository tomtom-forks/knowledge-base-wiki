---
name: knowledge-base-health-check
description: Use when the user asks for a health check, lint, audit, or wants to check for orphan pages, contradictions, or data gaps in the wiki.
---

# Knowledge Base - Health Check

## Step 1: Broken link check (automatic)

Run this command first — it auto-fixes trivial wikilink mismatches and reports what remains broken:

```bash
python3 scripts/check-broken-links.py --fix
```

- `--fix` repairs wikilinks where a unique normalized match exists (e.g. colons vs underscores in filenames). These are applied immediately without requiring user confirmation.
- Report how many links were fixed, and list any remaining broken links for the user to review manually.
- If there are problems left, suggest the user to run:
```bash
scripts/check-broken-links.py --interactive
```

## Step 2: Manual checks

Present recommendations only — never modify the wiki for these without user confirmation.

1. **Orphan pages** — pages with no inbound `[[links]]` from other pages (links from `index.md` or `_index.md` are fine).
2. **Contradictions** — scan for conflicting facts within and between pages (introduced by successive ingestions).
3. **Missing dedicated pages** — topics mentioned across multiple pages that lack their own page.
4. **Missing top-level topics** — if 10+ pages relate to a common concept not listed as a top-level topic in `wiki/index.md`, suggest adding it.
5. **Data gaps** — suggest new sources worth finding.
