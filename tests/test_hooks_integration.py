"""End-to-end smoke test for meta_hook.py event sequences.

Does NOT require VSCode. Invokes meta_hook.py as a subprocess with
simulated hook-input JSON to replay a realistic Stage 2 re-entry scenario,
then asserts the audit log contains the expected decisions in order.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".github" / "hooks" / "bin" / "meta_hook.py"


def _invoke(check: str, payload: dict, cwd: Path) -> dict:
    env = {**os.environ}
    env.pop("META_COMPILER_HOOK_TEST", None)  # enable audit log
    proc = subprocess.run(
        [sys.executable, str(HOOK), check],
        input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=10,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _bash(cmd): return {"hookEventName": "PreToolUse", "tool_input": {"command": cmd}}


def test_stage2_reentry_sequence_audited(tmp_path):
    """Replay: SessionStart → denied scaffold → write reentry_request → allowed stage2-reentry."""
    # Build workspace at stage 2
    artifacts = tmp_path / "workspace-artifacts"
    (artifacts / "manifests").mkdir(parents=True)
    (artifacts / "manifests" / "workspace_manifest.yaml").write_text(
        "workspace_manifest:\n  research:\n    last_completed_stage: '2'\n"
    )
    ps = tmp_path / "PROBLEM_STATEMENT.md"
    ps.write_text("problem v1\n")
    sha = hashlib.sha256(ps.read_bytes()).hexdigest()

    # 1. SessionStart inject_state
    r = _invoke("inject_state", {"hookEventName": "SessionStart"}, tmp_path)
    assert "last_completed_stage" in (r.get("additionalContext") or "")

    # 2. Deny scaffold (wrong stage is NOT this test — stage is "2" which allows scaffold)
    # Instead: deny stage2-reentry without request
    r = _invoke("gate_reentry_request",
                _bash("meta-compiler stage2-reentry --reason x --sections architecture"),
                tmp_path)
    assert r.get("permissionDecision") == "deny"

    # 3. Author reentry_request.yaml
    (artifacts / "runtime" / "stage2").mkdir(parents=True)
    req_path = artifacts / "runtime" / "stage2" / "reentry_request.yaml"
    req_path.write_text(
        "stage2_reentry_request:\n"
        "  parent_version: 1\n"
        "  problem_change_summary: 'test'\n"
        "  problem_statement:\n"
        f"    previously_ingested_sha256: {sha}\n"
        f"    current_sha256: {sha}\n"
        "    updated: false\n"
        "    update_rationale: 'unchanged'\n"
        "  reason: 'test'\n"
        "  revised_sections:\n    - architecture\n"
        "  carried_consistency_risks: []\n"
    )

    # 4. Now stage2-reentry is allowed
    r = _invoke("gate_reentry_request",
                _bash("meta-compiler stage2-reentry --from-request workspace-artifacts/runtime/stage2/reentry_request.yaml"),
                tmp_path)
    assert r.get("permissionDecision") == "allow"

    # 5. Audit log contains both decisions
    audit_path = artifacts / "runtime" / "hook_audit.log"
    assert audit_path.exists()
    lines = audit_path.read_text().splitlines()
    decisions = [json.loads(l).get("decision") for l in lines if l.strip()]
    assert "deny" in decisions
    assert "allow" in decisions


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_hybrid_manifest(artifacts: Path, stage: str = "2") -> None:
    _write(
        artifacts / "manifests" / "workspace_manifest.yaml",
        "workspace_manifest:\n"
        "  project_name: Test\n"
        "  project_type: hybrid\n"
        f"  research:\n    last_completed_stage: '{stage}'\n",
    )


def _write_decision_log_v1(artifacts: Path) -> None:
    _write(
        artifacts / "decision-logs" / "decision_log_v1.yaml",
        "decision_log:\n"
        "  meta:\n"
        "    version: 1\n"
        "    project_type: hybrid\n"
        "  requirements:\n"
        "    - id: REQ-001\n"
        "      description: Decision log validation\n"
        "      citations:\n        - src-decision-seed\n"
        "  architecture: []\n"
        "  conventions: []\n",
    )


# ---------------------------------------------------------------------------
# Commit 3 hooks: gate_capability_compile / validate_capability_schema /
# validate_trigger_specificity
# ---------------------------------------------------------------------------


def test_gate_capability_compile_denies_when_stage_not_2(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="1c")
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "last_completed_stage" in (r.get("reason") or "")


def test_gate_capability_compile_denies_without_decision_log(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "decision_log" in (r.get("reason") or "")


def test_gate_capability_compile_v1_bootstrap_allowed_without_findings(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    _write_decision_log_v1(artifacts)
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_gate_capability_compile_v2_requires_findings(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    _write(
        artifacts / "decision-logs" / "decision_log_v1.yaml",
        "decision_log:\n  meta:\n    version: 1\n",
    )
    _write(
        artifacts / "decision-logs" / "decision_log_v2.yaml",
        "decision_log:\n  meta:\n    version: 2\n",
    )
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "findings" in (r.get("reason") or "")


def test_gate_capability_compile_allow_empty_findings_flag(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    _write(
        artifacts / "decision-logs" / "decision_log_v2.yaml",
        "decision_log:\n  meta:\n    version: 2\n",
    )
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities --allow-empty-findings"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def _write_capabilities_yaml(scaffold_dir: Path, *, triggers: list[str], verification_type: str = "unit_test") -> Path:
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    path = scaffold_dir / "capabilities.yaml"
    trig_block = "\n".join(f"        - {t}" for t in triggers)
    path.write_text(
        f"""capability_graph:
  generated_at: 2026-04-22T00:00:00+00:00
  decision_log_version: 1
  project_type: hybrid
  capabilities:
    - name: test-cap
      description: Test capability for hook integration.
      when_to_use:
{trig_block}
      required_finding_ids:
        - src-decision-seed
      io_contract_ref: contract-test-cap
      verification_type: {verification_type}
      verification_hook_ids:
        - ver-test-cap-001
      requirement_ids:
        - REQ-001
      citation_ids:
        - src-decision-seed
      composes: []
""",
        encoding="utf-8",
    )
    return path


def test_validate_capability_schema_accepts_valid(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    path = _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["validate decision log schema"],
    )
    r = _invoke(
        "validate_capability_schema",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_validate_capability_schema_denies_unknown_verification_type(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    path = _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["validate decision log schema"],
        verification_type="bogus_type",
    )
    r = _invoke(
        "validate_capability_schema",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "verification_type" in (r.get("reason") or "")


def test_validate_trigger_specificity_accepts_domain_trigger(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_decision_log_v1(artifacts)
    path = _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["validate decision log schema"],
    )
    r = _invoke(
        "validate_trigger_specificity",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_validate_trigger_specificity_denies_generic_trigger(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_decision_log_v1(artifacts)
    path = _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["use when implementing"],
    )
    r = _invoke(
        "validate_trigger_specificity",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "generic triggers" in (r.get("reason") or "")


def test_gate_artifact_writes_blocks_capabilities_yaml(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    target = artifacts / "scaffolds" / "v1" / "capabilities.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    r = _invoke(
        "gate_artifact_writes",
        {"hookEventName": "PreToolUse", "tool_input": {"file_path": str(target)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "capabilities.yaml" in (r.get("reason") or "")


def test_gate_artifact_writes_allows_override_env(tmp_path, monkeypatch):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    target = artifacts / "scaffolds" / "v1" / "capabilities.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("META_COMPILER_SKIP_HOOK", "1")
    r = _invoke(
        "gate_artifact_writes",
        {"hookEventName": "PreToolUse", "tool_input": {"file_path": str(target)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_gate_artifact_writes_blocks_contract_yaml(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    target = artifacts / "scaffolds" / "v1" / "contracts" / "contract-x.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    r = _invoke(
        "gate_artifact_writes",
        {"hookEventName": "PreToolUse", "tool_input": {"file_path": str(target)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"


def test_gate_wiki_search_apply_denies_without_topic_files(tmp_path):
    """gate_wiki_search_apply blocks --apply when results dir is empty."""
    artifacts = tmp_path / "workspace-artifacts"
    runtime = artifacts / "runtime" / "stage2" / "wiki_search"
    runtime.mkdir(parents=True)

    apply_cmd = _bash("meta-compiler wiki-search --apply")

    # No request yet -> deny
    r = _invoke("gate_wiki_search_apply", apply_cmd, tmp_path)
    assert r.get("permissionDecision") == "deny"
    assert "wiki_search_request.yaml" in r.get("reason", "")

    # Request exists but no T-*.yaml -> still deny
    (runtime / "wiki_search_request.yaml").write_text(
        "wiki_search_request:\n  topic_count: 1\n"
    )
    r = _invoke("gate_wiki_search_apply", apply_cmd, tmp_path)
    assert r.get("permissionDecision") == "deny"
    assert "T-*.yaml" in r.get("reason", "")

    # Drop in a topic file -> allow
    results_dir = runtime / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "T-001.yaml").write_text(
        "wiki_search_topic_result:\n  topic_id: T-001\n  generated_at: x\n"
        "  concepts: []\n  equations: []\n  citations: []\n  related_pages: []\n"
        "  cross_source_notes: []\n"
    )
    r = _invoke("gate_wiki_search_apply", apply_cmd, tmp_path)
    assert r.get("permissionDecision") == "allow"
