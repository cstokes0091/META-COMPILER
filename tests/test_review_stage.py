from pathlib import Path

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.review_stage import run_review
from meta_compiler.validation import validate_stage


def test_run_review_writes_stage_1a2_handoff(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    dump_yaml(
        paths.manifest_path,
        {
            "workspace_manifest": {
                "name": "Test Project",
                "created": "2026-01-01T00:00:00Z",
                "last_modified": "2026-01-01T00:00:00Z",
                "problem_domain": "test domain",
                "project_type": "algorithm",
                "seeds": {"version": "seed-hash", "last_updated": "2026-01-01T00:00:00Z", "document_count": 1},
                "wiki": {"version": "wiki-hash", "last_updated": "2026-01-01T00:00:00Z", "page_count": 2},
                "decision_logs": [],
                "status": "researched",
                "research": {"iteration_count": 0, "last_completed_stage": "1B"},
            }
        },
    )
    dump_yaml(
        paths.reports_dir / "merged_gap_report.yaml",
        {
            "gap_report": {
                "generated_at": "2026-01-01T00:00:00Z",
                "unresolved_count": 1,
                "gaps": [
                    {
                        "id": "GAP-001",
                        "description": "Missing citation for concept-x",
                        "severity": "critical",
                        "type": "evidence",
                        "affected_concepts": ["concept-x"],
                        "attribution": ["schema_auditor"],
                        "status": "unresolved",
                    }
                ],
                "health": {
                    "orphan_pages": [],
                    "sparse_citation_pages": ["concept-x"],
                    "open_question_count": 0,
                },
            }
        },
    )
    (paths.wiki_v2_dir / "log.md").write_text("## [2026-01-01T00:00:00Z] Existing log\n", encoding="utf-8")

    result = run_review(artifacts_root=artifacts_root)

    assert result["handoff_path"].endswith("1a2_handoff.yaml")

    handoff = load_yaml(paths.reviews_dir / "1a2_handoff.yaml")
    packet = handoff["stage_1a2_handoff"]
    assert packet["decision"] in {"PROCEED", "ITERATE"}
    assert packet["blocking_gaps"]
    assert packet["suggested_sources"] == []

    issues = validate_stage(paths, stage="1c")
    assert issues == []


def test_run_review_collects_suggested_sources(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    dump_yaml(
        paths.manifest_path,
        {
            "workspace_manifest": {
                "name": "Test Project",
                "created": "2026-01-01T00:00:00Z",
                "last_modified": "2026-01-01T00:00:00Z",
                "problem_domain": "test domain",
                "project_type": "algorithm",
                "seeds": {"version": "seed-hash", "last_updated": "2026-01-01T00:00:00Z", "document_count": 1},
                "wiki": {"version": "wiki-hash", "last_updated": "2026-01-01T00:00:00Z", "page_count": 2, "name": "Test Build Atlas"},
                "decision_logs": [],
                "executions": [],
                "pitches": [],
                "status": "researched",
                "research": {"iteration_count": 0, "last_completed_stage": "1B"},
            }
        },
    )
    dump_yaml(
        paths.reports_dir / "merged_gap_report.yaml",
        {
            "gap_report": {
                "generated_at": "2026-01-01T00:00:00Z",
                "unresolved_count": 0,
                "gaps": [],
                "health": {"orphan_pages": [], "sparse_citation_pages": [], "open_question_count": 0},
            }
        },
    )
    dump_yaml(
        paths.reviews_search_dir / "pragmatic.yaml",
        {
            "search_results": {
                "reviewer": "pragmatic",
                "sources": [
                    {
                        "title": "Consensus Overview",
                        "provider": "consensus.app",
                        "url": "https://consensus.app/example",
                        "rationale": "Useful survey source.",
                    },
                    {
                        "title": "Semantic Scholar Paper",
                        "provider": "semanticscholar.org",
                        "url": "https://www.semanticscholar.org/paper/example",
                        "rationale": "Peer-reviewed supporting paper.",
                    },
                ],
            }
        },
    )
    (paths.wiki_v2_dir / "log.md").write_text("## [2026-01-01T00:00:00Z] Existing log\n", encoding="utf-8")

    result = run_review(artifacts_root=artifacts_root)

    assert result["suggested_source_count"] == 2
    handoff = load_yaml(paths.reviews_dir / "1a2_handoff.yaml")
    packet = handoff["stage_1a2_handoff"]
    assert packet["suggested_sources"][0]["provider"] in {"consensus.app", "semanticscholar.org"}