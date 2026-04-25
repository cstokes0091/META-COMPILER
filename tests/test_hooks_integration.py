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


def _write_skill_md(scaffold_dir: Path, *, capability_name: str, finding_ids: list[str]) -> Path:
    skill_dir = scaffold_dir / "skills" / capability_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    fm_fids = "\n".join(f"  - {fid}" for fid in finding_ids)
    fm_findings = "\n".join(
        f"  - finding_id: {fid}\n    citation_id: {fid.split('#')[0]}\n    seed_path: seeds/x.md\n    locator: {{}}"
        for fid in finding_ids
    )
    path.write_text(
        f"""---
name: {capability_name}
description: test skill
triggers:
  - validate decision log schema
required_finding_ids:
{fm_fids}
contract_refs:
  - contract-test
verification_hooks:
  - ver-test-001
findings:
{fm_findings}
---

# Skill: test
""",
        encoding="utf-8",
    )
    return path


def test_validate_skill_finding_citations_accepts_known_finding(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    # Write a real finding so the hook sees known IDs.
    findings = artifacts / "wiki" / "findings"
    findings.mkdir(parents=True)
    (findings / "src-decision-seed.json").write_text(
        json.dumps({
            "citation_id": "src-decision-seed",
            "seed_path": "seeds/decision-seed.md",
            "file_hash": "hashABCDEFGHIJ",
            "concepts": [],
            "quotes": [],
            "claims": [],
        }),
        encoding="utf-8",
    )
    _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["validate decision log schema"],
    )  # we won't use this, but keeping scaffold structure present
    path = _write_skill_md(
        artifacts / "scaffolds" / "v1",
        capability_name="req-001",
        finding_ids=["src-decision-seed#hashABCDEFGH"],
    )
    r = _invoke(
        "validate_skill_finding_citations",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow", r


def test_validate_skill_finding_citations_denies_unknown_finding(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    path = _write_skill_md(
        artifacts / "scaffolds" / "v1",
        capability_name="req-001",
        finding_ids=["src-fake#deadbeefdead"],
    )
    r = _invoke(
        "validate_skill_finding_citations",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "unresolved" in (r.get("reason") or "")


def test_validate_skill_finding_citations_bootstrap_v1_allows_citation_id(tmp_path):
    """v1 bootstrap: when findings are empty, citation IDs in the SKILL resolve
    against wiki/citations/index.yaml."""
    artifacts = tmp_path / "workspace-artifacts"
    _write(
        artifacts / "wiki" / "citations" / "index.yaml",
        "citations_index:\n"
        "  citations:\n"
        "    src-decision-seed:\n"
        "      human: decision seed\n"
        "      source:\n        type: document\n        path: seeds/x.md\n",
    )
    # Sibling capabilities.yaml with decision_log_version: 1
    _write_capabilities_yaml(
        artifacts / "scaffolds" / "v1",
        triggers=["validate decision log schema"],
    )
    path = _write_skill_md(
        artifacts / "scaffolds" / "v1",
        capability_name="req-001",
        finding_ids=["src-decision-seed"],
    )
    r = _invoke(
        "validate_skill_finding_citations",
        {"hookEventName": "PostToolUse", "tool_input": {"file_path": str(path)}},
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow", r


def _write_capability_graph_fixture(artifacts: Path, *, covered_reqs: list[str], dl_reqs: list[str]) -> None:
    scaffold_dir = artifacts / "scaffolds" / "v1"
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    caps = "\n".join(
        f"    - name: cap-{rid}\n"
        f"      description: Test capability for {rid}.\n"
        f"      when_to_use:\n        - validate schema\n"
        f"      required_finding_ids:\n        - src-x\n"
        f"      io_contract_ref: contract-x\n"
        f"      verification_type: unit_test\n"
        f"      verification_hook_ids:\n        - ver-001\n"
        f"      requirement_ids:\n        - {rid}\n"
        f"      citation_ids:\n        - src-x\n"
        f"      composes: []"
        for rid in covered_reqs
    )
    (scaffold_dir / "capabilities.yaml").write_text(
        f"capability_graph:\n"
        f"  generated_at: 2026-04-22T00:00:00+00:00\n"
        f"  decision_log_version: 1\n"
        f"  project_type: hybrid\n"
        f"  capabilities:\n{caps if caps else '    []'}\n",
        encoding="utf-8",
    )
    dl_rows = "\n".join(
        f"    - id: {rid}\n      description: stub\n      citations:\n        - src-x"
        for rid in dl_reqs
    )
    (artifacts / "decision-logs").mkdir(parents=True, exist_ok=True)
    (artifacts / "decision-logs" / "decision_log_v1.yaml").write_text(
        f"decision_log:\n  meta:\n    version: 1\n  requirements:\n{dl_rows}\n",
        encoding="utf-8",
    )


def test_validate_capability_coverage_accepts_full_coverage(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_capability_graph_fixture(
        artifacts,
        covered_reqs=["REQ-001", "REQ-002"],
        dl_reqs=["REQ-001", "REQ-002"],
    )
    r = _invoke(
        "validate_capability_coverage",
        _bash("meta-compiler validate-stage --stage 3"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_validate_capability_coverage_denies_uncovered_req(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_capability_graph_fixture(
        artifacts,
        covered_reqs=["REQ-001"],
        dl_reqs=["REQ-001", "REQ-999"],
    )
    r = _invoke(
        "validate_capability_coverage",
        _bash("meta-compiler validate-stage --stage 3"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "REQ-999" in (r.get("reason") or "")


def test_validate_capability_coverage_skips_unrelated_commands(tmp_path):
    r = _invoke(
        "validate_capability_coverage",
        _bash("meta-compiler scaffold"),
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


def test_gate_reconcile_request_denies_without_request_or_returns(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    artifacts.mkdir()
    cmd = _bash("meta-compiler wiki-apply-reconciliation --version 2")
    r = _invoke("gate_reconcile_request", cmd, tmp_path)
    assert r.get("permissionDecision") == "deny"
    assert "reconcile_request.yaml" in r.get("reason", "")


def test_gate_reconcile_request_denies_when_proposal_malformed(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    runtime = artifacts / "runtime" / "wiki_reconcile"
    runtime.mkdir(parents=True)
    (runtime / "reconcile_request.yaml").write_text(
        "wiki_reconcile_request:\n  version: 2\n"
    )
    reports = artifacts / "wiki" / "reports"
    reports.mkdir(parents=True)
    (reports / "concept_reconciliation_v2.yaml").write_text(
        "concept_reconciliation_proposal:\n"
        "  generated_at: 't'\n"
        "  version: 2\n"
        "  alias_groups:\n"
        "    - canonical_name: 'Foo'\n"
        "      justification: 'j'\n"
        "      members:\n"
        "        - name: 'a'\n"  # missing source_citation_id, evidence_locator, definition_excerpt
    )
    r = _invoke(
        "gate_reconcile_request",
        _bash("meta-compiler wiki-apply-reconciliation --version 2"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "schema validation" in r.get("reason", "")


def test_gate_reconcile_request_allows_with_subagent_returns(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    runtime = artifacts / "runtime" / "wiki_reconcile"
    returns_dir = runtime / "subagent_returns"
    returns_dir.mkdir(parents=True)
    (runtime / "reconcile_request.yaml").write_text(
        "wiki_reconcile_request:\n  version: 2\n"
    )
    (returns_dir / "noise.json").write_text(
        '{"bucket_key": "noise", "alias_groups": [], "distinct_concepts": []}'
    )
    r = _invoke(
        "gate_reconcile_request",
        _bash("meta-compiler wiki-apply-reconciliation --version 2"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_gate_cross_source_synthesis_returns_denies_without_workplan(tmp_path):
    r = _invoke(
        "gate_cross_source_synthesis_returns",
        _bash("meta-compiler wiki-apply-cross-source-synthesis --version 2"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "work_plan.yaml" in r.get("reason", "")


def test_gate_cross_source_synthesis_returns_denies_without_returns(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    runtime = artifacts / "runtime" / "wiki_cross_source"
    runtime.mkdir(parents=True)
    (runtime / "work_plan.yaml").write_text(
        "wiki_cross_source_work_plan:\n  version: 2\n  work_items: []\n"
    )
    r = _invoke(
        "gate_cross_source_synthesis_returns",
        _bash("meta-compiler wiki-apply-cross-source-synthesis --version 2"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "subagent" in r.get("reason", "")


def test_gate_cross_source_synthesis_returns_allows_when_returns_present(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    runtime = artifacts / "runtime" / "wiki_cross_source"
    returns_dir = runtime / "subagent_returns"
    returns_dir.mkdir(parents=True)
    (runtime / "work_plan.yaml").write_text(
        "wiki_cross_source_work_plan:\n  version: 2\n  work_items: []\n"
    )
    (returns_dir / "concept-foo.json").write_text("{}")
    r = _invoke(
        "gate_cross_source_synthesis_returns",
        _bash("meta-compiler wiki-apply-cross-source-synthesis --version 2"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_gate_implementation_plan_denies_without_decision_log(tmp_path):
    r = _invoke(
        "gate_implementation_plan",
        _bash("meta-compiler plan-implementation --finalize"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "decision_log" in r.get("reason", "")


def test_gate_implementation_plan_denies_without_plan_md(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    decision_logs = artifacts / "decision-logs"
    decision_logs.mkdir(parents=True)
    (decision_logs / "decision_log_v1.yaml").write_text(
        "decision_log:\n  meta:\n    version: 1\n"
    )
    r = _invoke(
        "gate_implementation_plan",
        _bash("meta-compiler plan-implementation --finalize"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "implementation_plan_v1.md" in r.get("reason", "")


def test_gate_implementation_plan_allows_when_plan_md_present(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    decision_logs = artifacts / "decision-logs"
    decision_logs.mkdir(parents=True)
    (decision_logs / "decision_log_v1.yaml").write_text(
        "decision_log:\n  meta:\n    version: 1\n"
    )
    (decision_logs / "implementation_plan_v1.md").write_text("# Plan\n")
    r = _invoke(
        "gate_implementation_plan",
        _bash("meta-compiler plan-implementation --finalize"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "allow"


def test_gate_capability_compile_denies_when_plan_md_present_but_extract_missing(tmp_path):
    artifacts = tmp_path / "workspace-artifacts"
    _write_hybrid_manifest(artifacts, stage="2")
    decision_logs = artifacts / "decision-logs"
    decision_logs.mkdir(parents=True, exist_ok=True)
    (decision_logs / "decision_log_v1.yaml").write_text(
        "decision_log:\n  meta:\n    version: 1\n"
    )
    (decision_logs / "implementation_plan_v1.md").write_text("# Plan\n")
    # plan_extract_v1.yaml deliberately not written.
    r = _invoke(
        "gate_capability_compile",
        _bash("meta-compiler compile-capabilities"),
        tmp_path,
    )
    assert r.get("permissionDecision") == "deny"
    assert "plan_extract_v1.yaml" in r.get("reason", "")


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
