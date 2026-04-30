"""Tests for the Stage 4 final-synthesis validators in meta_compiler.validation."""
from __future__ import annotations

from meta_compiler.validation import (
    validate_application_synthesis_return,
    validate_document_synthesis_return,
    validate_library_synthesis_return,
)


# ---------------------------------------------------------------------------
# library_synthesis_return
# ---------------------------------------------------------------------------


def _well_formed_library_payload() -> dict:
    return {
        "modality": "library",
        "package_name": "myproj",
        "module_layout": [
            {
                "target_path": "myproj/main.py",
                "sources": [
                    {"capability": "cap-001", "relative_path": "main.py"}
                ],
                "header_prose": '"""myproj.main"""\n',
                "footer_prose": "",
            }
        ],
        "exports": ["main"],
        "public_api": [
            {"symbol": "main", "summary": "entry point", "source_capability": "cap-001"}
        ],
        "entry_points": [{"name": "myproj", "target": "myproj.main:main"}],
        "readme_sections": [
            {"heading": "Overview", "body": "..."},
            {"heading": "Installation", "body": "..."},
            {"heading": "Usage", "body": "..."},
            {"heading": "Capabilities", "body": "..."},
        ],
        "deduplications_applied": [],
    }


def test_library_validator_accepts_well_formed_payload():
    payload = _well_formed_library_payload()
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert issues == []


def test_library_validator_rejects_stdlib_package_name():
    payload = _well_formed_library_payload()
    payload["package_name"] = "json"
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("collides with the Python stdlib" in m for m in issues)


def test_library_validator_rejects_meta_compiler_package_name():
    payload = _well_formed_library_payload()
    payload["package_name"] = "meta_compiler"
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("collides" in m for m in issues)


def test_library_validator_rejects_bad_package_name_pattern():
    payload = _well_formed_library_payload()
    payload["package_name"] = "MyProj"
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("must match" in m for m in issues)


def test_library_validator_catches_silent_fragment_loss():
    payload = _well_formed_library_payload()
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py", "cap-002:helper.py"},
        expected_req_ids=set(),
    )
    assert any("silent loss" in m or "missing from layout" in m for m in issues)


def test_library_validator_accepts_dropped_fragment_with_audit_entry():
    payload = _well_formed_library_payload()
    payload["deduplications_applied"] = [
        {
            "kept": "cap-001:main.py",
            "dropped": ["cap-002:helper.py"],
            "reason": "cap-002 was a stale prototype",
        }
    ]
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py", "cap-002:helper.py"},
        expected_req_ids=set(),
    )
    assert issues == []


def test_library_validator_rejects_unknown_fragment_in_sources():
    payload = _well_formed_library_payload()
    payload["module_layout"][0]["sources"].append(
        {"capability": "cap-fake", "relative_path": "ghost.py"}
    )
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("not in work plan" in m for m in issues)


def test_library_validator_rejects_malformed_entry_point_target():
    payload = _well_formed_library_payload()
    payload["entry_points"] = [{"name": "myproj", "target": "not-valid-target"}]
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("must match '<module_path>:<callable>'" in m for m in issues)


def test_library_validator_requires_minimum_readme_sections():
    payload = _well_formed_library_payload()
    payload["readme_sections"] = [{"heading": "Overview", "body": "..."}]
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("Installation" in m for m in issues)


def test_library_validator_rejects_duplicate_target_path():
    payload = _well_formed_library_payload()
    payload["module_layout"].append(
        {
            "target_path": "myproj/main.py",  # duplicate
            "sources": [{"capability": "cap-001", "relative_path": "main.py"}],
        }
    )
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("duplicate target" in m for m in issues)


def test_library_validator_rejects_missing_modality():
    payload = _well_formed_library_payload()
    del payload["modality"]
    issues = validate_library_synthesis_return(
        payload,
        expected_fragments={"cap-001:main.py"},
        expected_req_ids=set(),
    )
    assert any("modality: must be 'library'" in m for m in issues)


# ---------------------------------------------------------------------------
# document_synthesis_return
# ---------------------------------------------------------------------------


def _well_formed_document_payload() -> dict:
    return {
        "modality": "document",
        "title": "Project Report",
        "abstract": "A short abstract under five hundred chars.",
        "section_order": [
            {
                "heading": "Background",
                "source": {"synthesizer_prose": "Background paragraph. [src-foo, p.1]"},
                "transitions_after": None,
                "citations_inline": ["src-foo"],
            },
            {
                "heading": "Approach",
                "source": {"capability": "cap-001", "file": "approach.md"},
                "transitions_after": "Bridging to findings.",
                "citations_inline": [],
            },
        ],
        "intro_prose": "Opening paragraph.",
        "conclusion_prose": "Closing paragraph.",
        "references_unified": [
            {"id": "src-foo", "human": "Foo et al. (2024). Title."}
        ],
        "deduplications_applied": [],
    }


def test_document_validator_accepts_well_formed_payload():
    payload = _well_formed_document_payload()
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo", "src-bar"},
        expected_req_ids=set(),
    )
    assert issues == []


def test_document_validator_rejects_oversize_abstract():
    payload = _well_formed_document_payload()
    payload["abstract"] = "x" * 600
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo"},
        expected_req_ids=set(),
    )
    assert any("<=500 characters" in m for m in issues)


def test_document_validator_rejects_unknown_citation():
    payload = _well_formed_document_payload()
    payload["section_order"][0]["citations_inline"] = ["src-fabricated"]
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo"},
        expected_req_ids=set(),
    )
    assert any("not in citations index" in m for m in issues)


def test_document_validator_rejects_inline_cite_without_unified_entry():
    payload = _well_formed_document_payload()
    payload["section_order"][0]["citations_inline"] = ["src-foo", "src-bar"]
    # references_unified only has src-foo
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo", "src-bar"},
        expected_req_ids=set(),
    )
    assert any("missing entries for inline cites" in m for m in issues)


def test_document_validator_catches_silent_fragment_loss():
    payload = _well_formed_document_payload()
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md", "cap-002:findings.md"},
        expected_citation_ids={"src-foo"},
        expected_req_ids=set(),
    )
    assert any("silent loss" in m or "missing from layout" in m for m in issues)


def test_document_validator_accepts_synthesizer_prose_section():
    payload = _well_formed_document_payload()
    # Already has a synthesizer_prose section; ensure it doesn't trigger fragment-loss.
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo"},
        expected_req_ids=set(),
    )
    assert issues == []


def test_document_validator_rejects_section_without_source_or_prose():
    payload = _well_formed_document_payload()
    payload["section_order"].append(
        {"heading": "Empty", "source": {}, "citations_inline": []}
    )
    issues = validate_document_synthesis_return(
        payload,
        expected_fragments={"cap-001:approach.md"},
        expected_citation_ids={"src-foo"},
        expected_req_ids=set(),
    )
    assert any("must have either" in m for m in issues)


# ---------------------------------------------------------------------------
# application_synthesis_return
# ---------------------------------------------------------------------------


def _well_formed_application_payload() -> dict:
    return {
        "modality": "application",
        "application_name": "myflow",
        "directory_layout": {
            "inbox": [{"source": "cap-001:sample.docx", "target": "inbox/sample.docx"}],
            "outbox": [],
            "state": [],
            "kb_brief": [],
            "tests": [
                {"source": "cap-002:test_handler.py", "target": "tests/test_handler.py"}
            ],
            "orchestrator": [
                {"source": "cap-003:handler.py", "target": "orchestrator/handler.py"}
            ],
        },
        "entry_point": {
            "filename": "run.py",
            "body": "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
            "invocation": "python run.py --inbox inbox/",
        },
        "environment_variables": [
            {"name": "API_KEY", "purpose": "auth", "required": True}
        ],
        "dependencies": ["pyyaml"],
        "readme_sections": [
            {"heading": "Overview", "body": "..."},
            {"heading": "Run", "body": "..."},
            {"heading": "Configuration", "body": "..."},
        ],
        "deduplications_applied": [],
    }


def test_application_validator_accepts_well_formed_payload():
    payload = _well_formed_application_payload()
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert issues == []


def test_application_validator_rejects_missing_buckets():
    payload = _well_formed_application_payload()
    del payload["directory_layout"]["inbox"]
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("missing required bucket(s)" in m and "inbox" in m for m in issues)


def test_application_validator_rejects_invalid_python_entry_point():
    payload = _well_formed_application_payload()
    payload["entry_point"]["body"] = "def main(:\n    pass\n"  # SyntaxError
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("invalid Python syntax" in m for m in issues)


def test_application_validator_rejects_kebab_violation():
    payload = _well_formed_application_payload()
    payload["application_name"] = "MyFlow"
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("application_name" in m for m in issues)


def test_application_validator_rejects_empty_tests():
    payload = _well_formed_application_payload()
    payload["directory_layout"]["tests"] = []
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("tests: must be non-empty" in m for m in issues)


def test_application_validator_rejects_unknown_fragment():
    payload = _well_formed_application_payload()
    payload["directory_layout"]["inbox"].append(
        {"source": "cap-ghost:phantom.txt", "target": "inbox/phantom.txt"}
    )
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("not in work plan" in m for m in issues)


def test_application_validator_requires_minimum_readme_sections():
    payload = _well_formed_application_payload()
    payload["readme_sections"] = [{"heading": "Overview", "body": "..."}]
    issues = validate_application_synthesis_return(
        payload,
        expected_fragments={
            "cap-001:sample.docx",
            "cap-002:test_handler.py",
            "cap-003:handler.py",
        },
        expected_buckets={"inbox", "outbox", "state", "kb_brief", "tests", "orchestrator"},
        expected_req_ids=set(),
    )
    assert any("missing required heading" in m and "Run" in m for m in issues)
