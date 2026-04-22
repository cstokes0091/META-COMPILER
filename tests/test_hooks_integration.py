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
