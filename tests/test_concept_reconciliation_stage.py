"""Tests for the semantic wiki enrichment pipeline (concept reconciliation
+ cross-source synthesis). Replaces the former test_wiki_update_stage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_compiler import wiki_edit_manifest
from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml, parse_frontmatter
from meta_compiler.stages.concept_reconciliation_stage import (
    run_wiki_apply_reconciliation,
    run_wiki_cross_source_synthesize,
    run_wiki_reconcile_concepts,
)
from meta_compiler.validation import (
    validate_alias_page,
    validate_concept_reconciliation_proposal,
)
from meta_compiler.wiki_interface import WikiQueryInterface
from meta_compiler.wiki_linking import run_wiki_link


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _write_finding(paths, citation_id: str, concepts: list[dict]) -> Path:
    payload = {
        "citation_id": citation_id,
        "seed_path": f"workspace-artifacts/seeds/{citation_id}.pdf",
        "file_hash": "sha256:fake",
        "extracted_at": "2026-01-01T00:00:00Z",
        "extractor": {"agent_type": "seed-reader", "model": "test"},
        "document_metadata": {"title": citation_id},
        "concepts": concepts,
        "quotes": [],
        "equations": [],
        "claims": [
            {
                "statement": f"{citation_id} claim on {c['name']}",
                "locator": {"page": 3, "section": "2.1"},
            }
            for c in concepts
        ],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    out = paths.findings_dir / f"{citation_id}.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _write_concept_page(paths, name: str, sources: list[str], *, body_extra: str = "") -> Path:
    page = paths.wiki_v2_pages_dir / name
    sources_block = "sources:\n" + "\n".join(f"  - {s}" for s in sources) if sources else "sources: []"
    page.write_text(
        "---\n"
        f"id: {name.replace('.md', '')}\n"
        "type: concept\n"
        "created: 2026-01-01T00:00:00Z\n"
        f"{sources_block}\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        f"# {name.replace('.md', '').replace('-', ' ').title()}\n\n"
        "## Definition\n"
        "Baseline.\n\n"
        "## Formalism\n- none\n\n"
        "## Key Claims\n- stub\n\n"
        "## Relationships\n- prerequisite_for: []\n- depends_on: []\n- contradicts: []\n- extends: []\n\n"
        "## Open Questions\n- q1\n\n"
        "## Source Notes\n- ok\n"
        f"{body_extra}",
        encoding="utf-8",
    )
    return page


# ---------------------------------------------------------------------------
# Phase A preflight
# ---------------------------------------------------------------------------


def test_reconcile_preflight_buckets_multi_source_candidates(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_finding(
        paths,
        "src-johnson",
        [{"name": "Johnson noise", "definition": "thermal kT fluctuation", "importance": "central"}],
    )
    _write_finding(
        paths,
        "src-detector",
        [{"name": "thermal noise", "definition": "readout noise floor", "importance": "central"}],
    )

    result = run_wiki_reconcile_concepts(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    plan = load_yaml(paths.wiki_reconcile_work_plan_path)
    work_items = plan["wiki_concept_reconciliation_work_plan"]["work_items"]
    assert len(work_items) == 1
    item = work_items[0]
    assert item["bucket_key"] == "noise"
    assert item["candidate_count"] == 2
    assert sorted(item["source_citation_ids"]) == ["src-detector", "src-johnson"]

    # The request file exists so the gate hook can verify the preflight ran.
    assert paths.wiki_reconcile_request_path.exists()


def test_reconcile_preflight_skips_singletons(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_finding(
        paths,
        "src-one",
        [{"name": "Solo Concept", "definition": "x", "importance": "central"}],
    )

    result = run_wiki_reconcile_concepts(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "no_candidates"
    assert result["work_item_count"] == 0


def test_reconcile_preflight_skips_same_source_duplicates(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_finding(
        paths,
        "src-one",
        [
            {"name": "Noise A", "definition": "x"},
            {"name": "Noise B", "definition": "y"},
        ],
    )

    result = run_wiki_reconcile_concepts(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "no_candidates"


# ---------------------------------------------------------------------------
# Phase A postflight
# ---------------------------------------------------------------------------


def _write_proposal(paths, canonical_name: str, members: list[dict]) -> Path:
    proposal = {
        "concept_reconciliation_proposal": {
            "generated_at": "2026-01-01T00:00:00Z",
            "version": 2,
            "alias_groups": [
                {
                    "canonical_name": canonical_name,
                    "members": members,
                    "justification": "test",
                }
            ],
            "distinct_concepts": [],
        }
    }
    proposal_path = paths.reports_dir / "concept_reconciliation_v2.yaml"
    dump_yaml(proposal_path, proposal)
    return proposal_path


def test_apply_reconciliation_merges_sources_and_creates_alias_stubs(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    # Canonical page exists; one alias page exists; one doesn't (reconciler
    # surfaced a name from findings that never got its own v1 page).
    _write_concept_page(paths, "concept-thermal-noise.md", sources=["src-johnson"])
    _write_concept_page(paths, "concept-johnson-noise.md", sources=["src-johnson"])
    _write_proposal(
        paths,
        "Thermal Noise",
        [
            {
                "name": "Johnson noise",
                "source_citation_id": "src-johnson",
                "evidence_locator": {"page": 3, "section": "2.1"},
                "definition_excerpt": "Random electron thermal fluctuation in a resistor.",
            },
            {
                "name": "thermal noise",
                "source_citation_id": "src-detector",
                "evidence_locator": {"page": 87, "section": "4.2"},
                "definition_excerpt": "Noise proportional to kT in the readout chain.",
            },
        ],
    )

    result = run_wiki_apply_reconciliation(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "applied"
    assert result["alias_groups_applied_count"] == 1

    canonical = paths.wiki_v2_pages_dir / "concept-thermal-noise.md"
    fm, body = parse_frontmatter(canonical.read_text(encoding="utf-8"))
    assert set(fm["sources"]) == {"src-johnson", "src-detector"}
    assert "Johnson noise" in (fm.get("aliases") or [])
    assert "### Alias Sources" in body
    assert "src-detector" in body

    alias_stub = paths.wiki_v2_pages_dir / "concept-johnson-noise.md"
    fm_stub, body_stub = parse_frontmatter(alias_stub.read_text(encoding="utf-8"))
    assert fm_stub["type"] == "alias"
    assert fm_stub["canonical"] == "concept-thermal-noise"
    assert "concept-thermal-noise.md" in body_stub


def test_apply_reconciliation_creates_canonical_when_missing(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_concept_page(paths, "concept-johnson-noise.md", sources=["src-johnson"])
    _write_concept_page(paths, "concept-read-noise.md", sources=["src-detector"])
    _write_proposal(
        paths,
        "Thermal Noise",
        [
            {
                "name": "Johnson noise",
                "source_citation_id": "src-johnson",
                "evidence_locator": {"page": 3},
                "definition_excerpt": "kT fluctuation.",
            },
            {
                "name": "Read noise",
                "source_citation_id": "src-detector",
                "evidence_locator": {"page": 87},
                "definition_excerpt": "Readout noise floor.",
            },
        ],
    )

    run_wiki_apply_reconciliation(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    canonical = paths.wiki_v2_pages_dir / "concept-thermal-noise.md"
    assert canonical.exists()
    fm, _ = parse_frontmatter(canonical.read_text(encoding="utf-8"))
    assert fm["type"] == "concept"
    assert set(fm["aliases"]) == {"Johnson noise", "Read noise"}


def test_apply_reconciliation_records_edit_manifest(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_concept_page(paths, "concept-thermal-noise.md", sources=["src-johnson"])
    _write_proposal(
        paths,
        "Thermal Noise",
        [
            {
                "name": "Johnson noise",
                "source_citation_id": "src-johnson",
                "evidence_locator": {"page": 3},
                "definition_excerpt": "x",
            },
            {
                "name": "thermal noise",
                "source_citation_id": "src-detector",
                "evidence_locator": {"page": 87},
                "definition_excerpt": "y",
            },
        ],
    )

    run_wiki_apply_reconciliation(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    canonical = paths.wiki_v2_pages_dir / "concept-thermal-noise.md"
    entry = wiki_edit_manifest.entry_for(paths, canonical)
    assert entry is not None
    assert entry["source"] == "concept_reconciliation"


def test_apply_reconciliation_preserves_user_edited_pages(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    page = _write_concept_page(paths, "concept-thermal-noise.md", sources=["src-johnson"])
    wiki_edit_manifest.record_write(paths, page, "depth_baseline")
    # Simulate a user edit after the baseline write.
    page.write_text(page.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")

    _write_proposal(
        paths,
        "Thermal Noise",
        [
            {
                "name": "Johnson noise",
                "source_citation_id": "src-johnson",
                "evidence_locator": {"page": 3},
                "definition_excerpt": "x",
            },
            {
                "name": "thermal noise",
                "source_citation_id": "src-detector",
                "evidence_locator": {"page": 87},
                "definition_excerpt": "y",
            },
        ],
    )

    result = run_wiki_apply_reconciliation(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert "concept-thermal-noise.md" in result["skipped_user_edited"]
    # Canonical page was not rewritten; no alias frontmatter appears.
    fm, _ = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert "aliases" not in fm


def test_apply_reconciliation_missing_proposal_raises(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    with pytest.raises(FileNotFoundError):
        run_wiki_apply_reconciliation(
            artifacts_root=artifacts_root, workspace_root=workspace_root
        )


# ---------------------------------------------------------------------------
# Phase B preflight
# ---------------------------------------------------------------------------


def test_cross_source_preflight_covers_reconciled_canonicals(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    # Canonical with aliases, two sources, both sources have findings records
    # that mention the canonical or its alias.
    _write_concept_page(paths, "concept-thermal-noise.md", sources=["src-johnson", "src-detector"])
    page = paths.wiki_v2_pages_dir / "concept-thermal-noise.md"
    fm, body = parse_frontmatter(page.read_text(encoding="utf-8"))
    fm["aliases"] = ["Johnson noise"]
    page.write_text(
        "---\n"
        + "\n".join(f"{k}: {v}" if not isinstance(v, list) else f"{k}: {v}" for k, v in fm.items())
        + "\n---\n"
        + body,
        encoding="utf-8",
    )
    # Rewrite via yaml-safe path to preserve lists cleanly.
    from meta_compiler.io import render_frontmatter
    page.write_text(
        "---\n" + render_frontmatter(fm) + "\n---\n" + body, encoding="utf-8"
    )

    _write_finding(
        paths,
        "src-johnson",
        [{"name": "Johnson noise", "definition": "kT fluctuation"}],
    )
    _write_finding(
        paths,
        "src-detector",
        [{"name": "thermal noise", "definition": "readout noise"}],
    )

    result = run_wiki_cross_source_synthesize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    plan = load_yaml(paths.wiki_cross_source_work_plan_path)
    items = plan["wiki_cross_source_work_plan"]["work_items"]
    assert len(items) == 1
    item = items[0]
    assert item["page_id"] == "concept-thermal-noise"
    assert sorted(item["source_citation_ids"]) == ["src-detector", "src-johnson"]
    assert sorted(item["covered_citation_ids"]) == ["src-detector", "src-johnson"]
    assert len(item["findings_records"]) == 2


def test_cross_source_preflight_skips_single_source(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_concept_page(paths, "concept-only.md", sources=["src-alone"])
    _write_finding(paths, "src-alone", [{"name": "only", "definition": "x"}])

    result = run_wiki_cross_source_synthesize(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "no_candidates"
    assert "concept-only.md" in result["skipped_single_source"]


# ---------------------------------------------------------------------------
# Linker alias wiring
# ---------------------------------------------------------------------------


def test_wiki_linker_links_alias_mentions_to_canonical(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    # Canonical page with an alias; another page that mentions the alias in
    # prose.
    canonical = paths.wiki_v2_pages_dir / "concept-thermal-noise.md"
    canonical.write_text(
        "---\n"
        "id: concept-thermal-noise\n"
        "type: concept\n"
        "created: 2026-01-01T00:00:00Z\n"
        "sources: []\n"
        "aliases:\n  - Johnson noise\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        "# Thermal Noise\n\n## Definition\nBaseline.\n",
        encoding="utf-8",
    )
    host = paths.wiki_v2_pages_dir / "concept-host.md"
    host.write_text(
        "---\n"
        "id: concept-host\n"
        "type: concept\n"
        "created: 2026-01-01T00:00:00Z\n"
        "sources: []\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        "# Host\n\n## Definition\nThe Johnson noise dominates here.\n",
        encoding="utf-8",
    )

    run_wiki_link(artifacts_root=artifacts_root, workspace_root=workspace_root)

    host_text = host.read_text(encoding="utf-8")
    assert "[Johnson noise](concept-thermal-noise.md)" in host_text


# ---------------------------------------------------------------------------
# Validators + health metrics
# ---------------------------------------------------------------------------


def test_validate_concept_reconciliation_proposal_happy_path():
    payload = {
        "concept_reconciliation_proposal": {
            "generated_at": "2026-01-01T00:00:00Z",
            "version": 2,
            "alias_groups": [
                {
                    "canonical_name": "Thermal Noise",
                    "members": [
                        {
                            "name": "Johnson noise",
                            "source_citation_id": "src-johnson",
                            "evidence_locator": {"page": 3},
                            "definition_excerpt": "x",
                        },
                        {
                            "name": "thermal noise",
                            "source_citation_id": "src-detector",
                            "evidence_locator": {"page": 87},
                            "definition_excerpt": "y",
                        },
                    ],
                    "justification": "both describe kT fluctuation",
                }
            ],
        }
    }
    assert validate_concept_reconciliation_proposal(payload) == []


def test_validate_concept_reconciliation_proposal_flags_missing_member_fields():
    payload = {
        "concept_reconciliation_proposal": {
            "generated_at": "t",
            "version": 2,
            "alias_groups": [
                {
                    "canonical_name": "Foo",
                    "justification": "j",
                    "members": [{"name": "bar"}],
                }
            ],
        }
    }
    issues = validate_concept_reconciliation_proposal(payload)
    assert any("source_citation_id" in issue for issue in issues)
    assert any("evidence_locator" in issue for issue in issues)
    assert any("definition_excerpt" in issue for issue in issues)


def test_validate_alias_page_requires_canonical(tmp_path: Path):
    page = tmp_path / "alias.md"
    page.write_text(
        "---\n"
        "id: alias\n"
        "type: alias\n"
        "created: 2026-01-01T00:00:00Z\n"
        "sources: []\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        "# Alias\n",
        encoding="utf-8",
    )
    issues = validate_alias_page(page)
    assert any("canonical" in issue for issue in issues)


def test_health_report_tracks_alias_and_unreconciled(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    # Two findings define the same concept under different names; no
    # reconciliation applied yet.
    _write_finding(paths, "src-x", [{"name": "Foo", "definition": "x"}])
    _write_finding(paths, "src-y", [{"name": "foo", "definition": "y"}])
    _write_concept_page(paths, "concept-foo.md", sources=["src-x", "src-y"])

    # Add an applied alias group to confirm it's excluded from unreconciled.
    _write_concept_page(paths, "concept-bar.md", sources=["src-x"])
    page = paths.wiki_v2_pages_dir / "concept-bar.md"
    fm, body = parse_frontmatter(page.read_text(encoding="utf-8"))
    fm["aliases"] = ["Barracuda"]
    from meta_compiler.io import render_frontmatter
    page.write_text(
        "---\n" + render_frontmatter(fm) + "\n---\n" + body, encoding="utf-8"
    )

    interface = WikiQueryInterface(paths)
    health = interface.compute_health_metrics()
    assert "concept-bar" in health["canonical_concept_pages"]
    # concept-foo has sources from two citations but no aliases recorded yet
    # and matches the flag threshold for synthesis.
    assert "concept-foo" in health["concepts_with_multiple_sources_but_no_synthesis"]
