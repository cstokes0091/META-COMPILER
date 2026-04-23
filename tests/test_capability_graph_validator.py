"""Unit tests for the Pydantic schemas under meta_compiler.schemas.

Commit 1: schema shapes are defined but unused by producers yet. These tests
verify the models accept the documented shapes and reject the invariants we
care about (empty lists, unknown enum values, disallowed extra fields).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from meta_compiler.schemas import (
    Capability,
    CapabilityGraph,
    Contract,
    ContractIOField,
    ContractManifest,
    ContractManifestEntry,
    FindingRef,
    SkillFrontmatter,
    SkillIndex,
    SkillIndexEntry,
    VerificationHook,
    VerificationType,
)


def _finding_ref() -> dict:
    return {
        "finding_id": "src-decision-seed#a1b2c3d4e5f6",
        "citation_id": "src-decision-seed",
        "seed_path": "seeds/decision-seed.md",
        "quote": "…",
        "locator": {"section": "Schema"},
    }


def _contract_io_field(name: str = "decision_log_yaml", modality: str = "document") -> dict:
    return {"name": name, "modality": modality}


def _contract_payload() -> dict:
    return {
        "contract_id": "contract-decision-log-validate",
        "title": "Decision Log Schema Validation Contract",
        "inputs": [_contract_io_field("decision_log_yaml", "document")],
        "outputs": [_contract_io_field("validation_issues", "data")],
        "invariants": ["Every requirement row has a non-empty citations list"],
        "test_fixtures": [],
        "required_findings": [_finding_ref()],
    }


def _capability_payload() -> dict:
    return {
        "name": "decision-log-schema-validate",
        "description": "Verify decision log YAML conforms to the v1 schema with citation-traceable rows.",
        "when_to_use": ["validate decision log schema", "check citation traceability"],
        "required_finding_ids": ["src-decision-seed#a1b2c3d4e5f6"],
        "io_contract_ref": "contract-decision-log-validate",
        "verification_type": "unit_test",
        "verification_hook_ids": ["ver-decision-log-schema-001"],
        "requirement_ids": ["REQ-001"],
        "citation_ids": ["src-decision-seed", "src-sample-seed"],
        "composes": [],
    }


def _capability_graph_payload() -> dict:
    return {
        "generated_at": "2026-04-22T00:00:00+00:00",
        "decision_log_version": 1,
        "project_type": "hybrid",
        "capabilities": [_capability_payload()],
    }


def _skill_frontmatter_payload() -> dict:
    return {
        "name": "decision-log-schema-validate",
        "description": "Verify decision log YAML conforms to the v1 schema with citation-traceable rows.",
        "triggers": ["validate decision log schema", "check citation traceability"],
        "required_finding_ids": ["src-decision-seed#a1b2c3d4e5f6"],
        "contract_refs": ["contract-decision-log-validate"],
        "verification_hooks": ["ver-decision-log-schema-001"],
        "findings": [_finding_ref()],
    }


class TestFindingRef:
    def test_accepts_documented_shape(self):
        FindingRef.model_validate(_finding_ref())

    def test_rejects_empty_finding_id(self):
        payload = _finding_ref()
        payload["finding_id"] = ""
        with pytest.raises(ValidationError):
            FindingRef.model_validate(payload)

    def test_quote_max_length(self):
        payload = _finding_ref()
        payload["quote"] = "x" * 401
        with pytest.raises(ValidationError):
            FindingRef.model_validate(payload)


class TestContract:
    def test_accepts_documented_shape(self):
        Contract.model_validate(_contract_payload())

    def test_rejects_empty_inputs(self):
        payload = _contract_payload()
        payload["inputs"] = []
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)

    def test_rejects_empty_outputs(self):
        payload = _contract_payload()
        payload["outputs"] = []
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)

    def test_rejects_empty_invariants(self):
        payload = _contract_payload()
        payload["invariants"] = []
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)

    def test_rejects_empty_required_findings(self):
        payload = _contract_payload()
        payload["required_findings"] = []
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)

    def test_rejects_unknown_modality(self):
        payload = _contract_payload()
        payload["inputs"][0]["modality"] = "not-a-modality"
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)

    def test_rejects_extra_field(self):
        payload = _contract_payload()
        payload["bogus_key"] = "oops"
        with pytest.raises(ValidationError):
            Contract.model_validate(payload)


class TestContractManifest:
    def test_accepts_documented_shape(self):
        ContractManifest.model_validate({
            "generated_at": "2026-04-22T00:00:00+00:00",
            "decision_log_version": 1,
            "entries": [{
                "contract_id": "contract-decision-log-validate",
                "path": "contracts/decision-log-validate.yaml",
            }],
        })

    def test_rejects_empty_entries(self):
        with pytest.raises(ValidationError):
            ContractManifest.model_validate({
                "generated_at": "2026-04-22T00:00:00+00:00",
                "decision_log_version": 1,
                "entries": [],
            })

    def test_rejects_version_zero(self):
        with pytest.raises(ValidationError):
            ContractManifest.model_validate({
                "generated_at": "2026-04-22T00:00:00+00:00",
                "decision_log_version": 0,
                "entries": [ContractManifestEntry(contract_id="x", path="y").model_dump()],
            })


class TestCapability:
    def test_accepts_documented_shape(self):
        Capability.model_validate(_capability_payload())

    def test_rejects_empty_required_finding_ids(self):
        payload = _capability_payload()
        payload["required_finding_ids"] = []
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_empty_when_to_use(self):
        payload = _capability_payload()
        payload["when_to_use"] = []
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_empty_verification_hook_ids(self):
        payload = _capability_payload()
        payload["verification_hook_ids"] = []
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_empty_requirement_ids(self):
        payload = _capability_payload()
        payload["requirement_ids"] = []
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_empty_citation_ids(self):
        payload = _capability_payload()
        payload["citation_ids"] = []
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_unknown_verification_type(self):
        payload = _capability_payload()
        payload["verification_type"] = "numerical_ish"
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_rejects_extra_field(self):
        payload = _capability_payload()
        payload["project_type"] = "hybrid"  # wrong level
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)

    def test_description_max_length(self):
        payload = _capability_payload()
        payload["description"] = "x" * 241
        with pytest.raises(ValidationError):
            Capability.model_validate(payload)


class TestCapabilityGraph:
    def test_accepts_documented_shape(self):
        CapabilityGraph.model_validate(_capability_graph_payload())

    def test_rejects_empty_capabilities(self):
        payload = _capability_graph_payload()
        payload["capabilities"] = []
        with pytest.raises(ValidationError):
            CapabilityGraph.model_validate(payload)

    def test_rejects_missing_project_type(self):
        payload = _capability_graph_payload()
        del payload["project_type"]
        with pytest.raises(ValidationError):
            CapabilityGraph.model_validate(payload)


class TestVerificationHook:
    def test_accepts_documented_shape(self):
        VerificationHook.model_validate({
            "hook_id": "ver-decision-log-schema-001",
            "verification_type": VerificationType.unit_test,
            "entrypoint": "verification/ver-decision-log-schema-001.py::test_req_001",
        })

    def test_findings_default_empty_allowed(self):
        VerificationHook.model_validate({
            "hook_id": "ver-decision-log-schema-001",
            "verification_type": "unit_test",
            "entrypoint": "verification/x.py::test",
        })

    def test_rejects_unknown_verification_type(self):
        with pytest.raises(ValidationError):
            VerificationHook.model_validate({
                "hook_id": "x",
                "verification_type": "bogus",
                "entrypoint": "y::z",
            })


class TestSkillFrontmatter:
    def test_accepts_documented_shape(self):
        SkillFrontmatter.model_validate(_skill_frontmatter_payload())

    def test_rejects_empty_triggers(self):
        payload = _skill_frontmatter_payload()
        payload["triggers"] = []
        with pytest.raises(ValidationError):
            SkillFrontmatter.model_validate(payload)

    def test_rejects_empty_findings(self):
        payload = _skill_frontmatter_payload()
        payload["findings"] = []
        with pytest.raises(ValidationError):
            SkillFrontmatter.model_validate(payload)

    def test_rejects_empty_contract_refs(self):
        payload = _skill_frontmatter_payload()
        payload["contract_refs"] = []
        with pytest.raises(ValidationError):
            SkillFrontmatter.model_validate(payload)


class TestSkillIndex:
    def test_accepts_documented_shape(self):
        SkillIndex.model_validate({
            "generated_at": "2026-04-22T00:00:00+00:00",
            "decision_log_version": 1,
            "entries": [{
                "capability_name": "decision-log-schema-validate",
                "trigger_keywords": ["decision", "log", "schema"],
                "skill_path": "skills/decision-log-schema-validate/SKILL.md",
                "contract_refs": ["contract-decision-log-validate"],
                "composes": [],
            }],
        })

    def test_rejects_empty_entries(self):
        with pytest.raises(ValidationError):
            SkillIndex.model_validate({
                "generated_at": "2026-04-22T00:00:00+00:00",
                "decision_log_version": 1,
                "entries": [],
            })

    def test_rejects_empty_trigger_keywords(self):
        with pytest.raises(ValidationError):
            SkillIndexEntry.model_validate({
                "capability_name": "x",
                "trigger_keywords": [],
                "skill_path": "skills/x/SKILL.md",
                "contract_refs": ["contract-x"],
            })
