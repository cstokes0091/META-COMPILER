"""Phase C1b deterministic linker: insert inline links to sibling concept pages.

Pure Python — no LLM. Idempotent. Operates only on v2.

Algorithm
---------
1. Load every v2 concept page; build an index of `display_name -> file`.
2. For each page, walk the body line-by-line:
   - Skip frontmatter, code blocks (``` fences and indented), equation blocks
     (lines starting with `$$`), HTML/markdown link targets, and existing
     Markdown links `[text](target)`.
   - On the first occurrence per page-section (heading-bounded) of any other
     concept's `display_name` (case-insensitive whole-word match, with simple
     plural/possessive handling), wrap that occurrence in
     `[display_name](target.md)`.
3. Update the page's `related:` frontmatter to be the union of (existing,
   newly linked targets minus the page's own id).
4. If anything changed, write back and call
   `wiki_edit_manifest.record_write(paths, page_path, "wiki_linker")`.

Returns a structured report listing per-page link counts and `related:` deltas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths, build_paths, ensure_layout
from .io import dump_yaml, parse_frontmatter, render_frontmatter
from .utils import iso_now, read_text_safe
from . import wiki_edit_manifest


# Match an existing markdown link `[text](target)` so we can avoid linking
# inside one. Anchors at start of segment.
_EXISTING_LINK_RE = re.compile(r"\[[^\]]+\]\([^\)]+\)")


@dataclass(frozen=True)
class _ConceptEntry:
    page_id: str
    file: str
    display_name: str


def _load_concept_index(paths: ArtifactPaths) -> list[_ConceptEntry]:
    entries: list[_ConceptEntry] = []
    if not paths.wiki_v2_pages_dir.exists():
        return entries
    for page in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        text = read_text_safe(page)
        frontmatter, body = parse_frontmatter(text)
        page_type = str(frontmatter.get("type") or "")
        if page_type == "alias":
            # Alias stubs never participate in linking; their aliases flow
            # through the canonical page's `aliases:` frontmatter instead.
            continue
        if page_type and page_type != "concept":
            continue
        page_id = str(frontmatter.get("id") or page.stem)
        display = page_id
        for line in body.splitlines():
            if line.startswith("# "):
                display = line[2:].strip() or page_id
                break
        entries.append(
            _ConceptEntry(page_id=page_id, file=page.name, display_name=display)
        )
        # Secondary entries for every alias declared on the canonical page.
        aliases = frontmatter.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                alias_display = alias.strip()
                if not alias_display or alias_display == display:
                    continue
                entries.append(
                    _ConceptEntry(
                        page_id=page_id,
                        file=page.name,
                        display_name=alias_display,
                    )
                )
    return entries


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_match_pattern(display_name: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive, allows trailing 's' or "'s"."""
    escaped = re.escape(display_name)
    # \b on both sides; allow trailing 's', 'es', or "'s" without breaking
    # the match boundary.
    pattern = rf"(?<![A-Za-z0-9_])({escaped})((?:'s|s|es)?)(?![A-Za-z0-9_])"
    return re.compile(pattern, re.IGNORECASE)


def _link_line(
    line: str,
    *,
    candidates: list[_ConceptEntry],
    own_page_id: str,
    already_linked_in_section: set[str],
) -> tuple[str, list[str]]:
    """Insert links into one line. Returns (new_line, newly_linked_files)."""
    if not line.strip():
        return line, []

    # Track existing-link spans to avoid linking inside them.
    protected: list[tuple[int, int]] = [m.span() for m in _EXISTING_LINK_RE.finditer(line)]

    def is_protected(start: int, end: int) -> bool:
        return any(p_start <= start and end <= p_end for p_start, p_end in protected)

    # Process candidates in deterministic order — longest display name first
    # so "wiki page index" wins over "page".
    ordered = sorted(candidates, key=lambda e: (-len(e.display_name), e.file))
    newly_linked: list[str] = []
    new_line = line

    for entry in ordered:
        if entry.page_id == own_page_id:
            continue
        if entry.file in already_linked_in_section:
            continue
        pattern = _build_match_pattern(entry.display_name)
        match = pattern.search(new_line)
        if not match:
            continue
        if is_protected(match.start(), match.end()):
            continue
        # Replace only this first occurrence.
        replacement = f"[{match.group(1)}{match.group(2)}]({entry.file})"
        new_line = new_line[: match.start()] + replacement + new_line[match.end() :]
        already_linked_in_section.add(entry.file)
        newly_linked.append(entry.file)
        # Recompute protected spans because indices shifted.
        protected = [m.span() for m in _EXISTING_LINK_RE.finditer(new_line)]

    return new_line, newly_linked


def _link_body(
    body: str,
    *,
    candidates: list[_ConceptEntry],
    own_page_id: str,
) -> tuple[str, list[str]]:
    """Walk the body line-by-line, linking first mentions per section."""
    out_lines: list[str] = []
    linked_files: list[str] = []
    in_code_fence = False
    in_equation_block = False
    already_linked_in_section: set[str] = set()

    for raw_line in body.splitlines():
        line = raw_line

        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
            out_lines.append(line)
            continue
        if line.strip().startswith("$$"):
            in_equation_block = not in_equation_block
            out_lines.append(line)
            continue
        if in_code_fence or in_equation_block:
            out_lines.append(line)
            continue
        # Indented (4+ space) code blocks.
        if line.startswith("    ") and line.strip():
            out_lines.append(line)
            continue
        # Section boundaries reset the per-section dedupe set so each section
        # gets one link per concept.
        if line.startswith("## ") or line.startswith("# "):
            already_linked_in_section = set()
            out_lines.append(line)
            continue

        new_line, newly = _link_line(
            line,
            candidates=candidates,
            own_page_id=own_page_id,
            already_linked_in_section=already_linked_in_section,
        )
        if newly:
            linked_files.extend(newly)
        out_lines.append(new_line)

    rebuilt = "\n".join(out_lines)
    if body.endswith("\n") and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt, _ordered_unique(linked_files)


def _related_targets_from_links(
    linked_files: list[str], all_pages: list[_ConceptEntry]
) -> list[str]:
    file_to_id = {entry.file: entry.page_id for entry in all_pages}
    return _ordered_unique(
        [file_to_id[f] for f in linked_files if f in file_to_id]
    )


def _merge_related(existing: Any, new_ids: list[str]) -> tuple[list[str], list[str]]:
    base: list[str] = []
    if isinstance(existing, list):
        base = [str(item) for item in existing if str(item).strip()]
    merged = _ordered_unique(base + new_ids)
    added = [item for item in new_ids if item not in base]
    return merged, added


def run_wiki_link(
    artifacts_root: Path,
    workspace_root: Path | None = None,
    version: int = 2,
) -> dict[str, Any]:
    """Insert inline concept links into every v2 page; update related: frontmatter.

    Idempotent — running twice yields no further writes.
    """
    if version != 2:
        raise ValueError(
            f"wiki-link only supports --version 2, got {version}. "
            "v1 stays templated."
        )

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    candidates = _load_concept_index(paths)
    if not candidates:
        return {
            "status": "no_pages",
            "version": version,
            "pages_changed": 0,
            "links_inserted": 0,
            "report_path": None,
        }

    per_page: list[dict[str, Any]] = []
    pages_changed = 0
    total_links = 0
    writes: list[tuple[Path, str]] = []

    for page in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        text = read_text_safe(page)
        frontmatter, body = parse_frontmatter(text)
        if frontmatter.get("type") and str(frontmatter["type"]) != "concept":
            continue
        own_page_id = str(frontmatter.get("id") or page.stem)

        new_body, linked_files = _link_body(
            body, candidates=candidates, own_page_id=own_page_id
        )

        new_related_ids = _related_targets_from_links(linked_files, candidates)
        merged_related, added_related = _merge_related(
            frontmatter.get("related"), new_related_ids
        )

        # Only rewrite the file when something semantically changed. Comparing
        # body strings is unreliable because splitlines + join can normalize
        # trailing newlines.
        if not linked_files and not added_related:
            per_page.append(
                {
                    "page": page.name,
                    "links_inserted": 0,
                    "linked_files": [],
                    "related_added": [],
                    "changed": False,
                }
            )
            continue

        if added_related:
            frontmatter["related"] = merged_related

        new_text = (
            "---\n"
            + render_frontmatter(frontmatter)
            + "\n---\n"
            + new_body
        )
        if not new_text.endswith("\n"):
            new_text += "\n"
        page.write_text(new_text, encoding="utf-8")
        writes.append((page, "wiki_linker"))
        pages_changed += 1
        total_links += len(linked_files)
        per_page.append(
            {
                "page": page.name,
                "links_inserted": len(linked_files),
                "linked_files": linked_files,
                "related_added": added_related,
                "changed": True,
            }
        )

    if writes:
        wiki_edit_manifest.record_writes(paths, writes)

    report = {
        "wiki_linking_report": {
            "timestamp": iso_now(),
            "wiki_version": version,
            "pages_considered": len(per_page),
            "pages_changed": pages_changed,
            "links_inserted": total_links,
            "per_page": per_page,
        }
    }
    report_path = paths.reports_dir / "wiki_linking_report.yaml"
    dump_yaml(report_path, report)

    return {
        "status": "ok",
        "version": version,
        "pages_considered": len(per_page),
        "pages_changed": pages_changed,
        "links_inserted": total_links,
        "report_path": str(report_path.relative_to(paths.root).as_posix()),
    }


__all__ = ["run_wiki_link"]
