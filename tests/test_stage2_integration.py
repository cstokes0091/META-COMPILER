"""End-to-end Stage 2 test simulating the full prompt-as-conductor loop.

We skip the actual LLM invocations (no @stage2-orchestrator, no
Copilot Chat) and instead:

  1. Run `--start` via the Python API.
  2. Write a transcript as a human/LLM would — prose plus decision blocks.
  3. Run `--finalize` via the Python API.
  4. Assert the compiled Decision Log validates, the manifest lists it,
     and the fidelity checks all pass.

This exercises the code paths the CLI wires together, minus the agent.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from meta_compiler.artifacts import build_paths, load_manifest
from meta_compiler.stages.elicit_stage import (
    run_elicit_vision_finalize,
    run_elicit_vision_start,
)
from meta_compiler.validation import validate_stage
from tests.test_elicit_vision_start import _seed_workspace
from tests.test_elicit_vision_finalize import FULL_TRANSCRIPT_ALL_SECTIONS


def test_end_to_end_start_then_finalize_produces_valid_workspace(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    # --- Step 1: --start ---
    start_result = run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    assert start_result["status"] == "ready_for_orchestrator"
    assert paths.stage2_brief_path.exists()
    assert paths.stage2_transcript_path.exists()
    assert paths.stage2_precheck_request_path.exists()

    # --- Step 2 would run @stage2-orchestrator mode=preflight ---
    # Simulate a PROCEED verdict artifact the way the agent would write it.
    precheck_verdict_payload = {
        "stage2_orchestrator_verdict": {
            "stage": "preflight",
            "verdict": "PROCEED",
            "generated_at": "2026-04-17T00:00:00+00:00",
            "decision_log_version": 1,
            "checks": [
                {
                    "name": "semantic_readiness",
                    "result": "PASS",
                    "evidence": "wiki coverage adequate for the problem statement",
                }
            ],
            "summary": "Context is ready for Stage 2 dialog.",
            "next_action": "Proceed to Step 3 (converse).",
        }
    }
    with paths.stage2_precheck_verdict_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(precheck_verdict_payload, handle, sort_keys=False)

    # --- Step 3: LLM conducts the dialog; we inject a hand-written transcript ---
    paths.stage2_transcript_path.write_text(
        FULL_TRANSCRIPT_ALL_SECTIONS, encoding="utf-8"
    )

    # --- Step 4: --finalize ---
    finalize_result = run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert finalize_result["status"] == "compiled"
    assert finalize_result["decision_log_version"] == 1
    assert finalize_result["block_count"] == 9

    # --- Step 5 would run @stage2-orchestrator mode=postflight ---
    # Simulate the verdict the agent would write.
    postcheck_verdict_payload = {
        "stage2_orchestrator_verdict": {
            "stage": "postflight",
            "verdict": "PROCEED",
            "generated_at": "2026-04-17T00:01:00+00:00",
            "decision_log_version": 1,
            "checks": [
                {
                    "name": "fidelity_audit",
                    "result": "PASS",
                    "evidence": "every transcript block faithfully maps to a YAML entry",
                }
            ],
            "summary": "Ingest is faithful; safe to run audit-requirements.",
            "next_action": "Run `meta-compiler audit-requirements`.",
        }
    }
    with paths.stage2_postcheck_verdict_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(postcheck_verdict_payload, handle, sort_keys=False)

    # The decision-log validator expects wiki_search/results.yaml once
    # last_completed_stage is "2"; this integration test bypassed Step 0
    # via skip_wiki_search=True, so seed a minimal valid payload.
    with paths.wiki_search_results_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "wiki_search_results": {
                    "generated_at": "2026-04-17T00:00:00+00:00",
                    "problem_statement_hash": "test",
                    "topics": {},
                }
            },
            handle,
            sort_keys=False,
        )

    # --- Assertions on workspace state ---

    # Decision Log is schema-valid.
    decision_log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    assert decision_log_path.exists()
    issues = validate_stage(paths, stage="decision-log")
    assert issues == []

    # Manifest lists the new decision log entry.
    manifest = load_manifest(paths)
    decision_logs = manifest["workspace_manifest"]["decision_logs"]
    assert len(decision_logs) == 1
    assert decision_logs[0]["version"] == 1
    assert manifest["workspace_manifest"]["research"]["last_completed_stage"] == "2"

    # The Stage 2 runtime artifacts all validate.
    stage_issues = validate_stage(paths, stage="2")
    assert stage_issues == [], stage_issues

    # All four Stage 2 runtime YAMLs exist.
    assert paths.stage2_precheck_request_path.exists()
    assert paths.stage2_precheck_verdict_path.exists()
    assert paths.stage2_postcheck_request_path.exists()
    assert paths.stage2_postcheck_verdict_path.exists()
