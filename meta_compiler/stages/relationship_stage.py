"""Phase C1b apply-relationships: merge relationship-mapper proposals into v2.

Two CLI entrypoints live here:

* `run_propose_relationships` (`meta-compiler propose-relationships`) — pure
  prep. Walks v2 pages and citation index to write a request file the
  `relationship-mapper` agent consumes.
* `run_apply_relationships` (`meta-compiler apply-relationships`) — reads
  `wiki/reports/relationship_proposals.yaml` (produced by the agent), merges
  accepted proposals into each affected v2 page's `## Relationships` section
  and `related:` frontmatter, with provenance, and registers every write in
  the v2 edit manifest.

A proposal must cite at least 2 distinct citation IDs (cross-document by
construction) to be accepted. Single-source proposals are rejected with a
warning since per-source relationships are already captured at ingest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths, ensure_layout
from ..io import dump_yaml, load_yaml, parse_frontmatter, render_frontmatter
from ..utils import iso_now, read_text_safe
from .. import wiki_edit_manifest


VALID_RELATIONSHIP_TYPES = {
    "prerequisite_for",
    "depends_on",
    "contradicts",
    "extends",
}


def _v2_concept_pages(paths: ArtifactPaths) -> list[Path]:
    if not paths.wiki_v2_pages_dir.exists():
        return []
    pages: list[Path] = []
    for path in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        text = read_text_safe(path)
        frontmatter, _ = parse_frontmatter(text)
        if frontmatter.get("type") and str(frontmatter["type"]) != "concept":
            continue
        pages.append(path)
    return pages


def _index_concept_pages(pages: list[Path]) -> list[dict[str, str]]:
    index: list[dict[str, str]] = []
    for page in pages:
        text = read_text_safe(page)
        frontmatter, body = parse_frontmatter(text)
        page_id = str(frontmatter.get("id") or page.stem)
        display = page_id
        for line in body.splitlines():
            if line.startswith("# "):
                display = line[2:].strip() or page_id
                break
        index.append(
            {
                "id": page_id,
                "file": page.name,
                "display_name": display,
                "sources": [
                    str(item)
                    for item in (frontmatter.get("sources") or [])
                    if str(item).strip()
                ],
            }
        )
    return index


def run_propose_relationships(
    artifacts_root: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Write the relationship-mapper agent's request file."""
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    pages = _v2_concept_pages(paths)
    if not pages:
        return {
            "status": "no_pages",
            "request_path": None,
        }

    index = _index_concept_pages(pages)
    request = {
        "relationship_mapper_request": {
            "version": 1,
            "generated_at": iso_now(),
            "wiki_version": 2,
            "v2_pages_dir": str(paths.wiki_v2_pages_dir.relative_to(paths.root).as_posix()),
            "citation_index_path": str(
                paths.citations_index_path.relative_to(paths.root).as_posix()
            ),
            "concept_pages": index,
            "valid_relationship_types": sorted(VALID_RELATIONSHIP_TYPES),
            "proposals_output_path": "wiki/reports/relationship_proposals.yaml",
        }
    }
    request_dir = paths.runtime_dir / "wiki_relationships"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / "request.yaml"
    dump_yaml(request_path, request)

    return {
        "status": "ready_for_agent",
        "concept_pages": len(index),
        "request_path": str(request_path.relative_to(paths.root).as_posix()),
        "instruction": (
            "Invoke the relationship-mapper agent. It reads this request, "
            "scans every v2 concept page + the citation index, and writes "
            "wiki/reports/relationship_proposals.yaml. Then run "
            "`meta-compiler apply-relationships --version 2`."
        ),
    }


def _validate_proposal(
    proposal: dict[str, Any],
    *,
    valid_page_ids: set[str],
) -> list[str]:
    issues: list[str] = []
    if not isinstance(proposal, dict):
        return ["proposal must be an object"]
    subject = str(proposal.get("subject") or "").strip()
    target = str(proposal.get("target") or "").strip()
    rel_type = str(proposal.get("relationship_type") or "").strip()
    evidence = proposal.get("evidence")

    if not subject:
        issues.append("missing subject")
    elif subject not in valid_page_ids:
        issues.append(f"subject {subject!r} is not a known v2 page id")
    if not target:
        issues.append("missing target")
    elif target not in valid_page_ids:
        issues.append(f"target {target!r} is not a known v2 page id")
    if subject and target and subject == target:
        issues.append("subject and target must differ")
    if rel_type not in VALID_RELATIONSHIP_TYPES:
        issues.append(
            f"relationship_type {rel_type!r} not in {sorted(VALID_RELATIONSHIP_TYPES)}"
        )
    if not isinstance(evidence, list) or len(evidence) < 2:
        issues.append("evidence must be a list of at least 2 entries")
    else:
        citation_ids: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("citation_id") or "").strip()
            if cid:
                citation_ids.add(cid)
        if len(citation_ids) < 2:
            issues.append(
                "evidence must cite at least 2 distinct citation_ids "
                "(cross-document required)"
            )
    return issues


def _split_relationships_section(body: str) -> tuple[list[str], int, int]:
    """Return (existing_section_lines, start_line, end_line) for ## Relationships.

    end_line is exclusive. Returns ([], -1, -1) if not found.
    """
    lines = body.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip() == "## Relationships":
            start = idx
            break
    if start == -1:
        return [], -1, -1
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break
    return lines[start:end], start, end


def _bucket_existing(section_lines: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {key: [] for key in VALID_RELATIONSHIP_TYPES}
    current: str | None = None
    for line in section_lines:
        stripped = line.strip()
        for key in VALID_RELATIONSHIP_TYPES:
            prefix_a = f"- {key}:"
            if stripped.startswith(prefix_a):
                current = key
                tail = stripped[len(prefix_a):].strip()
                if tail and tail != "[]":
                    # Inline list: "- prerequisite_for: [a, b]" — split.
                    if tail.startswith("[") and tail.endswith("]"):
                        items = [
                            item.strip()
                            for item in tail[1:-1].split(",")
                            if item.strip()
                        ]
                        buckets[key].extend(items)
                break
        else:
            if current and stripped.startswith("- "):
                buckets[current].append(stripped[2:].strip())
    return buckets


def _render_relationships_section(buckets: dict[str, list[str]]) -> list[str]:
    lines = ["## Relationships"]
    for key in ["prerequisite_for", "depends_on", "contradicts", "extends"]:
        items = buckets.get(key, [])
        if not items:
            lines.append(f"- {key}: []")
            continue
        lines.append(f"- {key}:")
        for item in items:
            lines.append(f"  - {item}")
    return lines


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _apply_to_page(
    page_path: Path,
    proposals: list[dict[str, Any]],
    *,
    provenance_path: Path,
) -> tuple[bool, dict[str, Any]]:
    """Merge accepted proposals into one page. Returns (changed, per_page_report)."""
    text = read_text_safe(page_path)
    frontmatter, body = parse_frontmatter(text)

    existing_lines, start, end = _split_relationships_section(body)
    buckets = _bucket_existing(existing_lines) if existing_lines else {
        key: [] for key in VALID_RELATIONSHIP_TYPES
    }

    added: list[dict[str, Any]] = []
    related_added: list[str] = []
    for proposal in proposals:
        rel_type = str(proposal["relationship_type"])
        target = str(proposal["target"])
        bucket = buckets.setdefault(rel_type, [])
        if target not in bucket:
            bucket.append(target)
            added.append(
                {
                    "relationship_type": rel_type,
                    "target": target,
                    "proposed_by": "relationship-mapper",
                    "evidence": proposal.get("evidence", []),
                }
            )
            related_added.append(target)

    if not added:
        return False, {
            "page": page_path.name,
            "added": 0,
            "related_added": [],
        }

    # Rebuild body.
    new_section = _render_relationships_section(buckets)
    if start == -1:
        # No existing section — append before final blank lines.
        new_body = body.rstrip() + "\n\n" + "\n".join(new_section) + "\n"
    else:
        body_lines = body.splitlines()
        new_body = "\n".join(body_lines[:start] + new_section + body_lines[end:])
        if not new_body.endswith("\n"):
            new_body += "\n"

    # Update related: frontmatter.
    existing_related = frontmatter.get("related") or []
    if not isinstance(existing_related, list):
        existing_related = []
    merged_related = _ordered_unique(
        [str(item) for item in existing_related] + related_added
    )
    frontmatter["related"] = merged_related

    new_text = "---\n" + render_frontmatter(frontmatter) + "\n---\n" + new_body
    page_path.write_text(new_text, encoding="utf-8")

    # Provenance log.
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance = load_yaml(provenance_path)
    if not isinstance(provenance, dict):
        provenance = {}
    log = provenance.setdefault("relationship_provenance", {"entries": []})
    if not isinstance(log.get("entries"), list):
        log["entries"] = []
    log["entries"].append(
        {
            "page": page_path.name,
            "applied_at": iso_now(),
            "added": added,
        }
    )
    dump_yaml(provenance_path, provenance)

    return True, {
        "page": page_path.name,
        "added": len(added),
        "related_added": related_added,
    }


def run_apply_relationships(
    artifacts_root: Path,
    workspace_root: Path | None = None,
    version: int = 2,
) -> dict[str, Any]:
    """Merge relationship-mapper proposals into v2 pages."""
    if version != 2:
        raise ValueError(
            f"apply-relationships only supports --version 2, got {version}."
        )

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    proposals_path = paths.reports_dir / "relationship_proposals.yaml"
    if not proposals_path.exists():
        return {
            "status": "no_proposals",
            "proposals_path": str(
                proposals_path.relative_to(paths.root).as_posix()
            ),
            "applied": 0,
            "rejected": 0,
        }

    raw = load_yaml(proposals_path)
    root = raw.get("relationship_proposals") if isinstance(raw, dict) else None
    proposals = root.get("proposals", []) if isinstance(root, dict) else []
    if not isinstance(proposals, list):
        proposals = []

    pages = _v2_concept_pages(paths)
    page_index = {
        str(parse_frontmatter(read_text_safe(p))[0].get("id") or p.stem): p
        for p in pages
    }
    valid_page_ids = set(page_index.keys())

    accepted_by_subject: dict[str, list[dict[str, Any]]] = {}
    rejected: list[dict[str, Any]] = []
    for proposal in proposals:
        issues = _validate_proposal(proposal, valid_page_ids=valid_page_ids)
        if issues:
            rejected.append({"proposal": proposal, "reasons": issues})
            continue
        subject = str(proposal["subject"])
        accepted_by_subject.setdefault(subject, []).append(proposal)

    per_page_report: list[dict[str, Any]] = []
    writes: list[tuple[Path, str]] = []
    pages_changed = 0
    total_added = 0

    provenance_path = paths.reports_dir / "relationship_provenance.yaml"
    for subject, subject_proposals in accepted_by_subject.items():
        page_path = page_index[subject]
        changed, report = _apply_to_page(
            page_path, subject_proposals, provenance_path=provenance_path
        )
        per_page_report.append(report)
        if changed:
            pages_changed += 1
            total_added += report["added"]
            writes.append((page_path, "relationship_mapper"))

    if writes:
        wiki_edit_manifest.record_writes(paths, writes)

    summary = {
        "apply_relationships_report": {
            "timestamp": iso_now(),
            "wiki_version": version,
            "proposals_total": len(proposals),
            "applied": total_added,
            "pages_changed": pages_changed,
            "rejected": len(rejected),
            "per_page": per_page_report,
            "rejections": rejected,
        }
    }
    summary_path = paths.reports_dir / "apply_relationships_report.yaml"
    dump_yaml(summary_path, summary)

    return {
        "status": "ok",
        "applied": total_added,
        "pages_changed": pages_changed,
        "rejected": len(rejected),
        "report_path": str(summary_path.relative_to(paths.root).as_posix()),
    }


__all__ = [
    "run_propose_relationships",
    "run_apply_relationships",
    "VALID_RELATIONSHIP_TYPES",
]
