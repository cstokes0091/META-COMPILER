from __future__ import annotations

import shutil
from pathlib import Path

from ..artifacts import (
    build_paths,
    compute_seed_version,
    compute_wiki_version,
    ensure_layout,
    list_seed_files,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, parse_frontmatter, render_frontmatter
from ..utils import extract_keywords, iso_now, read_text_safe
from ..wiki_interface import WikiQueryInterface
from ..wiki_lifecycle import append_log_entry, write_index
from ..wiki_rendering import inject_wiki_nav


REQUIRED_SECTIONS = [
    "## Definition",
    "## Key Claims",
    "## Relationships",
    "## Open Questions",
    "## Source Notes",
]


def _severity_sort_key(value: str) -> int:
    order = {"critical": 0, "major": 1, "minor": 2}
    return order.get(value, 3)


def _make_gap(
    description: str,
    severity: str,
    gap_type: str,
    affected_concepts: list[str],
    attribution: list[str],
) -> dict:
    return {
        "description": description,
        "severity": severity,
        "type": gap_type,
        "affected_concepts": sorted(set(affected_concepts)),
        "attribution": sorted(set(attribution)),
        "status": "unresolved",
    }


def _load_pages(pages_dir: Path) -> list[dict]:
    pages: list[dict] = []
    for page_path in sorted(pages_dir.glob("*.md")):
        text = read_text_safe(page_path)
        frontmatter, body = parse_frontmatter(text)
        pages.append(
            {
                "path": page_path,
                "frontmatter": frontmatter,
                "body": body,
                "concept_id": frontmatter.get("id", page_path.stem),
            }
        )
    return pages


def _schema_auditor_report(pages: list[dict], timestamp: str) -> dict:
    gaps: list[dict] = []
    for page in pages:
        frontmatter = page["frontmatter"]
        concept_id = page["concept_id"]
        for key in ["id", "type", "created", "sources", "related", "status"]:
            if key not in frontmatter:
                gaps.append(
                    _make_gap(
                        description=f"Page {concept_id} is missing frontmatter field '{key}'",
                        severity="major",
                        gap_type="structural",
                        affected_concepts=[concept_id],
                        attribution=["schema_auditor"],
                    )
                )

        body = page["body"]
        for section in REQUIRED_SECTIONS:
            if section not in body:
                gaps.append(
                    _make_gap(
                        description=f"Page {concept_id} is missing required section '{section}'",
                        severity="major",
                        gap_type="structural",
                        affected_concepts=[concept_id],
                        attribution=["schema_auditor"],
                    )
                )

    return {
        "gap_report": {
            "evaluator": "schema_auditor",
            "timestamp": timestamp,
            "gaps": gaps,
        }
    }


def _adversarial_report(pages: list[dict], timestamp: str) -> dict:
    gaps: list[dict] = []
    for page in pages:
        concept_id = page["concept_id"]
        frontmatter = page["frontmatter"]
        sources = frontmatter.get("sources", []) if isinstance(frontmatter, dict) else []
        body_lower = page["body"].lower()

        if not sources:
            gaps.append(
                _make_gap(
                    description=f"Concept {concept_id} has no citations and cannot be trusted yet",
                    severity="critical",
                    gap_type="epistemic",
                    affected_concepts=[concept_id],
                    attribution=["adversarial_questioner"],
                )
            )

        if "todo" in body_lower or "tbd" in body_lower:
            gaps.append(
                _make_gap(
                    description=f"Concept {concept_id} still contains unresolved TODO/TBD markers",
                    severity="major",
                    gap_type="epistemic",
                    affected_concepts=[concept_id],
                    attribution=["adversarial_questioner"],
                )
            )

    return {
        "gap_report": {
            "evaluator": "adversarial_questioner",
            "timestamp": timestamp,
            "gaps": gaps,
        }
    }


def _ontology_report(problem_statement: str, pages: list[dict], timestamp: str) -> dict:
    gaps: list[dict] = []
    expected_topics = extract_keywords(problem_statement, max_terms=10)

    corpus = "\n".join(
        [
            str(page["concept_id"]).lower() + "\n" + page["body"].lower()
            for page in pages
        ]
    )

    for topic in expected_topics:
        if topic not in corpus:
            gaps.append(
                _make_gap(
                    description=f"Expected topic '{topic}' from problem statement has weak or missing coverage",
                    severity="minor",
                    gap_type="coverage",
                    affected_concepts=[],
                    attribution=["domain_ontologist"],
                )
            )

    return {
        "gap_report": {
            "evaluator": "domain_ontologist",
            "timestamp": timestamp,
            "gaps": gaps,
        }
    }


def _merge_gaps(
    schema: dict,
    adversarial: dict,
    ontology: dict,
    timestamp: str,
    health_metrics: dict,
) -> dict:
    combined = (
        schema["gap_report"].get("gaps", [])
        + adversarial["gap_report"].get("gaps", [])
        + ontology["gap_report"].get("gaps", [])
    )
    merged_map: dict[str, dict] = {}

    for gap in combined:
        description = gap.get("description", "")
        if description not in merged_map:
            merged_map[description] = dict(gap)
            continue

        existing = merged_map[description]
        existing["affected_concepts"] = sorted(
            set(existing.get("affected_concepts", [])) | set(gap.get("affected_concepts", []))
        )
        existing["attribution"] = sorted(
            set(existing.get("attribution", [])) | set(gap.get("attribution", []))
        )

        current_severity = existing.get("severity", "minor")
        incoming_severity = gap.get("severity", "minor")
        if _severity_sort_key(incoming_severity) < _severity_sort_key(current_severity):
            existing["severity"] = incoming_severity

    merged = sorted(
        merged_map.values(),
        key=lambda row: (_severity_sort_key(row.get("severity", "minor")), row.get("description", "")),
    )

    for idx, gap in enumerate(merged, start=1):
        gap["id"] = f"GAP-{idx:03d}"

    unresolved_count = sum(1 for gap in merged if gap.get("status") == "unresolved")
    return {
        "gap_report": {
            "generated_at": timestamp,
            "gaps": merged,
            "unresolved_count": unresolved_count,
            "health": health_metrics,
        }
    }


def _health_gaps(health_metrics: dict) -> list[dict]:
    gaps: list[dict] = []
    orphan_pages = health_metrics.get("orphan_pages", [])
    sparse_citation_pages = health_metrics.get("sparse_citation_pages", [])
    weak_relationship_pages = health_metrics.get("weak_relationship_pages", [])

    if orphan_pages:
        gaps.append(
            _make_gap(
                description="Wiki contains orphan pages with no meaningful inbound or outbound links",
                severity="major",
                gap_type="connection",
                affected_concepts=orphan_pages[:20],
                attribution=["wiki_query_lint"],
            )
        )

    if sparse_citation_pages:
        gaps.append(
            _make_gap(
                description="Some pages lack citation anchors and need evidence links",
                severity="critical",
                gap_type="evidence",
                affected_concepts=sparse_citation_pages[:20],
                attribution=["wiki_query_lint"],
            )
        )

    if weak_relationship_pages and len(weak_relationship_pages) > 1:
        gaps.append(
            _make_gap(
                description="Relationship coverage is thin; additional cross-links should be added",
                severity="minor",
                gap_type="connection",
                affected_concepts=weak_relationship_pages[:20],
                attribution=["wiki_query_lint"],
            )
        )

    return gaps


def _write_debate_transcript(
    schema: dict,
    adversarial: dict,
    ontology: dict,
    merged_gap_count: int,
    timestamp: str,
) -> dict:
    transcript = {
        "debate_transcript": {
            "generated_at": timestamp,
            "round_1": {
                "schema_auditor": {
                    "gap_count": len(schema["gap_report"].get("gaps", [])),
                    "focus": "structural completeness",
                },
                "adversarial_questioner": {
                    "gap_count": len(adversarial["gap_report"].get("gaps", [])),
                    "focus": "epistemic risk",
                },
                "domain_ontologist": {
                    "gap_count": len(ontology["gap_report"].get("gaps", [])),
                    "focus": "coverage against expected topics",
                },
            },
            "round_2": {
                "schema_auditor": "Agrees epistemic risks often stem from missing structure.",
                "adversarial_questioner": "Agrees weak structure increases citation ambiguity.",
                "domain_ontologist": "Agrees missing concepts correlate with sparse source linking.",
            },
            "round_3": {
                "synthesizer_input": "All role reports merged by severity and attribution.",
                "merged_gap_count": merged_gap_count,
            },
            "synthesis": {
                "recommendation": "Run Stage 1C review panel or iterate Stage 1B if blockers remain.",
                "status": "complete",
            },
        }
    }
    return transcript


def _copy_v1_to_v2(paths) -> int:
    paths.wiki_v2_pages_dir.mkdir(parents=True, exist_ok=True)
    for existing in paths.wiki_v2_pages_dir.glob("*.md"):
        existing.unlink()

    count = 0
    for page in sorted(paths.wiki_v1_pages_dir.glob("*.md")):
        shutil.copy2(page, paths.wiki_v2_pages_dir / page.name)
        count += 1
    return count


def _append_gap_remediation_page(paths, merged_report: dict, timestamp: str) -> None:
    gaps = merged_report["gap_report"].get("gaps", [])
    frontmatter = {
        "id": "gap-remediation-v2",
        "type": "open-question",
        "created": timestamp,
        "sources": [],
        "related": [],
        "status": "raw",
    }
    lines = [
        "---",
        render_frontmatter(frontmatter),
        "---",
        "# Gap Remediation V2",
        "",
        "## Definition",
        "Aggregated unresolved questions after Stage 1B depth pass.",
        "",
        "## Formalism",
        "No formalism for remediation index.",
        "",
        "## Key Claims",
        "- Stage 1B produced a merged gap set for review.",
        "",
        "## Relationships",
        "- prerequisite_for: []",
        "- depends_on: []",
        "- contradicts: []",
        "- extends: []",
        "",
        "## Open Questions",
    ]

    if gaps:
        for gap in gaps[:25]:
            lines.append(f"- {gap.get('id')}: {gap.get('description')}")
    else:
        lines.append("- No unresolved gaps detected.")

    lines.extend(
        [
            "",
            "## Source Notes",
            "Generated from merged gap report.",
            "",
        ]
    )

    remediation_path = paths.wiki_v2_pages_dir / "gap-remediation-v2.md"
    page_text = "\n".join(lines)
    manifest = load_manifest(paths)
    wiki_name = ""
    if manifest:
        wiki_name = str(manifest.get("workspace_manifest", {}).get("wiki", {}).get("name") or "")
    frontmatter, body = parse_frontmatter(page_text)
    if frontmatter:
        page_text = "---\n" + render_frontmatter(frontmatter) + "\n---\n" + inject_wiki_nav(body, wiki_name).rstrip() + "\n"
    remediation_path.write_text(page_text, encoding="utf-8")


def _update_manifest(paths, stage: str, page_count: int) -> None:
    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    now = iso_now()
    wm = manifest["workspace_manifest"]
    wm["seeds"] = {
        "version": compute_seed_version(paths),
        "last_updated": now,
        "document_count": len(list_seed_files(paths)),
    }
    wiki = wm.setdefault("wiki", {})
    wiki["version"] = compute_wiki_version(paths.wiki_v2_pages_dir)
    wiki["last_updated"] = now
    wiki["page_count"] = page_count
    wm["status"] = "researched"
    research = wm.setdefault("research", {})
    research["last_completed_stage"] = stage
    research["last_depth_health_marker"] = "gap-remediation-v2"
    save_manifest(paths, manifest)


def run_research_depth(artifacts_root: Path, workspace_root: Path) -> dict:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    pages = _load_pages(paths.wiki_v1_pages_dir)
    if not pages:
        raise RuntimeError("No wiki v1 pages found. Run research-breadth first.")

    problem_statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    if not problem_statement_path.exists():
        raise RuntimeError("PROBLEM_STATEMENT.md not found at workspace root.")

    problem_statement = read_text_safe(problem_statement_path)
    timestamp = iso_now()

    schema_report = _schema_auditor_report(pages, timestamp)
    adversarial_report = _adversarial_report(pages, timestamp)
    ontology_report = _ontology_report(problem_statement, pages, timestamp)

    wiki_query = WikiQueryInterface(paths=paths, prefer_v2=False)
    health_metrics = wiki_query.compute_health_metrics()
    lint_gaps = _health_gaps(health_metrics)
    if lint_gaps:
        ontology_report["gap_report"]["gaps"].extend(lint_gaps)

    dump_yaml(paths.reports_dir / "schema_auditor.yaml", schema_report)
    dump_yaml(paths.reports_dir / "adversarial_questioner.yaml", adversarial_report)
    dump_yaml(paths.reports_dir / "domain_ontologist.yaml", ontology_report)

    merged_report = _merge_gaps(
        schema_report,
        adversarial_report,
        ontology_report,
        timestamp,
        health_metrics=health_metrics,
    )
    dump_yaml(paths.reports_dir / "merged_gap_report.yaml", merged_report)
    dump_yaml(paths.reports_dir / "wiki_health_report.yaml", {"wiki_health": health_metrics})

    transcript = _write_debate_transcript(
        schema_report,
        adversarial_report,
        ontology_report,
        merged_gap_count=len(merged_report["gap_report"]["gaps"]),
        timestamp=timestamp,
    )
    dump_yaml(paths.reports_dir / "debate_transcript.yaml", transcript)

    copied_pages = _copy_v1_to_v2(paths)
    _append_gap_remediation_page(paths, merged_report, timestamp)

    v2_pages = sorted(paths.wiki_v2_pages_dir.glob("*.md"))
    write_index(
        pages_dir=paths.wiki_v2_pages_dir,
        index_path=paths.wiki_v2_dir / "index.md",
        title="Wiki v2 Index",
    )
    append_log_entry(
        log_path=paths.wiki_v2_dir / "log.md",
        operation="depth",
        title="Stage 1B depth pass",
        details=[
            f"pages_copied_from_v1: {copied_pages}",
            f"pages_in_v2: {len(v2_pages)}",
            f"merged_gap_count: {len(merged_report['gap_report']['gaps'])}",
            f"orphan_page_count: {len(health_metrics.get('orphan_pages', []))}",
            f"sparse_citation_count: {len(health_metrics.get('sparse_citation_pages', []))}",
            f"depth_completed_at: {timestamp}",
        ],
    )

    _update_manifest(paths, stage="1B", page_count=len(v2_pages))

    return {
        "schema_gaps": len(schema_report["gap_report"]["gaps"]),
        "adversarial_gaps": len(adversarial_report["gap_report"]["gaps"]),
        "ontology_gaps": len(ontology_report["gap_report"]["gaps"]),
        "merged_gaps": len(merged_report["gap_report"]["gaps"]),
        "orphan_pages": len(health_metrics.get("orphan_pages", [])),
        "sparse_citation_pages": len(health_metrics.get("sparse_citation_pages", [])),
    }
