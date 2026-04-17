"""Tests for the new Stage 2 runtime artifact validators."""
from __future__ import annotations

from meta_compiler.validation import (
    validate_stage2_postcheck_request,
    validate_stage2_precheck_request,
    validate_stage2_verdict,
)


# ---------------------------------------------------------------------------
# precheck_request
# ---------------------------------------------------------------------------


def test_precheck_request_valid_payload_yields_no_issues():
    payload = {
        "stage2_precheck_request": {
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "mechanical_checks": [
                {
                    "name": "problem_statement_complete",
                    "result": "PASS",
                    "evidence": "all sections present",
                }
            ],
            "verdict_output_path": "workspace-artifacts/runtime/stage2/precheck_verdict.yaml",
        }
    }
    assert validate_stage2_precheck_request(payload) == []


def test_precheck_request_missing_root_is_flagged():
    assert validate_stage2_precheck_request({}) == [
        "stage2 precheck request: missing stage2_precheck_request root"
    ]


def test_precheck_request_missing_required_field_is_flagged():
    payload = {
        "stage2_precheck_request": {
            "generated_at": "2026-04-17T00:00:00+00:00",
            "mechanical_checks": [],
            # decision_log_version and verdict_output_path missing
        }
    }
    issues = validate_stage2_precheck_request(payload)
    assert any("decision_log_version" in i for i in issues)
    assert any("verdict_output_path" in i for i in issues)


def test_precheck_request_bad_check_result_flagged():
    payload = {
        "stage2_precheck_request": {
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "mechanical_checks": [
                {"name": "foo", "result": "MAYBE"},
            ],
            "verdict_output_path": "out.yaml",
        }
    }
    issues = validate_stage2_precheck_request(payload)
    assert any("PASS|FAIL|WARN" in i for i in issues)


# ---------------------------------------------------------------------------
# postcheck_request
# ---------------------------------------------------------------------------


def test_postcheck_request_valid_payload_yields_no_issues():
    payload = {
        "stage2_postcheck_request": {
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "inputs": {
                "transcript": "workspace-artifacts/runtime/stage2/transcript.md",
                "decision_log": "workspace-artifacts/decision-logs/decision_log_v1.yaml",
            },
            "mechanical_checks": [
                {"name": "block_count_matches_entry_count", "result": "PASS"},
            ],
            "verdict_output_path": "workspace-artifacts/runtime/stage2/postcheck_verdict.yaml",
        }
    }
    assert validate_stage2_postcheck_request(payload) == []


def test_postcheck_request_requires_inputs_object():
    payload = {
        "stage2_postcheck_request": {
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "inputs": "not a dict",
            "mechanical_checks": [],
            "verdict_output_path": "out.yaml",
        }
    }
    issues = validate_stage2_postcheck_request(payload)
    assert any("inputs" in i for i in issues)


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------


def test_preflight_verdict_proceed_is_valid():
    payload = {
        "stage2_orchestrator_verdict": {
            "stage": "preflight",
            "verdict": "PROCEED",
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "checks": [{"name": "readiness", "result": "PASS"}],
            "summary": "context looks good",
        }
    }
    assert validate_stage2_verdict(payload) == []


def test_preflight_verdict_revise_is_flagged():
    payload = {
        "stage2_orchestrator_verdict": {
            "stage": "preflight",
            "verdict": "REVISE",
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "checks": [],
            "summary": "",
        }
    }
    issues = validate_stage2_verdict(payload)
    assert any("preflight must be PROCEED|BLOCK" in i for i in issues)


def test_postflight_verdict_block_is_flagged():
    payload = {
        "stage2_orchestrator_verdict": {
            "stage": "postflight",
            "verdict": "BLOCK",
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "checks": [],
            "summary": "",
        }
    }
    issues = validate_stage2_verdict(payload)
    assert any("postflight must be PROCEED|REVISE" in i for i in issues)


def test_verdict_unknown_stage_flagged():
    payload = {
        "stage2_orchestrator_verdict": {
            "stage": "midflight",
            "verdict": "PROCEED",
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "checks": [],
            "summary": "",
        }
    }
    issues = validate_stage2_verdict(payload)
    assert any("preflight|postflight" in i for i in issues)
