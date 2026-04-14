from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.validation import validate_manifest, validate_stage


def test_manifest_validation_happy_path():
    manifest = {
        "workspace_manifest": {
            "name": "Test",
            "created": "2026-01-01T00:00:00+00:00",
            "last_modified": "2026-01-01T00:00:00+00:00",
            "problem_domain": "Domain",
            "project_type": "algorithm",
            "seeds": {"version": "abc", "last_updated": "2026-01-01T00:00:00+00:00", "document_count": 1},
            "wiki": {"version": "def", "last_updated": "2026-01-01T00:00:00+00:00", "page_count": 1},
            "decision_logs": [],
            "status": "initialized",
        }
    }
    issues = validate_manifest(manifest)
    assert issues == []


def test_manifest_validation_missing_root():
    issues = validate_manifest({})
    assert issues


def test_stage4_validation_happy_path(tmp_path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    execution_dir = paths.executions_dir / "v1"
    execution_dir.mkdir(parents=True, exist_ok=True)
    (execution_dir / "FINAL_OUTPUT_MANIFEST.yaml").write_text(
        "final_output:\n  project_type: algorithm\n",
        encoding="utf-8",
    )
    (paths.wiki_provenance_dir / "what_i_built.md").write_text("## What I Built\n", encoding="utf-8")
    (paths.pitches_dir / "pitch_v1.pptx").write_bytes(b"pptx")

    issues = validate_stage(paths, stage="4")
    assert issues == []
