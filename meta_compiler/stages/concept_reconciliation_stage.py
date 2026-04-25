"""Semantic wiki enrichment: concept reconciliation + cross-source synthesis.

Replaces the legacy `wiki-update` command. Three CLI-facing entry points:

* `run_wiki_reconcile_concepts` — Phase A preflight. Reads every findings
  JSON, flattens every `concepts[]` record into candidate tuples, buckets by
  normalized-stem, writes a work plan + reconcile_request.yaml for the
  `wiki-concept-reconciliation` orchestrator prompt.

* `run_wiki_apply_reconciliation` — Phase A postflight. Consumes the
  orchestrator's `concept_reconciliation_v{N}.yaml` proposal; promotes one
  page per alias group to canonical, rewrites member pages as `type: alias`
  redirect stubs, stamps every write via the edit manifest
  (`source: concept_reconciliation`).

* `run_wiki_cross_source_synthesize` — Phase B preflight. For every canonical
  concept page backed by >=2 distinct sources, builds a per-page work item
  containing all findings records for the concept and its aliases. The
  orchestrator prompt writes reconciled sections back through the edit
  manifest with `source: cross_source_synthesis`.

The structured findings JSON is the authoritative signal source. The lexical
linker (`wiki_linking.py`) is left untouched aside from a small index upgrade
to pick up aliases.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import wiki_edit_manifest
from ..artifacts import ArtifactPaths, build_paths, ensure_layout
from ..io import dump_yaml, load_yaml, parse_frontmatter, render_frontmatter
from ..utils import iso_now, read_text_safe, slugify
from ..validation import (
    validate_concept_reconciliation_proposal,
    validate_concept_reconciliation_return,
    validate_cross_source_synthesis_return,
)


RELATIONSHIPS_TEMPLATE = [
    "## Relationships",
    "- prerequisite_for: []",
    "- depends_on: []",
    "- contradicts: []",
    "- extends: []",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _coerce_version(version: int | None, default: int = 2) -> int:
    if version is None:
        return default
    if version != 2:
        raise ValueError(
            f"wiki reconciliation only supports version=2, got {version}"
        )
    return version


def _load_findings_payloads(paths: ArtifactPaths) -> list[tuple[Path, dict[str, Any]]]:
    payloads: list[tuple[Path, dict[str, Any]]] = []
    if not paths.findings_dir.exists():
        return payloads
    for findings_path in sorted(paths.findings_dir.glob("*.json")):
        try:
            payload = json.loads(findings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append((findings_path, payload))
    return payloads


def _concept_stem(name: str) -> str:
    """Strip very common English suffixes after slugifying.

    Buckets "Johnson noise", "Johnson noises", "Johnson-noise" onto the same
    stem key so the reconciler subagent sees them together. Deliberately
    cheap — semantic judgment still rests with the LLM.
    """
    slug = slugify(name)
    if not slug:
        return ""
    for suffix in ("ization", "isation", "iness", "ness", "ments", "ment", "ings", "ing", "ies", "ed", "es", "s"):
        if len(slug) > len(suffix) + 2 and slug.endswith(suffix):
            return slug[: -len(suffix)]
    return slug


_BUCKET_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with",
}


def _bucket_keys(name: str) -> list[str]:
    """Return every significant stemmed word in a concept name.

    A candidate lands in one bucket per word so that "Johnson noise" and
    "thermal noise" co-bucket on `noise`, letting the LLM reconciler judge
    whether they're the same concept. Single-word candidates still return a
    single key. Duplicate keys are collapsed.
    """
    slug = slugify(name)
    if not slug:
        return ["unbucketed"]
    keys: list[str] = []
    seen: set[str] = set()
    for word in slug.split("-"):
        if not word or word in _BUCKET_STOPWORDS:
            continue
        stemmed = _concept_stem(word)
        if stemmed and stemmed not in seen:
            seen.add(stemmed)
            keys.append(stemmed)
    return keys or ["unbucketed"]


def _flatten_concept_candidates(
    findings: list[tuple[Path, dict[str, Any]]]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for findings_path, payload in findings:
        citation_id = str(payload.get("citation_id") or "src-unknown")
        seed_path = str(payload.get("seed_path") or findings_path.name)
        for concept in payload.get("concepts", []) or []:
            if not isinstance(concept, dict):
                continue
            name = str(concept.get("name") or "").strip()
            if not name:
                continue
            candidates.append(
                {
                    "name": name,
                    "stem": _concept_stem(name),
                    "definition": str(concept.get("definition") or "").strip(),
                    "importance": str(concept.get("importance") or "").strip(),
                    "first_mention": concept.get("first_mention") or {},
                    "source_citation_id": citation_id,
                    "source_path": seed_path,
                    "findings_path": str(findings_path),
                }
            )
    return candidates


def _canonical_page_id_for(name: str) -> str:
    slug = slugify(name)
    if not slug:
        return "concept-unknown"
    if slug.startswith("concept-"):
        return slug
    return f"concept-{slug}"


def _load_page(page_path: Path) -> tuple[dict[str, Any], str] | None:
    if not page_path.exists():
        return None
    text = read_text_safe(page_path)
    frontmatter, body = parse_frontmatter(text)
    return frontmatter, body


def _write_page(page_path: Path, frontmatter: dict[str, Any], body: str) -> None:
    rendered = "---\n" + render_frontmatter(frontmatter) + "\n---\n" + body
    if not rendered.endswith("\n"):
        rendered += "\n"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(rendered, encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase A preflight: reconcile-concepts
# ---------------------------------------------------------------------------


def run_wiki_reconcile_concepts(
    artifacts_root: Path,
    workspace_root: Path,
    version: int | None = 2,
) -> dict[str, Any]:
    """Write the work plan + reconcile_request for the reconciler orchestrator."""
    _coerce_version(version)
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings = _load_findings_payloads(paths)
    candidates = _flatten_concept_candidates(findings)

    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        for key in _bucket_keys(candidate["name"]):
            buckets.setdefault(key, []).append(candidate)

    # Drop buckets with only one candidate — no reconciliation work to do.
    work_items: list[dict[str, Any]] = []
    skipped_singletons = 0
    for key in sorted(buckets.keys()):
        records = buckets[key]
        if len(records) < 2:
            skipped_singletons += 1
            continue
        unique_citations = sorted({r["source_citation_id"] for r in records})
        # Only reconcile when the candidates come from >1 source. Same-source
        # duplicates are usually the same concept written twice and don't need
        # cross-source judgment.
        if len(unique_citations) < 2:
            skipped_singletons += 1
            continue
        work_items.append(
            {
                "bucket_key": key,
                "candidate_count": len(records),
                "source_citation_ids": unique_citations,
                "candidates": records,
            }
        )

    generated_at = iso_now()
    plan = {
        "wiki_concept_reconciliation_work_plan": {
            "generated_at": generated_at,
            "version": version or 2,
            "total_candidates": len(candidates),
            "bucket_count": len(buckets),
            "work_item_count": len(work_items),
            "skipped_singleton_buckets": skipped_singletons,
            "work_items": work_items,
            "proposal_output_path": str(
                paths.reports_dir / f"concept_reconciliation_v{version or 2}.yaml"
            ),
            "edit_manifest_path": str(wiki_edit_manifest.manifest_path(paths)),
        }
    }
    dump_yaml(paths.wiki_reconcile_work_plan_path, plan)

    request = {
        "wiki_reconcile_request": {
            "generated_at": generated_at,
            "version": version or 2,
            "work_plan_path": str(paths.wiki_reconcile_work_plan_path),
            "proposal_output_path": str(
                paths.reports_dir / f"concept_reconciliation_v{version or 2}.yaml"
            ),
            "candidate_count": len(candidates),
            "work_item_count": len(work_items),
        }
    }
    dump_yaml(paths.wiki_reconcile_request_path, request)

    return {
        "status": "ready_for_orchestrator" if work_items else "no_candidates",
        "work_plan_path": str(paths.wiki_reconcile_work_plan_path),
        "request_path": str(paths.wiki_reconcile_request_path),
        "candidate_count": len(candidates),
        "bucket_count": len(buckets),
        "work_item_count": len(work_items),
        "skipped_singleton_buckets": skipped_singletons,
    }


# ---------------------------------------------------------------------------
# Phase A postflight: apply-reconciliation
# ---------------------------------------------------------------------------


def _load_proposal(paths: ArtifactPaths, version: int) -> dict[str, Any]:
    """Load and validate the reconciliation proposal.

    Tries the canonical YAML at `wiki/reports/concept_reconciliation_v{N}.yaml`
    first. If absent, synthesizes a proposal from per-bucket subagent JSON
    returns under `runtime/wiki_reconcile/subagent_returns/` and writes the
    synthesized proposal back to the canonical location for audit. Either
    way, the resulting proposal is validated via
    `validate_concept_reconciliation_proposal`; malformed proposals raise
    `ValueError` aggregating the validator's issues.
    """
    proposal_path = paths.reports_dir / f"concept_reconciliation_v{version}.yaml"
    payload: dict[str, Any] | None = None

    if proposal_path.exists():
        loaded = load_yaml(proposal_path)
        if isinstance(loaded, dict):
            payload = loaded
    if payload is None:
        payload = _synthesize_proposal_from_subagent_returns(paths, version)
        if payload is None:
            raise FileNotFoundError(
                f"Reconciliation proposal missing: {proposal_path}. "
                "Either run the wiki-concept-reconciliation prompt (which "
                "writes the proposal) or persist subagent returns to "
                f"{paths.wiki_reconcile_subagent_returns_dir} for the CLI "
                "to assemble."
            )
        # Persist the synthesized proposal so the audit trail includes it.
        proposal_path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml(proposal_path, payload)

    issues = validate_concept_reconciliation_proposal(payload)
    if issues:
        raise ValueError(
            "concept_reconciliation_proposal is malformed:\n  - "
            + "\n  - ".join(issues)
        )

    root = payload.get("concept_reconciliation_proposal")
    assert isinstance(root, dict)  # validator guarantees this
    return root


def _synthesize_proposal_from_subagent_returns(
    paths: ArtifactPaths, version: int
) -> dict[str, Any] | None:
    """Assemble a proposal payload from per-bucket subagent JSON returns.

    Returns None when there is no work plan or no subagent_returns to
    consume. Validates each return before merging; raises ValueError with
    aggregated issues if any return is malformed.
    """
    if not paths.wiki_reconcile_work_plan_path.exists():
        return None
    plan = load_yaml(paths.wiki_reconcile_work_plan_path) or {}
    work_plan = plan.get("wiki_concept_reconciliation_work_plan") or {}
    work_items = work_plan.get("work_items") or []
    if not paths.wiki_reconcile_subagent_returns_dir.exists():
        return None
    return_files = sorted(paths.wiki_reconcile_subagent_returns_dir.glob("*.json"))
    if not return_files:
        return None

    bucket_expected: dict[str, set[str]] = {}
    for item in work_items:
        if not isinstance(item, dict):
            continue
        bucket_key = str(item.get("bucket_key") or "").strip()
        citations = item.get("source_citation_ids") or []
        bucket_expected[bucket_key] = {
            str(c) for c in citations if isinstance(c, str)
        }

    aggregated_alias_groups: list[dict[str, Any]] = []
    aggregated_distinct: list[dict[str, Any]] = []
    issues: list[str] = []

    for return_file in return_files:
        try:
            payload = json.loads(return_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"{return_file.name}: unreadable ({exc})")
            continue
        if not isinstance(payload, dict):
            issues.append(f"{return_file.name}: must be a JSON object")
            continue
        bucket_key = str(payload.get("bucket_key") or return_file.stem)
        expected = bucket_expected.get(bucket_key, set())
        return_issues = validate_concept_reconciliation_return(
            payload, bucket_key=bucket_key, expected_citation_ids=expected
        )
        if return_issues:
            issues.extend(return_issues)
            continue
        aggregated_alias_groups.extend(payload.get("alias_groups") or [])
        aggregated_distinct.extend(payload.get("distinct_concepts") or [])

    if issues:
        raise ValueError(
            "subagent returns failed validation:\n  - " + "\n  - ".join(issues)
        )

    return {
        "concept_reconciliation_proposal": {
            "generated_at": iso_now(),
            "version": version,
            "alias_groups": aggregated_alias_groups,
            "distinct_concepts": aggregated_distinct,
            "source": "synthesized_from_subagent_returns",
            "subagent_return_count": len(return_files),
        }
    }


def _resolve_member_page(
    paths: ArtifactPaths, member_name: str
) -> Path | None:
    """Find a v2 page whose id matches a member concept name."""
    candidate_id = _canonical_page_id_for(member_name)
    candidate_path = paths.wiki_v2_pages_dir / f"{candidate_id}.md"
    if candidate_path.exists():
        return candidate_path
    # Fall back: scan for frontmatter id match (handles display-name variants).
    lowered = candidate_id.lower()
    for page in paths.wiki_v2_pages_dir.glob("*.md"):
        frontmatter, _ = parse_frontmatter(read_text_safe(page))
        if str(frontmatter.get("id", "")).lower() == lowered:
            return page
    return None


def _merge_list(existing: Any, incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, str):
                continue
            clean = item.strip()
            if clean and clean not in seen:
                merged.append(clean)
                seen.add(clean)
    for item in incoming:
        clean = str(item).strip()
        if clean and clean not in seen:
            merged.append(clean)
            seen.add(clean)
    return merged


def _format_locator(locator: Any) -> str:
    if not isinstance(locator, dict):
        return ""
    parts: list[str] = []
    if locator.get("page") is not None:
        parts.append(f"p.{locator['page']}")
    if locator.get("section"):
        parts.append(f"sec. {locator['section']}")
    if locator.get("paragraph") is not None:
        parts.append(f"para. {locator['paragraph']}")
    if locator.get("file"):
        file_piece = str(locator["file"])
        if locator.get("line_start") is not None:
            file_piece += f":{locator['line_start']}"
            if locator.get("line_end") is not None and locator["line_end"] != locator.get("line_start"):
                file_piece += f"-{locator['line_end']}"
        parts.append(file_piece)
    return f" ({', '.join(parts)})" if parts else ""


def _build_alias_sources_block(members: list[dict[str, Any]]) -> list[str]:
    lines = ["", "### Alias Sources"]
    for member in members:
        name = str(member.get("name") or "").strip() or "(unknown alias)"
        citation = str(member.get("source_citation_id") or "src-unknown")
        locator = _format_locator(member.get("evidence_locator"))
        definition = str(member.get("definition_excerpt") or "").strip()
        header = f"- **{name}** — {citation}{locator}"
        lines.append(header)
        if definition:
            for line in definition.splitlines():
                line = line.strip()
                if line:
                    lines.append(f"  > {line}")
    return lines


def _insert_alias_sources_section(body: str, block: list[str]) -> str:
    """Append an Alias Sources subsection to the Source Notes section.

    If ## Source Notes doesn't exist, append a new section at the end.
    Idempotent: if the Alias Sources subsection already appears, replace it.
    """
    block_text = "\n".join(block).rstrip() + "\n"
    start_marker = "### Alias Sources"
    if "## Source Notes" not in body:
        footer = body
        if not footer.endswith("\n"):
            footer += "\n"
        return footer + "\n## Source Notes\n" + block_text

    # Find the source notes section and any existing alias sources subsection.
    source_idx = body.index("## Source Notes")
    tail = body[source_idx:]
    if start_marker in tail:
        alias_start = body.index(start_marker, source_idx)
        # Find next section heading (## or ### or end of file).
        next_heading_idx = len(body)
        for marker in ("\n## ", "\n### "):
            scan_from = alias_start + len(start_marker)
            idx = body.find(marker, scan_from)
            if idx != -1 and idx < next_heading_idx:
                next_heading_idx = idx
        prefix = body[:alias_start].rstrip("\n")
        suffix = body[next_heading_idx:]
        return f"{prefix}\n\n{block_text.rstrip()}\n{suffix}" if suffix else f"{prefix}\n\n{block_text}"

    # Append subsection at the end of Source Notes.
    next_section_idx = len(body)
    for marker in ("\n## ",):
        scan_from = source_idx + len("## Source Notes")
        idx = body.find(marker, scan_from)
        if idx != -1 and idx < next_section_idx:
            next_section_idx = idx
    prefix = body[:next_section_idx].rstrip("\n")
    suffix = body[next_section_idx:]
    return f"{prefix}\n\n{block_text.rstrip()}\n{suffix}" if suffix else f"{prefix}\n\n{block_text}"


def _build_alias_stub_body(canonical_page_id: str, canonical_display: str) -> str:
    return (
        f"# {canonical_display}\n\n"
        "## Definition\n"
        f"This concept is covered at [{canonical_display}]({canonical_page_id}.md).\n"
    )


def run_wiki_apply_reconciliation(
    artifacts_root: Path,
    workspace_root: Path,
    version: int | None = 2,
) -> dict[str, Any]:
    """Apply a concept_reconciliation_v{N}.yaml proposal to v2 pages."""
    resolved_version = _coerce_version(version)
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    root = _load_proposal(paths, resolved_version)
    alias_groups = root.get("alias_groups") or []
    if not isinstance(alias_groups, list):
        raise ValueError("concept_reconciliation_proposal.alias_groups must be a list")

    writes: list[tuple[Path, str]] = []
    groups_applied: list[dict[str, Any]] = []
    pages_redirected: list[str] = []
    pages_merged: list[str] = []
    skipped_user_edited: list[str] = []

    for group in alias_groups:
        if not isinstance(group, dict):
            continue
        canonical_name = str(group.get("canonical_name") or "").strip()
        members = [m for m in (group.get("members") or []) if isinstance(m, dict)]
        if not canonical_name or not members:
            continue

        canonical_page_id = _canonical_page_id_for(canonical_name)
        canonical_path = paths.wiki_v2_pages_dir / f"{canonical_page_id}.md"

        # If the canonical page doesn't exist yet, create it from the first
        # member's definition as a starting point.
        if not canonical_path.exists():
            seed_member = members[0]
            seed_citation = str(seed_member.get("source_citation_id") or "src-unknown")
            seed_definition = str(seed_member.get("definition_excerpt") or "").strip()
            frontmatter = {
                "id": canonical_page_id,
                "type": "concept",
                "created": iso_now(),
                "sources": [seed_citation],
                "related": [],
                "aliases": [],
                "status": "raw",
            }
            body = (
                f"# {canonical_name}\n\n"
                "## Definition\n"
                f"{seed_definition or 'Canonical concept created during reconciliation.'}\n\n"
                "## Formalism\n"
                "No formalism captured yet.\n\n"
                "## Key Claims\n"
                "- Canonical concept surfaced via cross-source reconciliation.\n\n"
                + "\n".join(RELATIONSHIPS_TEMPLATE)
                + "\n\n"
                "## Open Questions\n"
                "- What inter-source divergence should cross-source synthesis surface?\n\n"
                "## Source Notes\n"
                "- Seed definition captured during reconciliation.\n"
            )
            _write_page(canonical_path, frontmatter, body)

        if wiki_edit_manifest.is_user_edited(paths, canonical_path):
            skipped_user_edited.append(canonical_path.name)
            continue

        frontmatter, body = _load_page(canonical_path)  # type: ignore[misc]

        # Merge member citations + alias display names.
        incoming_sources = [str(m.get("source_citation_id") or "").strip() for m in members]
        incoming_sources = [c for c in incoming_sources if c]
        alias_names = [str(m.get("name") or "").strip() for m in members]
        alias_names = [a for a in alias_names if a and a != canonical_name]

        frontmatter["sources"] = _merge_list(frontmatter.get("sources"), incoming_sources)
        frontmatter["aliases"] = _merge_list(frontmatter.get("aliases"), alias_names)
        frontmatter.setdefault("related", [])
        frontmatter.setdefault("status", "raw")

        alias_block = _build_alias_sources_block(members)
        new_body = _insert_alias_sources_section(body, alias_block)
        _write_page(canonical_path, frontmatter, new_body)
        writes.append((canonical_path, "concept_reconciliation"))
        pages_merged.append(canonical_path.name)

        # Rewrite each member page (if it exists) as an alias redirect stub.
        for member in members:
            member_name = str(member.get("name") or "").strip()
            if not member_name or member_name == canonical_name:
                continue
            member_path = _resolve_member_page(paths, member_name)
            if member_path is None:
                continue
            if member_path.resolve() == canonical_path.resolve():
                continue
            if wiki_edit_manifest.is_user_edited(paths, member_path):
                skipped_user_edited.append(member_path.name)
                continue
            alias_frontmatter = {
                "id": member_path.stem,
                "type": "alias",
                "canonical": canonical_page_id,
                "created": iso_now(),
                "sources": [str(member.get("source_citation_id") or "src-unknown")],
                "related": [canonical_page_id],
                "status": "raw",
            }
            alias_body = _build_alias_stub_body(canonical_page_id, canonical_name)
            _write_page(member_path, alias_frontmatter, alias_body)
            writes.append((member_path, "concept_reconciliation"))
            pages_redirected.append(member_path.name)

        groups_applied.append(
            {
                "canonical_name": canonical_name,
                "canonical_page_id": canonical_page_id,
                "member_count": len(members),
                "member_names": [str(m.get("name") or "") for m in members],
            }
        )

    if writes:
        wiki_edit_manifest.record_writes(paths, writes)

    report_payload = {
        "wiki_reconciliation_applied": {
            "generated_at": iso_now(),
            "version": resolved_version,
            "proposal_path": str(
                paths.reports_dir / f"concept_reconciliation_v{resolved_version}.yaml"
            ),
            "alias_groups_applied": groups_applied,
            "alias_groups_applied_count": len(groups_applied),
            "pages_merged": sorted(set(pages_merged)),
            "pages_redirected": sorted(set(pages_redirected)),
            "skipped_user_edited": sorted(set(skipped_user_edited)),
            "writes": len(writes),
        }
    }
    report_path = paths.reports_dir / f"wiki_reconciliation_applied_v{resolved_version}.yaml"
    dump_yaml(report_path, report_payload)

    return {
        "status": "applied" if groups_applied else "nothing_applied",
        "alias_groups_applied_count": len(groups_applied),
        "pages_merged": sorted(set(pages_merged)),
        "pages_redirected": sorted(set(pages_redirected)),
        "skipped_user_edited": sorted(set(skipped_user_edited)),
        "report_path": str(report_path),
    }


# ---------------------------------------------------------------------------
# Phase B preflight: cross-source-synthesize
# ---------------------------------------------------------------------------


def _concept_names_for_page(
    frontmatter: dict[str, Any], body: str
) -> list[str]:
    """Names a findings record could refer to this page by."""
    names: list[str] = []
    page_id = str(frontmatter.get("id") or "").strip()
    if page_id:
        names.append(page_id)
    # Derive the natural display name from the first `# Heading` in body.
    for line in body.splitlines():
        if line.startswith("# "):
            display = line[2:].strip()
            if display:
                names.append(display)
            break
    aliases = frontmatter.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                names.append(alias.strip())
    return names


def _concept_matches(candidate_name: str, names: list[str]) -> bool:
    slug = _canonical_page_id_for(candidate_name)
    stem = _concept_stem(candidate_name)
    for name in names:
        if not name:
            continue
        if name == candidate_name:
            return True
        if _canonical_page_id_for(name) == slug:
            return True
        if stem and stem == _concept_stem(name):
            return True
    return False


def _collect_findings_records_for_concept(
    findings: list[tuple[Path, dict[str, Any]]],
    names: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for findings_path, payload in findings:
        citation_id = str(payload.get("citation_id") or "src-unknown")
        seed_path = str(payload.get("seed_path") or findings_path.name)
        concept_matches: list[dict[str, Any]] = []
        for concept in payload.get("concepts", []) or []:
            if not isinstance(concept, dict):
                continue
            concept_name = str(concept.get("name") or "").strip()
            if not concept_name:
                continue
            if _concept_matches(concept_name, names):
                concept_matches.append(concept)
        if not concept_matches:
            continue
        matched_names_lower = {str(c.get("name") or "").strip().lower() for c in concept_matches}

        def _mentions(row: Any) -> bool:
            if not isinstance(row, dict):
                return False
            for key in ("concept", "concepts", "from", "to", "topic"):
                value = row.get(key)
                if isinstance(value, str) and value.strip().lower() in matched_names_lower:
                    return True
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip().lower() in matched_names_lower:
                            return True
            return False

        claims = [c for c in payload.get("claims", []) or [] if isinstance(c, dict)]
        quotes = [q for q in payload.get("quotes", []) or [] if isinstance(q, dict)]
        equations = [e for e in payload.get("equations", []) or [] if isinstance(e, dict)]
        relationships = [r for r in payload.get("relationships", []) or [] if _mentions(r)]
        records.append(
            {
                "citation_id": citation_id,
                "seed_path": seed_path,
                "findings_path": str(findings_path),
                "matched_concepts": concept_matches,
                "claims": claims,
                "quotes": quotes,
                "equations": equations,
                "relationships": relationships,
            }
        )
    return records


def run_wiki_cross_source_synthesize(
    artifacts_root: Path,
    workspace_root: Path,
    version: int | None = 2,
) -> dict[str, Any]:
    """Write the work plan for the cross-source synthesizer orchestrator."""
    resolved_version = _coerce_version(version)
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings = _load_findings_payloads(paths)

    work_items: list[dict[str, Any]] = []
    skipped_single_source: list[str] = []
    skipped_user_edited: list[str] = []
    skipped_no_findings: list[str] = []

    for page_path in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        loaded = _load_page(page_path)
        if loaded is None:
            continue
        frontmatter, body = loaded
        if str(frontmatter.get("type") or "") != "concept":
            continue
        sources = frontmatter.get("sources") or []
        citation_ids = sorted({str(s) for s in sources if isinstance(s, str) and s.strip()})
        if len(citation_ids) < 2:
            skipped_single_source.append(page_path.name)
            continue
        if wiki_edit_manifest.is_user_edited(paths, page_path):
            skipped_user_edited.append(page_path.name)
            continue
        names = _concept_names_for_page(frontmatter, body)
        records = _collect_findings_records_for_concept(findings, names)
        covered_citations = {r["citation_id"] for r in records}
        if len(covered_citations) < 2:
            skipped_no_findings.append(page_path.name)
            continue
        work_items.append(
            {
                "page_id": str(frontmatter.get("id") or page_path.stem),
                "page_file": page_path.name,
                "aliases": list(frontmatter.get("aliases") or []),
                "source_citation_ids": citation_ids,
                "covered_citation_ids": sorted(covered_citations),
                "findings_records": records,
            }
        )

    generated_at = iso_now()
    plan = {
        "wiki_cross_source_work_plan": {
            "generated_at": generated_at,
            "version": resolved_version,
            "work_item_count": len(work_items),
            "skipped_single_source": sorted(set(skipped_single_source)),
            "skipped_user_edited": sorted(set(skipped_user_edited)),
            "skipped_no_findings": sorted(set(skipped_no_findings)),
            "work_items": work_items,
            "edit_manifest_path": str(wiki_edit_manifest.manifest_path(paths)),
            "edit_manifest_source": "cross_source_synthesis",
            "subagent_returns_dir": str(paths.wiki_cross_source_subagent_returns_dir),
        }
    }
    dump_yaml(paths.wiki_cross_source_work_plan_path, plan)

    request = {
        "wiki_cross_source_request": {
            "generated_at": generated_at,
            "version": resolved_version,
            "work_plan_path": str(paths.wiki_cross_source_work_plan_path),
            "subagent_returns_dir": str(paths.wiki_cross_source_subagent_returns_dir),
            "work_item_count": len(work_items),
        }
    }
    dump_yaml(paths.wiki_cross_source_request_path, request)

    return {
        "status": "ready_for_orchestrator" if work_items else "no_candidates",
        "work_plan_path": str(paths.wiki_cross_source_work_plan_path),
        "request_path": str(paths.wiki_cross_source_request_path),
        "work_item_count": len(work_items),
        "skipped_single_source": sorted(set(skipped_single_source)),
        "skipped_user_edited": sorted(set(skipped_user_edited)),
        "skipped_no_findings": sorted(set(skipped_no_findings)),
    }


# ---------------------------------------------------------------------------
# Phase B postflight: apply-cross-source-synthesis
# ---------------------------------------------------------------------------


def _replace_section(body: str, heading: str, new_content: str) -> str:
    """Replace the body of `## {heading}` with `new_content`.

    Idempotent — re-applying the same content yields the same body. If the
    heading is missing, append it as a new section at the end of the body.
    Sub-headings (`### ...`) inside the section are preserved up to the
    next `## ` marker.
    """
    marker = f"## {heading}"
    new_text = new_content.rstrip()
    if marker not in body:
        suffix = "" if body.endswith("\n") else "\n"
        return f"{body}{suffix}\n{marker}\n{new_text}\n"

    start = body.index(marker)
    after_heading = body.index("\n", start) + 1
    next_idx = body.find("\n## ", after_heading)
    if next_idx == -1:
        return body[:after_heading] + new_text + "\n"
    # Preserve a single blank line between sections.
    return body[:after_heading] + new_text + "\n" + body[next_idx + 1 :]


def _render_synthesis_body(existing_body: str, payload: dict[str, Any]) -> str:
    """Replace Definition / Key Claims / Open Questions sections with the
    synthesizer's prose, preserving every other section (Formalism,
    Relationships, Source Notes including any `### Alias Sources`
    subsection)."""
    body = existing_body
    body = _replace_section(body, "Definition", str(payload.get("definition") or "").strip())
    body = _replace_section(body, "Key Claims", str(payload.get("key_claims") or "").strip())
    body = _replace_section(
        body, "Open Questions", str(payload.get("open_questions") or "").strip()
    )
    return body


def _load_cross_source_work_plan(paths: ArtifactPaths) -> dict[str, Any]:
    if not paths.wiki_cross_source_work_plan_path.exists():
        raise FileNotFoundError(
            f"Cross-source work plan missing: {paths.wiki_cross_source_work_plan_path}. "
            "Run `meta-compiler wiki-cross-source-synthesize` first."
        )
    payload = load_yaml(paths.wiki_cross_source_work_plan_path) or {}
    plan = payload.get("wiki_cross_source_work_plan")
    if not isinstance(plan, dict):
        raise ValueError(
            "Cross-source work plan missing root key 'wiki_cross_source_work_plan'"
        )
    return plan


def _load_cross_source_returns(paths: ArtifactPaths) -> dict[str, dict[str, Any]]:
    """Read every subagent JSON return as `{page_id: payload}`.

    Falls back to the file stem when a payload omits `page_id`.
    """
    returns: dict[str, dict[str, Any]] = {}
    if not paths.wiki_cross_source_subagent_returns_dir.exists():
        return returns
    for return_file in sorted(paths.wiki_cross_source_subagent_returns_dir.glob("*.json")):
        try:
            payload = json.loads(return_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        page_id = str(payload.get("page_id") or return_file.stem).strip()
        if not page_id:
            continue
        returns[page_id] = payload
    return returns


def run_wiki_apply_cross_source_synthesis(
    artifacts_root: Path,
    workspace_root: Path,
    version: int | None = 2,
) -> dict[str, Any]:
    """Phase B postflight. Apply cross-source synthesizer JSON returns to v2 pages.

    Reads every `runtime/wiki_cross_source/subagent_returns/*.json`,
    validates each against the page's expected citation IDs from the work
    plan, and rewrites the Definition / Key Claims / Open Questions
    sections of the canonical concept page. Pages that have been edited
    after the last system write (per `wiki_edit_manifest`) are skipped.
    Writes a `wiki/reports/cross_source_synthesis_applied_v{N}.yaml`
    report.
    """
    resolved_version = _coerce_version(version)
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    plan = _load_cross_source_work_plan(paths)
    work_items = plan.get("work_items") or []
    if not isinstance(work_items, list):
        raise ValueError("wiki_cross_source_work_plan.work_items must be a list")

    returns = _load_cross_source_returns(paths)
    if not returns:
        raise FileNotFoundError(
            f"No cross-source subagent returns found at "
            f"{paths.wiki_cross_source_subagent_returns_dir}. "
            "Invoke the wiki-cross-source-synthesis prompt before running "
            "wiki-apply-cross-source-synthesis."
        )

    pages_synthesized: list[dict[str, Any]] = []
    skipped_user_edited: list[str] = []
    skipped_no_return: list[str] = []
    validation_issues: list[str] = []
    writes: list[tuple[Path, str]] = []

    for item in work_items:
        if not isinstance(item, dict):
            continue
        page_id = str(item.get("page_id") or "").strip()
        page_file = str(item.get("page_file") or "").strip()
        if not page_id or not page_file:
            continue

        page_path = paths.wiki_v2_pages_dir / page_file
        if not page_path.exists():
            skipped_no_return.append(page_file)
            continue

        if wiki_edit_manifest.is_user_edited(paths, page_path):
            skipped_user_edited.append(page_file)
            continue

        payload = returns.get(page_id)
        if payload is None:
            skipped_no_return.append(page_file)
            continue

        expected_citations = {
            str(c)
            for c in item.get("source_citation_ids") or []
            if isinstance(c, str)
        }
        issues = validate_cross_source_synthesis_return(
            payload,
            page_id=page_id,
            expected_citation_ids=expected_citations,
        )
        if issues:
            validation_issues.extend(issues)
            continue

        loaded = _load_page(page_path)
        if loaded is None:
            skipped_no_return.append(page_file)
            continue
        frontmatter, existing_body = loaded
        new_body = _render_synthesis_body(existing_body, payload)
        _write_page(page_path, frontmatter, new_body)
        writes.append((page_path, "cross_source_synthesis"))

        divergences = payload.get("inter_source_divergences") or []
        pages_synthesized.append(
            {
                "page_id": page_id,
                "page_file": page_file,
                "citations_used": list(payload.get("citations_used") or []),
                "inter_source_divergences_flagged": (
                    len(divergences) if isinstance(divergences, list) else 0
                ),
            }
        )

    if validation_issues:
        raise ValueError(
            "cross-source synthesis returns failed validation:\n  - "
            + "\n  - ".join(validation_issues)
        )

    if writes:
        wiki_edit_manifest.record_writes(paths, writes)

    report_payload = {
        "cross_source_synthesis_applied": {
            "generated_at": iso_now(),
            "version": resolved_version,
            "work_plan_path": str(paths.wiki_cross_source_work_plan_path),
            "pages_considered": len(work_items),
            "pages_synthesized": pages_synthesized,
            "pages_synthesized_count": len(pages_synthesized),
            "skipped_user_edited": sorted(set(skipped_user_edited)),
            "skipped_no_return": sorted(set(skipped_no_return)),
            "writes": len(writes),
        }
    }
    report_path = paths.reports_dir / f"cross_source_synthesis_applied_v{resolved_version}.yaml"
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml(report_path, report_payload)

    return {
        "status": "applied" if pages_synthesized else "nothing_applied",
        "pages_synthesized_count": len(pages_synthesized),
        "skipped_user_edited": sorted(set(skipped_user_edited)),
        "skipped_no_return": sorted(set(skipped_no_return)),
        "report_path": str(report_path),
    }
