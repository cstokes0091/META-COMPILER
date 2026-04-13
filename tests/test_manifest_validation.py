from meta_compiler.validation import validate_manifest


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
