#!/usr/bin/env python3
"""
check-broken-links.py — Scan Markdown files for broken internal and external links.

Output is structured JSON designed for AI consumption:
  {
    "broken_links": [
      {
        "file": "relative/path/to/file.md",
        "line": 12,
        "type": "wikilink|markdown|image",
        "raw": "[[target]]",
        "target": "target",
        "reason": "file not found"
      },
      ...
    ],
    "summary": { "files_checked": N, "links_checked": N, "broken": N, "skipped_external": N },
    "errors": [ "...", ... ]
  }

Usage:
  python3 check-broken-links.py [OPTIONS] [ROOT_DIR]

Options:
  --help, -h          Show this help message and exit
  --external          Also check HTTP/HTTPS links (slow; requires network)
  --timeout N         Timeout in seconds for external requests (default: 5)
  --include-images    Also check embedded images (![[...]])
  --format text|json  Output format: 'text' for human-readable, 'json' for AI (default: json)
  --quiet             Suppress progress messages on stderr

ROOT_DIR defaults to the directory containing this script's parent.
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

# Matches [[target]], [[target|alias]], [[target#anchor|alias]] — Obsidian wikilinks.
# Captures only the target portion (before any | or # delimiter).
# A single ']' that is NOT followed by another ']' is allowed inside the target
# (e.g. [[example [1] of a note]]), while ']]' ends the link.
RE_WIKILINK = re.compile(r'(?<!!)\[\[((?:[^\]|#\n]|\](?!\]))+)')
# Matches ![[target]] — Obsidian image embeds (same bracket rule applies)
RE_IMAGE_EMBED = re.compile(r'!\[\[((?:[^\]|#\n]|\](?!\]))+)')
# Matches [text](target) — standard markdown links; skips http/https separately
RE_MDLINK = re.compile(r'(?<!!)\[[^\]]*\]\(([^)#\n]+?)(?:#[^)]*)?\)')
# Matches ![alt](target) — standard markdown images
RE_MDIMAGE = re.compile(r'!\[[^\]]*\]\(([^)#\n]+?)(?:#[^)]*)?\)')


CURLY_TO_STRAIGHT = str.maketrans({
    '‘': "'",  # '  LEFT SINGLE QUOTATION MARK
    '’': "'",  # '  RIGHT SINGLE QUOTATION MARK
    '“': '"',  # "  LEFT DOUBLE QUOTATION MARK
    '”': '"',  # "  RIGHT DOUBLE QUOTATION MARK
})
_CURLY_RE = re.compile(r'[‘’“”]')


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "ftp://", "mailto:"))


def strip_frontmatter(content: str) -> tuple[str, int]:
    """
    If content starts with a YAML frontmatter block (--- ... ---), replace it
    with blank lines so line numbers are preserved. Returns (modified_content, fm_end_line).
    """
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content, 0
    for i, line in enumerate(lines[1:], 1):
        if line.strip() in ("---", "..."):
            blanked = [""] * (i + 1)
            return "\n".join(blanked) + "\n" + "".join(lines[i + 1:]), i + 1
    return content, 0  # unclosed frontmatter — don't strip


def extract_links(content: str, include_images: bool, skip_frontmatter: bool = False):
    """Yield (line_number, type, raw_match, target) for every link in content."""
    if skip_frontmatter:
        content, _ = strip_frontmatter(content)
    lines = content.splitlines()
    for lineno, line in enumerate(lines, 1):
        # Obsidian wikilinks
        for m in RE_WIKILINK.finditer(line):
            target = m.group(1).strip()
            if line[m.end():].startswith('|(broken link)'):
                continue  # already marked by --remove-broken-links; skip
            yield lineno, "wikilink", m.group(0), target
        # Obsidian image embeds
        if include_images:
            for m in RE_IMAGE_EMBED.finditer(line):
                target = m.group(1).strip()
                yield lineno, "image", m.group(0), target
        # Standard markdown links
        for m in RE_MDLINK.finditer(line):
            target = m.group(1).strip()
            yield lineno, "markdown", m.group(0), target
        # Standard markdown images
        if include_images:
            for m in RE_MDIMAGE.finditer(line):
                target = m.group(1).strip()
                yield lineno, "image", m.group(0), target


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

KNOWN_EXTENSIONS = {".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp"}

# Characters that are often replaced by '_' when a title becomes a filename.
_PROBLEMATIC_CHARS = re.compile(r'[_:?*|"<>\\]')


def normalize_name(name: str) -> str:
    """Canonical form for fuzzy matching.

    Replaces '_' and chars typically substituted with '_' in filenames with a
    space, then collapses whitespace. This makes '[[foo: bar]]', '[[foo bar]]',
    and the file 'foo_ bar.md' all map to the same key.
    """
    return re.sub(r'\s+', ' ', _PROBLEMATIC_CHARS.sub(' ', name)).strip().lower()


def build_normalized_index(root: Path) -> dict[str, list[Path]]:
    """Map normalize_name(stem) -> list of .md paths, for fuzzy wikilink matching."""
    index: dict[str, list[Path]] = {}
    for p in root.rglob("*.md"):
        if should_skip_md(p, root):
            continue
        key = normalize_name(p.stem)
        index.setdefault(key, []).append(p)
    return index


def find_normalized_match(target: str, root: Path, norm_index: dict[str, list[Path]]) -> "str | None":
    """Try to match a broken wikilink target by normalizing problematic characters.

    Returns the corrected link text (stem, or relative path if the original
    target included a directory) if exactly one file matches, else None.
    """
    candidate = Path(target)
    has_known_ext = candidate.suffix.lower() in KNOWN_EXTENSIONS
    name = candidate.stem if has_known_ext else candidate.name
    key = normalize_name(name)
    if not key:
        return None
    # If the target includes a directory prefix, restrict the search to that subdir.
    if candidate.parent != Path("."):
        subdir = root / candidate.parent
        if subdir.is_dir():
            for p in subdir.glob("*.md"):
                if normalize_name(p.stem) == key:
                    return str(candidate.parent / p.stem)
        return None
    # Vault-wide fuzzy match — only accept a unique result to avoid false fixes.
    matches = norm_index.get(key, [])
    if len(matches) == 1:
        return matches[0].stem
    return None


def fix_wikilinks_in_file(file_path: Path, fixes: list) -> int:
    """Replace wikilink targets in-place; returns the number of substitutions made."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    count = 0
    for old_target, new_target in fixes:
        pattern = re.compile(r'(?<!!)\[\[' + re.escape(old_target) + r'(?=[\]|#\n])')
        content, n = pattern.subn(f'[[{new_target}', content)
        count += n
    if count:
        file_path.write_text(content, encoding="utf-8")
    return count


def resolve_wikilink(target: str, root: Path, all_md_stems: dict[str, list[Path]]) -> bool:
    """
    Resolve an Obsidian wikilink against the vault root.
    Wikilinks can be:
      - a bare filename stem:    people/rijn-buve  →  <root>/people/rijn-buve.md
      - a full path (no ext):    wiki/concepts/foo →  <root>/wiki/concepts/foo.md
      - a full path with ext:    wiki/concepts/foo.md
    Also checks .png/.jpg/.jpeg/.gif/.svg/.pdf for embedded files.
    If the target has no recognized extension, .md is assumed (Obsidian default).
    Returns True if the target resolves to an existing file.
    """
    candidate = Path(target)
    has_known_ext = candidate.suffix.lower() in KNOWN_EXTENSIONS

    # Try exact path first
    if (root / target).exists():
        return True

    # If no recognized extension, try appending .md and other known types.
    # This handles bare names like "my-note", paths like "wiki/concepts/foo",
    # and names with dots that aren't file extensions (e.g. "2024.05.15").
    if not has_known_ext:
        for ext in (".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp"):
            if (root / (target + ext)).exists():
                return True

    # Fuzzy match: bare stem against all known markdown files.
    # Use the full name as the lookup key when there is no recognized extension,
    # so that "v1.2" matches "v1.2.md" rather than looking up "v1".
    stem = candidate.stem if has_known_ext else candidate.name
    if stem in all_md_stems:
        return True

    return False


def resolve_mdlink(target: str, source_file: Path, root: Path, all_md_stems: dict[str, list[Path]]) -> bool:
    """Resolve a standard markdown relative link."""
    if is_external(target):
        return True  # handled separately

    # URL-decode basic percent-encoding (e.g. spaces as %20)
    try:
        from urllib.parse import unquote
        target = unquote(target)
    except Exception:
        pass

    p = (source_file.parent / target).resolve()
    if p.exists():
        return True

    # Also try treating as root-relative
    p2 = (root / target).resolve()
    if p2.exists():
        return True

    return False


def check_external(url: str, timeout: int) -> tuple[bool, str]:
    """Return (ok, reason). Performs a HEAD request, falls back to GET."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "check-broken-links/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 400, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code == 405:
            # HEAD not allowed — try GET
            try:
                req2 = urllib.request.Request(url, method="GET")
                req2.add_header("User-Agent", "check-broken-links/1.0")
                with urllib.request.urlopen(req2, timeout=timeout) as resp:
                    return resp.status < 400, f"HTTP {resp.status}"
            except Exception as e2:
                return False, str(e2)
        return False, f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def mark_broken_wikilinks_in_file(file_path: Path, targets: list) -> int:
    """
    For each target in `targets`, rewrite every matching wikilink in file_path:
      [[target]]           -> [[target|(broken link) target]]
      [[target|text]]      -> [[target|(broken link) text]]
      [[target#heading]]   -> [[target#heading|(broken link) target]]
      [[target#heading|text]] -> [[target#heading|(broken link) text]]
    Returns the total number of substitutions made.
    """
    content = file_path.read_text(encoding="utf-8", errors="replace")
    count = 0
    for target in targets:
        pattern = re.compile(
            r'(?<!!)\[\[(' + re.escape(target) + r')(#[^|\]]*)?(\|[^\]\n]*)?\]\]'
        )
        def _replacer(m, _t=target):
            heading = m.group(2) or ""
            alias_part = m.group(3)          # includes leading '|', or None
            display = alias_part[1:] if alias_part else _t
            return f'[[{_t}{heading}|(broken link) {display}]]'
        content, n = pattern.subn(_replacer, content)
        count += n
    if count:
        file_path.write_text(content, encoding="utf-8")
    return count


def truncate_path(path: str, max_len: int = 40, prefix_len: int = 20) -> str:
    """Truncate path to max_len chars: first prefix_len chars + '...' + tail."""
    if len(path) <= max_len:
        return path
    tail_len = max_len - prefix_len - 3
    if tail_len <= 0:
        return path[:max_len]
    return path[:prefix_len] + "..." + path[-tail_len:]


def delete_wikilink_in_file(file_path: Path, target: str):
    # Remove [[target...]] from file, collapsing surrounding whitespace.
    # If the resulting line is empty or just a bare list marker, the whole line is dropped.
    # Returns (changed, removed_linenos) where removed_linenos are 1-indexed lines that
    # were fully deleted (so callers can adjust line numbers in sibling entries).
    content = file_path.read_text(encoding='utf-8', errors='replace')
    link_pat = re.compile(
        r'( ?)(?<!!)\[\[' + re.escape(target) + r'(?:#[^|\]]*)?(?:\|[^\]]*)?\]\]( ?)'
    )
    # Bare: optional indent + optional list marker + optional empty quote pair + whitespace.
    # Quote pairs: "" '' and their curly variants (via \u escapes)
    bare_pat = re.compile(
        r'^\s*(?:[-*+]|\d+\.)?\s*(?:""|\'\'|\u201c\u201d|\u2018\u2019)?\s*$'
    )

    lines = content.splitlines(keepends=True)
    new_lines = []
    changed = False
    removed_linenos: list[int] = []

    for lineno, line in enumerate(lines, 1):
        if not link_pat.search(line):
            new_lines.append(line)
            continue
        def _repl(m):
            return ' ' if (m.group(1) and m.group(2)) else ''
        new_line = link_pat.sub(_repl, line)
        if bare_pat.match(new_line.rstrip('\r\n')):
            changed = True
            removed_linenos.append(lineno)
        else:
            if new_line != line:
                changed = True
            new_lines.append(new_line)

    if changed:
        file_path.write_text(''.join(new_lines), encoding='utf-8')
    return changed, removed_linenos


def delete_mdlink_in_file(file_path: Path, target: str):
    """Remove [text](target) standard markdown links from file.
    If the resulting line is bare, the whole line is dropped.
    Returns (changed, removed_linenos)."""
    content = file_path.read_text(encoding='utf-8', errors='replace')
    link_pat = re.compile(
        r'( ?)(?<!!)\[[^\]]*\]\(' + re.escape(target) + r'(?:#[^)]*)?\)( ?)'
    )
    bare_pat = re.compile(
        r'^\s*(?:[-*+]|\d+\.)?\s*(?:""|\'\'|\u201c\u201d|\u2018\u2019)?\s*$'
    )

    lines = content.splitlines(keepends=True)
    new_lines = []
    changed = False
    removed_linenos: list[int] = []

    for lineno, line in enumerate(lines, 1):
        if not link_pat.search(line):
            new_lines.append(line)
            continue
        def _repl(m):
            return ' ' if (m.group(1) and m.group(2)) else ''
        new_line = link_pat.sub(_repl, line)
        if bare_pat.match(new_line.rstrip('\r\n')):
            changed = True
            removed_linenos.append(lineno)
        else:
            if new_line != line:
                changed = True
            new_lines.append(new_line)

    if changed:
        file_path.write_text(''.join(new_lines), encoding='utf-8')
    return changed, removed_linenos


def mark_as_broken_link_in_file(file_path: Path, target: str) -> bool:
    """Rewrite [[target]] → [[broken-link|target]] in file. Returns True if changed."""
    content = file_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r'(?<!!)\[\[(' + re.escape(target) + r')(#[^|\]]*)?(\|[^\]\n]*)?\]\]'
    )
    def _replacer(m, _t=target):
        alias_part = m.group(3)
        display = alias_part[1:] if alias_part else _t
        return f'[[broken-link|{display}]]'
    new_content, n = pattern.subn(_replacer, content)
    if n:
        file_path.write_text(new_content, encoding="utf-8")
        return True
    return False


def fix_curly_quotes(root: Path, quiet: bool) -> tuple[int, int, int]:
    """Rename .md files whose stems contain curly quotes and fix curly quotes in all link targets.
    Returns (renamed_files, link_files_changed, links_changed)."""
    # Pass 1: rename files whose stems contain curly quotes
    renamed = 0
    for p in sorted(root.rglob("*.md")):
        if should_skip_md(p, root):
            continue
        if not _CURLY_RE.search(p.stem):
            continue
        new_stem = p.stem.translate(CURLY_TO_STRAIGHT)
        new_path = p.parent / (new_stem + ".md")
        if new_path.exists():
            if not quiet:
                print(f"  Cannot rename {p.name}: {new_path.name} already exists", file=sys.stderr)
            continue
        p.rename(new_path)
        renamed += 1
        if not quiet:
            print(f"  Renamed: {p.name} → {new_path.name}", file=sys.stderr)

    # Pass 2: fix curly quotes inside link targets across all .md files
    link_files = 0
    link_count = 0
    for md_file in sorted(root.rglob("*.md")):
        if should_skip_md(md_file, root):
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _CURLY_RE.search(content):
            continue  # fast-path: no curly quotes anywhere in this file

        counter = [0]

        def _fix_wiki(m, _c=counter):
            inner = m.group(1)
            fixed = inner.translate(CURLY_TO_STRAIGHT)
            if fixed != inner:
                _c[0] += 1
                return f'[[{fixed}'
            return m.group(0)

        def _fix_img(m, _c=counter):
            inner = m.group(1)
            fixed = inner.translate(CURLY_TO_STRAIGHT)
            if fixed != inner:
                _c[0] += 1
                return f'![[{fixed}'
            return m.group(0)

        def _fix_md(m, _c=counter):
            target = m.group(1)
            if is_external(target) or not _CURLY_RE.search(target):
                return m.group(0)
            fixed = target.translate(CURLY_TO_STRAIGHT)
            _c[0] += 1
            offset = m.start(1) - m.start(0)
            full = m.group(0)
            return full[:offset] + fixed + full[offset + len(target):]

        new_content = RE_WIKILINK.sub(_fix_wiki, content)
        new_content = RE_IMAGE_EMBED.sub(_fix_img, new_content)
        new_content = RE_MDLINK.sub(_fix_md, new_content)
        new_content = RE_MDIMAGE.sub(_fix_md, new_content)

        if counter[0]:
            md_file.write_text(new_content, encoding="utf-8")
            link_files += 1
            link_count += counter[0]

    return renamed, link_files, link_count


def should_skip_md(path: Path, root: Path) -> bool:
    """Return True if this .md file should be excluded from scanning."""
    rel = path.relative_to(root)
    # Skip files inside hidden directories (any parent component starting with '.')
    if any(part.startswith(".") for part in rel.parts[:-1]):
        return True
    # Skip SKILL.md files (superpowers skill definitions)
    if path.name == "SKILL.md":
        return True
    # Skip the log file — it contains ingest headers with individual page links
    if rel == Path("wiki/log.md"):
        return True
    return False


def build_stem_index(root: Path) -> dict[str, list[Path]]:
    """Build a map from filename stem → list of matching paths (for fuzzy wikilink resolution)."""
    index: dict[str, list[Path]] = {}
    for p in root.rglob("*.md"):
        if should_skip_md(p, root):
            continue
        s = p.stem
        index.setdefault(s, []).append(p)
    return index


def check_vault(root: Path, args) -> dict:
    errors = []
    broken = []
    total_files = 0
    total_links = 0
    skipped_external = 0

    if not root.is_dir():
        return {
            "broken_links": [],
            "summary": {"files_checked": 0, "links_checked": 0, "broken": 0, "skipped_external": 0},
            "errors": [f"Root directory not found: {root}"]
        }

    if not args.quiet:
        print(f"Scanning {root} ...", file=sys.stderr)

    stem_index = build_stem_index(root)
    norm_index = build_normalized_index(root)
    md_files = sorted(p for p in root.rglob("*.md") if not should_skip_md(p, root))

    for md_file in md_files:
        total_files += 1
        rel = md_file.relative_to(root)

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errors.append(f"Cannot read {rel}: {e}")
            continue

        _, fm_end_line = strip_frontmatter(content)

        for lineno, link_type, raw, target in extract_links(content, args.include_images, args.skip_frontmatter):
            total_links += 1

            # External links
            if is_external(target):
                if args.external:
                    ok, reason = check_external(target, args.timeout)
                    if not ok:
                        broken.append({
                            "file": str(rel),
                            "line": lineno,
                            "type": link_type,
                            "raw": raw,
                            "target": target,
                            "reason": reason,
                        })
                else:
                    skipped_external += 1
                continue

            # Skip empty or anchor-only targets
            if not target or target.startswith("#"):
                total_links -= 1
                continue

            # Resolve
            if link_type == "wikilink" or (link_type == "image" and "[[" in raw):
                ok = resolve_wikilink(target, root, stem_index)
            else:
                ok = resolve_mdlink(target, md_file, root, stem_index)

            if not ok:
                entry = {
                    "file": str(rel),
                    "line": lineno,
                    "type": link_type,
                    "raw": raw,
                    "target": target,
                    "reason": "file not found",
                }
                if fm_end_line and lineno <= fm_end_line:
                    entry["in_frontmatter"] = True
                if link_type == "wikilink" or (link_type == "image" and "[[" in raw):
                    fix = find_normalized_match(target, root, norm_index)
                    if fix:
                        entry["suggested_fix"] = fix
                broken.append(entry)

        if not args.quiet and total_files % 50 == 0:
            print(f"\r  {total_files} files scanned ...", end="", flush=True, file=sys.stderr)

    if not args.quiet:
        print(f"\r  {total_files} files scanned — done.        ", file=sys.stderr)

    fixed_links = 0
    fixed_files = 0
    if getattr(args, "fix_simple_errors", False):
        fixes_by_file: dict = {}
        for entry in broken:
            if "suggested_fix" in entry:
                fp = root / entry["file"]
                fixes_by_file.setdefault(fp, []).append(
                    (entry["target"], entry["suggested_fix"])
                )
        for fp, fixes in fixes_by_file.items():
            seen: set = set()
            deduped = [f for f in fixes if not (f in seen or seen.add(f))]  # type: ignore[func-returns-value]
            n = fix_wikilinks_in_file(fp, deduped)
            if n:
                fixed_files += 1
                fixed_links += n
        for entry in broken:
            if "suggested_fix" in entry:
                entry["fixed"] = True
        if not args.quiet and fixed_links:
            print(f"  Fixed {fixed_links} link(s) in {fixed_files} file(s).", file=sys.stderr)

        # Delete bullet lines in YAML frontmatter that contain unfixable broken wikilinks
        fm_targets_by_file: dict = {}
        for entry in broken:
            if entry.get("fixed") or not entry.get("in_frontmatter"):
                continue
            if entry["type"] == "wikilink" or (entry["type"] == "image" and "[[" in entry["raw"]):
                fp = root / entry["file"]
                fm_targets_by_file.setdefault(fp, []).append(entry["target"])
        fm_deleted_links = 0
        fm_deleted_files = 0
        for fp, targets in fm_targets_by_file.items():
            seen: set = set()
            deduped = [t for t in targets if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]
            file_changed = False
            for target in deduped:
                changed, _ = delete_wikilink_in_file(fp, target)
                if changed:
                    fm_deleted_links += 1
                    file_changed = True
            if file_changed:
                fm_deleted_files += 1
        for entry in broken:
            if not entry.get("fixed") and entry.get("in_frontmatter"):
                if entry["type"] == "wikilink" or (entry["type"] == "image" and "[[" in entry["raw"]):
                    entry["fm_deleted"] = True
        if not args.quiet and fm_deleted_links:
            print(f"  Removed {fm_deleted_links} frontmatter broken link(s) in {fm_deleted_files} file(s).", file=sys.stderr)

        q_renamed, q_link_files, q_links = fix_curly_quotes(root, args.quiet)
        if not args.quiet and (q_renamed or q_links):
            print(f"  Curly quotes: {q_renamed} file(s) renamed, "
                  f"{q_links} link(s) updated in {q_link_files} file(s).", file=sys.stderr)

    removed_links = 0
    removed_files = 0
    if getattr(args, "remove_broken_links", False):
        targets_by_file: dict = {}
        for entry in broken:
            if entry.get("fixed"):
                continue
            if entry["type"] == "wikilink" or (entry["type"] == "image" and "[[" in entry["raw"]):
                fp = root / entry["file"]
                targets_by_file.setdefault(fp, []).append(entry["target"])
        for fp, targets in targets_by_file.items():
            seen: set = set()
            deduped = [t for t in targets if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]
            n = mark_broken_wikilinks_in_file(fp, deduped)
            if n:
                removed_files += 1
                removed_links += n
        for entry in broken:
            if not entry.get("fixed") and (entry["type"] == "wikilink" or (entry["type"] == "image" and "[[" in entry["raw"])):
                entry["removed"] = True
        if not args.quiet and removed_links:
            print(f"  Marked {removed_links} broken link(s) in {removed_files} file(s).", file=sys.stderr)

    summary: dict = {
        "files_checked": total_files,
        "links_checked": total_links,
        "broken": len(broken),
        "skipped_external": skipped_external,
    }
    if getattr(args, "fix_simple_errors", False):
        summary["fixed_links"] = fixed_links
        summary["fixed_files"] = fixed_files
        if fm_deleted_links:
            summary["fm_deleted_links"] = fm_deleted_links
            summary["fm_deleted_files"] = fm_deleted_files
        if q_renamed or q_links:
            summary["quote_renamed_files"] = q_renamed
            summary["quote_updated_links"] = q_links
            summary["quote_updated_link_files"] = q_link_files
    if getattr(args, "remove_broken_links", False):
        summary["removed_links"] = removed_links
        summary["removed_files"] = removed_files

    return {
        "broken_links": broken,
        "summary": summary,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_text(result: dict) -> str:
    lines = []
    s = result["summary"]
    lines.append(f"Checked {s['files_checked']} files, {s['links_checked']} links — "
                 f"{s['broken']} broken, {s['skipped_external']} external skipped.")
    if s.get("fixed_links"):
        lines.append(f"Fixed {s['fixed_links']} link(s) in {s['fixed_files']} file(s).")
    if s.get("fm_deleted_links"):
        lines.append(f"Removed {s['fm_deleted_links']} frontmatter broken link(s) in {s['fm_deleted_files']} file(s).")
    if s.get("removed_links"):
        lines.append(f"Marked {s['removed_links']} broken link(s) in {s['removed_files']} file(s).")
    lines.append("")

    if result["errors"]:
        lines.append("ERRORS:")
        for e in result["errors"]:
            lines.append(f"  ! {e}")
        lines.append("")

    if not result["broken_links"]:
        lines.append("No broken links found.")
    else:
        lines.append("BROKEN LINKS:")
        for b in result["broken_links"]:
            lines.append(f"{b['line']}: {b['file']}")
            lines.append(f"    type  : {b['type']}")
            lines.append(f"    reason: {b['reason']}")
            lines.append(f"    raw   : {b['raw']}")
            lines.append(f"    target: {b['target']}")
            if "suggested_fix" in b:
                suffix = " (fixed)" if b.get("fixed") else " (use --fix-simple-errors to apply)"
                lines.append(f"    suggested_fix: {b['suggested_fix']}{suffix}")
            if b.get("removed"):
                lines.append("    action: marked as broken in file")
        lines.append("")
        lines.append("Tip: run with --interactive to fix broken links one by one.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def run_interactive(broken_links: list, root: Path) -> None:
    """Curses-based TUI for reviewing and fixing broken links one by one."""
    try:
        import curses as _curses
    except ImportError:
        print("Error: curses module not available on this platform.", file=sys.stderr)
        sys.exit(1)

    if not broken_links:
        print("No broken links found.")
        return

    n = len(broken_links)
    states: list = [None] * n      # None | 'deleted' | 'broken'
    messages: list = [""] * n

    def fmt_line(i: int) -> str:
        b = broken_links[i]
        return f"{i + 1:3d}  file:{truncate_path(b['file'])}, line:{b['line']}, link:{b['target']}"

    def do_delete(i: int) -> str:
        entry = broken_links[i]
        try:
            is_wiki = entry["type"] == "wikilink" or (entry["type"] == "image" and "[[" in entry["raw"])
            if is_wiki:
                ok, removed = delete_wikilink_in_file(root / entry["file"], entry["target"])
            else:
                ok, removed = delete_mdlink_in_file(root / entry["file"], entry["target"])
            if ok and removed:
                # Adjust line numbers for all entries in the same file that came
                # after any deleted line, so the popup context stays accurate.
                for other in broken_links:
                    if other["file"] != entry["file"]:
                        continue
                    shift = sum(1 for dl in removed if dl < other["line"])
                    if shift:
                        other["line"] -= shift
            return "deleted" if ok else "no match — may already be handled"
        except Exception as e:
            return f"error: {e}"

    def do_broken(i: int) -> str:
        entry = broken_links[i]
        try:
            ok = mark_as_broken_link_in_file(root / entry["file"], entry["target"])
            return "broken" if ok else "no match — may already be handled"
        except Exception as e:
            return f"error: {e}"

    def read_source_context(entry: dict, context: int = 2) -> list:
        """Return list of (lineno, text, is_current) for the line and `context` lines around it."""
        try:
            fp = root / entry["file"]
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            current = entry["line"] - 1  # 0-indexed
            start = max(0, current - context)
            end = min(len(lines), current + context + 1)
            return [(i + 1, lines[i], i == current) for i in range(start, end)]
        except Exception as e:
            return [(entry["line"], f"(error reading file: {e})", True)]

    def show_popup(stdscr, entry: dict) -> "str | None":
        """Draw a wide popup with source context and missing link; close on Enter."""
        context_lines = read_source_context(entry)
        missing = entry["target"]

        height, width = stdscr.getmaxyx()
        pop_w = min(max(40, width - 4), width - 2)
        inner_w = pop_w - 4

        def _wrap(lineno, text):
            prefix = f"{lineno:4d}  "
            text_w = max(1, inner_w - len(prefix))
            indent = " " * len(prefix)
            if not text:
                return [prefix]
            chunks = [text[i:i + text_w] for i in range(0, len(text), text_w)]
            return [prefix + chunks[0]] + [indent + c for c in chunks[1:]]

        # Pre-wrap all context lines so pop_h reflects actual row count
        display_rows: list[tuple[str, bool]] = []
        for lineno, text, is_current in context_lines:
            for chunk in _wrap(lineno, text):
                display_rows.append((chunk, is_current))

        # Layout: 4 header rows + content rows + sep + missing + hint + border = +4 fixed footer
        pop_h = min(4 + len(display_rows) + 4, height - 2)
        pop_y = max(0, (height - pop_h) // 2)
        pop_x = max(0, (width - pop_w) // 2)

        sep = "─" * (pop_w - 2)
        hint = "[ ↑/↓ prev/next   d=delete   b=mark broken   Enter/q=close ]"

        win = _curses.newwin(pop_h, pop_w, pop_y, pop_x)
        win.keypad(True)
        win.box()
        title = " Broken link detail "
        win.addstr(0, (pop_w - len(title)) // 2, title)
        win.addstr(1, 2, f"file: {entry['file']}"[:pop_w - 3])
        win.addstr(2, 2, f"line: {entry['line']}"[:pop_w - 3])
        win.addstr(3, 1, sep[:pop_w - 2])
        max_content_rows = max(0, pop_h - 8)
        for i, (display, is_current) in enumerate(display_rows[:max_content_rows]):
            attr = _curses.A_NORMAL if is_current else _curses.A_DIM
            try:
                win.addstr(4 + i, 2, display[:pop_w - 3], attr)
            except _curses.error:
                pass
        sep_row = 4 + min(len(display_rows), max_content_rows)
        try:
            win.addstr(sep_row, 1, sep[:pop_w - 2])
            win.addstr(sep_row + 1, 2, f"Missing link: {missing}"[:pop_w - 3])
        except _curses.error:
            pass
        try:
            win.addstr(pop_h - 2, max(1, (pop_w - len(hint)) // 2), hint[:pop_w - 2])
        except _curses.error:
            pass
        win.refresh()

        action = None
        while True:
            key = win.getch()
            if key in (10, 13, ord("q"), ord("Q"), 27):
                break
            elif key == _curses.KEY_UP:
                action = "prev"
                break
            elif key == _curses.KEY_DOWN:
                action = "next"
                break
            elif key in (ord("d"), ord("D")):
                action = "d"
                break
            elif key in (ord("b"), ord("B")):
                action = "b"
                break

        del win
        stdscr.touchwin()
        stdscr.refresh()
        return action

    def curses_main(stdscr):
        _curses.curs_set(0)
        _curses.start_color()
        _curses.use_default_colors()
        _curses.init_pair(1, _curses.COLOR_BLACK, _curses.COLOR_CYAN)  # selected
        _curses.init_pair(2, _curses.COLOR_GREEN, -1)                  # deleted
        _curses.init_pair(3, _curses.COLOR_YELLOW, -1)                 # marked broken

        selected = 0
        offset = 0

        def redraw():
            nonlocal offset
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            list_height = height - 4

            stdscr.addstr(0, 0, f"Broken links ({n} total)"[:width - 1])
            stdscr.addstr(1, 0, "UP/DOWN navigate   ENTER=preview   d=delete   b=mark broken   q=quit"[:width - 1])
            stdscr.addstr(2, 0, ("─" * (width - 1))[:width - 1])

            if selected < offset:
                offset = selected
            elif selected >= offset + list_height:
                offset = selected - list_height + 1

            for row in range(list_height):
                idx = offset + row
                if idx >= n:
                    break
                state = states[idx]
                line = fmt_line(idx)
                if state == "deleted":
                    prefix = "[DEL] "
                    attr = _curses.color_pair(2)
                elif state == "broken":
                    prefix = "[BRK] "
                    attr = _curses.color_pair(3)
                else:
                    prefix = "      "
                    attr = _curses.A_NORMAL
                if idx == selected:
                    attr = _curses.color_pair(1) | _curses.A_BOLD
                stdscr.addstr(3 + row, 0, (prefix + line)[:width - 1], attr)

            done = sum(1 for s in states if s is not None)
            status = messages[selected] if messages[selected] else ""
            footer = f"  {done}/{n} handled" + (f"  — {status}" if status else "")
            try:
                stdscr.addstr(height - 1, 0, footer[:width - 1])
            except _curses.error:
                pass
            stdscr.refresh()

        while True:
            redraw()
            key = stdscr.getch()

            if key in (ord("q"), ord("Q"), 27):
                break
            elif key == _curses.KEY_UP:
                if selected > 0:
                    selected -= 1
            elif key == _curses.KEY_DOWN:
                if selected < n - 1:
                    selected += 1
            elif key in (10, 13):  # Enter — open popup starting from first unhandled item
                first_unhandled = next((i for i in range(n) if states[i] is None), None)
                if first_unhandled is None:
                    continue  # nothing left to handle
                idx = first_unhandled
                selected = idx
                while True:
                    redraw()
                    action = show_popup(stdscr, broken_links[idx])
                    if action == "d":
                        result = do_delete(idx)
                        states[idx] = "deleted" if result == "deleted" else None
                        messages[idx] = "Link removed." if result == "deleted" else result
                    elif action == "b":
                        result = do_broken(idx)
                        states[idx] = "broken" if result == "broken" else None
                        messages[idx] = "Marked [[broken-link|…]]." if result == "broken" else result
                    if action in ("d", "b"):
                        # After acting, advance to next unhandled
                        next_idx = next((i for i in range(idx + 1, n) if states[i] is None), None)
                        if next_idx is not None:
                            idx = next_idx
                            selected = idx
                            continue
                    elif action == "next":
                        if idx < n - 1:
                            idx += 1
                            selected = idx
                            continue
                    elif action == "prev":
                        if idx > 0:
                            idx -= 1
                            selected = idx
                            continue
                    selected = idx
                    break
            elif key in (ord("d"), ord("D")):
                if states[selected] is None:
                    result = do_delete(selected)
                    states[selected] = "deleted" if result == "deleted" else None
                    messages[selected] = "Link removed." if result == "deleted" else result
            elif key in (ord("b"), ord("B")):
                if states[selected] is None:
                    result = do_broken(selected)
                    states[selected] = "broken" if result == "broken" else None
                    messages[selected] = "Marked [[broken-link|…]]." if result == "broken" else result

    _curses.wrapper(curses_main)

    deleted = sum(1 for s in states if s == "deleted")
    broken_count = sum(1 for s in states if s == "broken")
    skipped = n - deleted - broken_count
    print(f"\nSession complete: {deleted} deleted, {broken_count} marked broken, {skipped} skipped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        prog="check-broken-links.py",
        description=(
            "Scan Markdown files for broken internal and external links.\n"
            "Output is structured JSON (default) or human-readable text, "
            "designed for AI consumption.\n\n"
            "Pass --check to run a scan; invoking with no arguments prints this help."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check vault rooted at the script's parent directory:
  python3 check-broken-links.py --check

  # Check a specific vault directory:
  python3 check-broken-links.py --check /path/to/vault

  # Human-readable output:
  python3 check-broken-links.py --check --format text

  # Include external HTTP link checks:
  python3 check-broken-links.py --check --external --timeout 10

  # Include image embeds in checks:
  python3 check-broken-links.py --check --include-images

  # Skip frontmatter links (e.g. author: [[Name]] in raw/clips):
  python3 check-broken-links.py --check --skip-frontmatter

  # Show suggested fixes for broken wikilinks, then apply them:
  python3 check-broken-links.py --check --format text
  python3 check-broken-links.py --check --fix-simple-errors

  # Combine options:
  python3 check-broken-links.py --check --external --include-images --skip-frontmatter --format text /path/to/vault
        """,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the broken-link scan (required; without this flag the help is shown)",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="Root directory of the vault (default: parent of this script's directory)",
    )
    parser.add_argument(
        "--external",
        action="store_true",
        help="Also check HTTP/HTTPS links (requires network access; slow)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        metavar="N",
        help="Timeout in seconds for external HTTP requests (default: 5)",
    )
    parser.add_argument(
        "--include-images",
        action="store_true",
        help="Also check embedded image links (![[...]] and ![alt](...))",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format: 'json' for AI (default), 'text' for humans",
    )
    parser.add_argument(
        "--skip-frontmatter",
        action="store_true",
        help="Do not check links inside YAML frontmatter (useful to ignore author/tag references)",
    )
    parser.add_argument(
        "--remove-broken-links", "--remove",
        action="store_true",
        dest="remove_broken_links",
        help=(
            "Rewrite broken wikilinks in-place to mark them visually. "
            "[[broken]] becomes [[broken|(broken link) broken]] and "
            "[[broken|text]] becomes [[broken|(broken link) text]], "
            "preserving the original target while flagging it in the display text."
        ),
    )
    parser.add_argument(
        "--fix-simple-errors", "--fix",
        action="store_true",
        dest="fix_simple_errors",
        help=(
            "Rewrite broken wikilinks where a unique normalized match is found. "
            "Characters like ':' are often replaced by '_' in filenames or omitted "
            "in link text; this flag repairs such mismatches in-place."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages written to stderr",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "After scanning, open an interactive TUI to fix broken links one by one. "
            "Use UP/DOWN to navigate, d to delete the link, b to mark it as [[broken-link|…]]."
        ),
    )
    return parser, parser.parse_args()


def main():
    parser, args = parse_args()

    if not any([args.check, args.interactive, args.fix_simple_errors, args.remove_broken_links]):
        parser.print_help()
        sys.exit(0)

    # Determine root directory
    if args.root:
        root = Path(args.root).resolve()
    else:
        # Default: parent of the 'scripts' directory (i.e., the vault root)
        script_dir = Path(__file__).resolve().parent
        if script_dir.name == "scripts":
            root = script_dir.parent
        else:
            root = script_dir

    if not root.exists():
        msg = f"Error: directory does not exist: {root}"
        if args.format == "json":
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)

    if not root.is_dir():
        msg = f"Error: not a directory: {root}"
        if args.format == "json":
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)

    try:
        result = check_vault(root, args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        msg = f"Unexpected error: {e}"
        if args.format == "json":
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)

    if getattr(args, "fix_simple_errors", False) and not args.interactive:
        result["broken_links"] = [b for b in result["broken_links"] if "suggested_fix" in b or b.get("fm_deleted")]
        result["summary"]["broken"] = len(result["broken_links"])

    if args.interactive:
        run_interactive(result["broken_links"], root)
    elif args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_text(result))

    # Exit code: 0 = no broken links, 1 = broken links found, 2 = errors
    if result.get("errors"):
        sys.exit(2)
    if result["summary"]["broken"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
