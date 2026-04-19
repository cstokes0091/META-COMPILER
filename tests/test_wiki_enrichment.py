"""Phase C1 tests: enrich-wiki work plan + synthesis payload validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_compiler import wiki_edit_manifest
from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import load_yaml
from meta_compiler.stages.enrichment_stage import (
    run_enrich_wiki,
    validate_synthesis_payload,
)


def _bootstrap(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    return workspace_root, artifacts_root, paths


def _write_v2_concept_page(
    paths,
    name: str,
    sources: list[str],
    body: str = "Templated content",
    page_type: str = "concept",
) -> Path:
    page = paths.wiki_v2_pages_dir / name
    page.parent.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join([f"- {s}" for s in sources]) if sources else "[]"
    if sources:
        sources_block = "sources:\n" + "\n".join([f"  - {s}" for s in sources])
    else:
        sources_block = "sources: []"
    page.write_text(
        "---\n"
        f"id: {name.replace('.md', '')}\n"
        f"type: {page_type}\n"
        "created: 2026-01-01T00:00:00Z\n"
        f"{sources_block}\n"
        "related: []\n"
        "status: raw\n"
        "---\n"
        f"# {name.replace('.md', '').replace('-', ' ').title()}\n\n## Definition\n{body}\n",
        encoding="utf-8",
    )
    return page


def _write_findings(paths, citation_id: str, *, with_quote: bool = True) -> Path:
    findings_path = paths.findings_dir / f"{citation_id}.json"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "citation_id": citation_id,
        "seed_path": f"workspace-artifacts/seeds/{citation_id}.pdf",
        "file_hash": "sha256:fake",
        "extracted_at": "2026-01-01T00:00:00Z",
        "extractor": {"agent_type": "seed-reader", "model": "test"},
        "document_metadata": {"title": citation_id, "abstract": ""},
        "concepts": [{"name": "concept-a", "definition": "from " + citation_id}],
        "quotes": (
            [{"text": "verbatim", "locator": {"page": 12}}] if with_quote else []
        ),
        "equations": [],
        "claims": [{"statement": "claim-1", "locator": {"section": "3.2"}}],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {"completeness": "full"},
    }
    findings_path.write_text(json.dumps(payload), encoding="utf-8")
    return findings_path


# ---------------------------------------------------------------------------
# run_enrich_wiki
# ---------------------------------------------------------------------------


def test_enrich_wiki_no_pages_returns_no_pages_status(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    result = run_enrich_wiki(artifacts_root=artifacts_root, workspace_root=workspace_root)
    assert result["status"] == "no_pages"
    assert result["work_items"] == 0
    assert result["work_plan_path"] is None


def test_enrich_wiki_rejects_v1(tmp_path: Path):
    workspace_root, artifacts_root, _paths = _bootstrap(tmp_path)
    with pytest.raises(ValueError):
        run_enrich_wiki(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
            version=1,
        )


def test_enrich_wiki_writes_work_plan_with_findings_paths(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_v2_concept_page(paths, "concept-a.md", sources=["src-x", "src-y"])
    _write_findings(paths, "src-x")
    _write_findings(paths, "src-y")

    result = run_enrich_wiki(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["status"] == "ready_for_orchestrator"
    assert result["work_items"] == 1
    assert result["pages_without_findings"] == []
    assert result["pages_user_edited"] == []

    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    item = plan["wiki_enrichment_work_plan"]["work_items"][0]
    assert item["page_id"] == "concept-a"
    assert item["source_citation_ids"] == ["src-x", "src-y"]
    assert len(item["findings_paths"]) == 2
    assert item["missing_findings_for"] == []
    assert item["user_edited"] is False


def test_enrich_wiki_flags_pages_without_findings(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_v2_concept_page(paths, "concept-orphan.md", sources=["src-missing"])

    result = run_enrich_wiki(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["pages_without_findings"] == ["concept-orphan.md"]
    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    item = plan["wiki_enrichment_work_plan"]["work_items"][0]
    assert item["findings_paths"] == []
    assert item["missing_findings_for"] == ["src-missing"]


def test_enrich_wiki_flags_user_edited_pages(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    page = _write_v2_concept_page(paths, "concept-edited.md", sources=["src-x"])
    _write_findings(paths, "src-x")

    wiki_edit_manifest.record_write(paths, page, "depth_baseline")
    page.write_text(page.read_text() + "\n## My edit\n", encoding="utf-8")

    result = run_enrich_wiki(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["pages_user_edited"] == ["concept-edited.md"]
    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    item = plan["wiki_enrichment_work_plan"]["work_items"][0]
    assert item["user_edited"] is True


def test_enrich_wiki_skips_non_concept_pages(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_v2_concept_page(paths, "concept-a.md", sources=["src-x"])
    _write_v2_concept_page(
        paths, "gap-remediation-v2.md", sources=[], page_type="open-question"
    )
    _write_findings(paths, "src-x")

    result = run_enrich_wiki(
        artifacts_root=artifacts_root, workspace_root=workspace_root
    )

    assert result["work_items"] == 1
    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    page_files = [item["page_file"] for item in plan["wiki_enrichment_work_plan"]["work_items"]]
    assert page_files == ["concept-a.md"]


def test_enrich_wiki_includes_related_index(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_v2_concept_page(paths, "concept-a.md", sources=["src-x"])
    _write_v2_concept_page(paths, "concept-b.md", sources=["src-y"])

    run_enrich_wiki(artifacts_root=artifacts_root, workspace_root=workspace_root)

    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    related = plan["wiki_enrichment_work_plan"]["related_pages"]
    assert {entry["file"] for entry in related} == {"concept-a.md", "concept-b.md"}
    for entry in related:
        assert entry["display_name"]
        assert entry["id"]


def test_enrich_wiki_records_edit_manifest_path(tmp_path: Path):
    workspace_root, artifacts_root, paths = _bootstrap(tmp_path)
    _write_v2_concept_page(paths, "concept-a.md", sources=["src-x"])

    run_enrich_wiki(artifacts_root=artifacts_root, workspace_root=workspace_root)

    plan = load_yaml(paths.runtime_dir / "wiki_enrichment" / "work_plan.yaml")
    assert plan["wiki_enrichment_work_plan"]["edit_manifest_path"].endswith(
        "wiki/v2/edit_manifest.yaml"
    )


# ---------------------------------------------------------------------------
# validate_synthesis_payload
# ---------------------------------------------------------------------------


def test_validate_synthesis_payload_happy_path():
    payload = {
        "definition": "A solid definition [src-x, p.1]",
        "formalism": "A formalism [src-x, §2]",
        "key_claims": "A claim [src-x, p.3]",
        "open_questions": "An open question [src-x, p.4]",
        "citations_used": ["src-x"],
        "related_pages_linked": [],
    }
    issues = validate_synthesis_payload(
        payload, page_id="concept-a", expected_citation_ids={"src-x"}
    )
    assert issues == []


def test_validate_synthesis_payload_missing_section():
    payload = {
        "definition": "A definition",
        "key_claims": "A claim",
        "open_questions": "A question",
        "citations_used": ["src-x"],
    }
    issues = validate_synthesis_payload(
        payload, page_id="concept-a", expected_citation_ids={"src-x"}
    )
    assert any("formalism" in issue for issue in issues)


def test_validate_synthesis_payload_empty_section():
    payload = {
        "definition": "",
        "formalism": "F",
        "key_claims": "K",
        "open_questions": "Q",
        "citations_used": ["src-x"],
    }
    issues = validate_synthesis_payload(
        payload, page_id="concept-a", expected_citation_ids={"src-x"}
    )
    assert any("definition" in issue for issue in issues)


def test_validate_synthesis_payload_extraneous_citation():
    payload = {
        "definition": "D",
        "formalism": "F",
        "key_claims": "K",
        "open_questions": "Q",
        "citations_used": ["src-x", "src-bogus"],
    }
    issues = validate_synthesis_payload(
        payload, page_id="concept-a", expected_citation_ids={"src-x"}
    )
    assert any("src-bogus" in issue for issue in issues)


def test_validate_synthesis_payload_no_citations_when_expected():
    payload = {
        "definition": "D",
        "formalism": "F",
        "key_claims": "K",
        "open_questions": "Q",
        "citations_used": [],
    }
    issues = validate_synthesis_payload(
        payload, page_id="concept-a", expected_citation_ids={"src-x"}
    )
    assert any("at least one source" in issue for issue in issues)


def test_validate_synthesis_payload_not_a_dict():
    issues = validate_synthesis_payload(
        "not a dict",  # type: ignore[arg-type]
        page_id="concept-a",
        expected_citation_ids={"src-x"},
    )
    assert issues == ["concept-a: synthesis payload must be a JSON object"]
