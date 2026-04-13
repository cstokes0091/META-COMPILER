from meta_compiler.validation import validate_decision_log


def test_decision_log_validation_happy_path():
    payload = {
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
                    "reads": ["decision_log"],
                    "writes": ["scaffold"],
                    "key_constraints": ["no hallucination"],
                }
            ],
        }
    }

    issues = validate_decision_log(payload)
    assert issues == []
