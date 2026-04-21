from meta_compiler.validation import validate_decision_log


def _algorithm_payload() -> dict:
    return {
        "decision_log": {
            "meta": {
                "project_name": "Demo",
                "project_type": "algorithm",
                "created": "2026-01-01T00:00:00+00:00",
                "version": 1,
                "parent_version": None,
                "reason_for_revision": None,
                "problem_statement_hash": "abc",
                "wiki_version": "def",
            },
            "conventions": [
                {
                    "name": "Code style",
                    "domain": "code",
                    "choice": "Typed Python",
                    "rationale": "Consistency",
                    "citations": ["src-example"],
                }
            ],
            "architecture": [
                {
                    "component": "orchestrator",
                    "approach": "CLI",
                    "alternatives_rejected": [{"name": "manual", "reason": "not repeatable"}],
                    "constraints_applied": ["artifact-driven"],
                    "citations": ["src-example"],
                }
            ],
            "code_architecture": [
                {
                    "aspect": "language",
                    "choice": "Python 3.11",
                    "rationale": "matches the workspace toolchain",
                    "citations": ["src-example"],
                },
                {
                    "aspect": "libraries",
                    "choice": "numpy + pyarrow",
                    "libraries": [
                        {"name": "numpy", "description": "PSF math (>=1.26)"},
                        {"name": "pyarrow", "description": "columnar I/O (>=15)"},
                    ],
                    "rationale": "stable and well-documented",
                    "citations": ["src-example"],
                },
            ],
            "scope": {
                "in_scope": [{"item": "stage2", "rationale": "required"}],
                "out_of_scope": [{"item": "execution", "rationale": "later", "revisit_if": "phase 2"}],
            },
            "requirements": [
                {
                    "id": "REQ-001",
                    "description": "must validate",
                    "source": "derived",
                    "citations": ["src-example"],
                    "verification": "validate-stage --stage 2",
                }
            ],
            "open_items": [
                {
                    "description": "decide update flow",
                    "deferred_to": "future_work",
                    "owner": "human",
                }
            ],
            "agents_needed": [
                {
                    "role": "scaffold-generator",
                    "responsibility": "generate structure",
                    "inputs": [{"name": "decision_log", "modality": "document"}],
                    "outputs": [
                        {"name": "scaffold", "modality": "code"},
                        {"name": "agents", "modality": "document"},
                    ],
                    "key_constraints": ["no hallucination"],
                }
            ],
        }
    }


def test_decision_log_validation_happy_path():
    issues = validate_decision_log(_algorithm_payload())
    assert issues == []


def test_decision_log_rejects_legacy_reads_writes():
    payload = _algorithm_payload()
    agent = payload["decision_log"]["agents_needed"][0]
    agent["reads"] = ["decision_log"]
    agent["writes"] = ["scaffold"]
    issues = validate_decision_log(payload)
    assert any("legacy 'reads'/'writes'" in i for i in issues)


def test_decision_log_rejects_unknown_modality():
    payload = _algorithm_payload()
    payload["decision_log"]["agents_needed"][0]["outputs"][0]["modality"] = "binary"
    issues = validate_decision_log(payload)
    assert any("modality" in i for i in issues)


def test_decision_log_requires_code_architecture_for_algorithm():
    payload = _algorithm_payload()
    payload["decision_log"].pop("code_architecture")
    issues = validate_decision_log(payload)
    assert any("code_architecture" in i and "required" in i for i in issues)


def test_decision_log_requires_language_and_libraries_aspects():
    payload = _algorithm_payload()
    payload["decision_log"]["code_architecture"] = [
        {
            "aspect": "language",
            "choice": "Python 3.11",
            "rationale": "team familiarity",
            "citations": [],
        }
    ]
    issues = validate_decision_log(payload)
    assert any("aspect='libraries'" in i for i in issues)


def test_decision_log_forbids_code_architecture_for_report():
    payload = _algorithm_payload()
    payload["decision_log"]["meta"]["project_type"] = "report"
    # report payload should not require code_architecture; remove it.
    issues = validate_decision_log(payload)
    assert any("code_architecture" in i and "must be omitted" in i for i in issues)


def test_decision_log_report_rejects_code_modality_outputs():
    payload = _algorithm_payload()
    payload["decision_log"]["meta"]["project_type"] = "report"
    payload["decision_log"].pop("code_architecture")
    # The agent still has a 'code' modality output from the algorithm fixture.
    issues = validate_decision_log(payload)
    assert any(
        "report projects cannot declare 'code' outputs" in i for i in issues
    )


def test_decision_log_report_with_document_outputs_passes():
    payload = _algorithm_payload()
    payload["decision_log"]["meta"]["project_type"] = "report"
    payload["decision_log"].pop("code_architecture")
    payload["decision_log"]["agents_needed"][0]["outputs"] = [
        {"name": "report", "modality": "document"}
    ]
    issues = validate_decision_log(payload)
    assert issues == []
