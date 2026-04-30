---
name: wiki:add-missing
description: Use when the user notices a system, concept, person, project, decision, competitor, or problem is missing from the wiki and wants to create a page for it. Gathers scattered information from raw notes and existing wiki pages, synthesizes it, and creates a properly structured wiki page with backlinks.
---

# Wiki — Add Missing Page

Follow this workflow step by step. Do not skip steps or batch them together.

---

## Step 1 — Choose topic type

Use `AskUserQuestion` with `type: single_choice` to ask:

> "Which topic type does this page belong to?"

Present these options:
- `competition` — competing companies, products, and approaches
- `concepts` — technologies, standards, mental models, domain vocabulary
- `decisions` — why decisions were taken, on what basis, by whom, and when
- `people` — colleagues, contacts, external stakeholders, teams
- `problems` — active and past problems
- `projects` — active and past initiatives
- `systems` — our products, platforms, and services

---

## Step 2 — Name and description

Use `AskUserQuestion` (free text) to ask:

> "What is the name and a one-sentence description of the missing page?
>
> Example: Name: NDS.Live / Description: The tile and layer-based streaming format of NDS used for real-time map data delivery."

Parse the response to extract:
- **name** — the page title (used verbatim as the filename and H1)
- **description** — one-sentence summary (used in the index entry and to guide search)

---

## Step 3 — Related search terms

Use `AskUserQuestion` (free text) to ask:

> "List any related terms, acronyms, aliases, or people that should be searched to find relevant notes (comma-separated).
>
> Example: NDS, tile streaming, LiveMap, HERE HD Live, map tiles"

Combine these terms with the name and description from Step 2. You will use all of them in the searches below.

---

## Step 4 — Search and collect

Run **parallel** QMD searches to gather all potentially relevant content. Cast a wide net.

### Searches to run (all in parallel):

**Lexical (lex) searches** — exact keyword matches:
- Query: the page name
- Query: each related term from Step 3 (one query per term or combined)

**Semantic (vec) searches** — meaning-based:
- Query: the description from Step 2
- Query: "what is [name] and how does it work"
- Query: each related term phrased as a concept

**Hypothetical document (hyde) search**:
- Query: write a short paragraph describing what an answer about [name] would look like

**Collections to search** (include all):
- `raw-notes` — primary source (2700+ meeting notes, documents)
- `raw-emails` — email threads
- `raw-scans-transcribed` — scanned/transcribed documents
- `raw-confluence` — Confluence pages
- `wiki-concepts`, `wiki-systems`, `wiki-decisions`, `wiki-people`, `wiki-competition`, `wiki-projects`, `wiki-problems` — existing wiki pages

Use `minScore: 0.5` to filter noise. Use `intent` on every call to improve snippet relevance (set intent to the description from Step 2).

### After searching:
- Retrieve full content of the top-scoring hits using `mcp__plugin_qmd_qmd__get` or `mcp__plugin_qmd_qmd__multi_get`.
- Apply your own insight: think about what adjacent concepts, systems, or people might relate to this topic and run additional targeted searches for those too.
- Collect all source file paths (for citation).

---

## Step 5 — Synthesize and write the page

Apply the correct template below for the chosen topic type. Fill every section with synthesized content from Step 4. Omit any section for which no relevant information was found.

### Formatting rules (always apply):
- Wikilinks in body text: bare slugs — `[[elastic-map]]`
- Wikilinks in `_index.md` entries: vault-relative — `[[wiki/systems/elastic-map]]`
- Empty link sections are omitted entirely
- YAML frontmatter lists use indented hyphens
- Cite sources inline: `Source: raw/notes/2024-03-15 Meeting.md`
- Always wikilink any reference to a known person, system, concept, or decision page

### Templates by topic type:

**concepts:**
```
---
type: concept
date: YYYY-MM-DD HH:mm:ss
tags: []
---
# <Name>
## Short definition
## When it applies
## Explanation of the concept
## Examples in our context
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related people
- [[...]]
## Related notes
- [[...]]
```

**systems:**
```
---
type: system
owner:
  - team-name
status: active | deprecated | planned
---
# <Name>
## What it does
## Interfaces and dependencies
## Known issues and risks
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related people
- [[...]]
## Related notes
- [[...]]
```

**competition:**
```
---
type: competitor
---
# <Name>
## What they do
## Key products and technologies
## How they compare to us
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related notes
- [[...]]
```

**decisions:**
```
---
type: decision
status: accepted | superseded | proposed
date: YYYY-MM-DD HH:mm:ss
systems:
  - system-name
people:
  - person-name
---
# Decision: <title>
## Context
## Concern
## Criteria
## Options
## Decision
## Rationale
## Consequences
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related people
- [[...]]
## Related notes
- [[...]]
```

**people:**
```
---
type: person | team
---
# <Name>
## Role and scope
## Working style and context
## Active on
- [[project-link]]
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related notes
- [[...]]
```

**projects:**
```
---
type: project
status: active | closed | paused
started: YYYY-MM-DD HH:mm:ss
---
# <Title>
## Project description and goals
## Current state
## Open questions
## Log
<!-- append updates here, newest first -->
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related people
- [[...]]
## Related notes
- [[...]]
```

**problems:**
```
---
type: problem
status: open | closed | deferred
started: YYYY-MM-DD HH:mm:ss
---
# <Title>
## Problem statement and goal
## Current state
## Open questions
## Log
<!-- append updates here, newest first -->
## Related decisions
- [[...]]
## Related systems
- [[...]]
## Related people
- [[...]]
## Related notes
- [[...]]
```

Write the completed page to: `wiki/<topic>/<Name>.md`

Use today's date for the `date` field.

---

## Step 6 — Add backlinks

For each page listed in the new page's Related sections:

1. Read the existing page.
2. Find the relevant Related section (e.g. `## Related concepts`, `## Related systems`, `## Related people`).
3. If the new page is not already linked there, add a wikilink entry: `- [[<new-page-slug>]]`
4. If the section doesn't exist yet, add it before `## Related notes` (or at the end if that section is absent).

Do not modify any other content of those pages.

---

## Step 7 — Update the index

1. Read `wiki/<topic>/_index.md`.
2. Add a new entry for the new page in **alphabetical order** among the existing entries.
3. Use vault-relative wikilink format: `[[wiki/<topic>/<Name>|<Name>]] — <one-sentence description>`
4. Write the updated index back.

---

## Step 8 — Report

After completing all steps, report to the user:
- The path of the new page created
- The number of source documents used
- The pages that received backlinks
- Any sections left empty due to insufficient information
