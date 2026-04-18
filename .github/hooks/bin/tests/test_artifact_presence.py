import pytest

from .test_utils import _build_workspace


@pytest.mark.parametrize("check,required_rel_path,event", [
    ("gate_orchestrator_mode_preflight",
     "workspace-artifacts/runtime/stage2/precheck_request.yaml",
     "PreToolUse"),
    ("gate_orchestrator_mode_postflight",
     "workspace-artifacts/runtime/stage2/postcheck_request.yaml",
     "PreToolUse"),
    ("require_verdict_preflight",
     "workspace-artifacts/runtime/stage2/precheck_verdict.yaml",
     "SubagentStop"),
    ("require_verdict_postflight",
     "workspace-artifacts/runtime/stage2/postcheck_verdict.yaml",
     "SubagentStop"),
    ("gate_ingest_workplan",
     "workspace-artifacts/runtime/ingest/work_plan.yaml",
     "PreToolUse"),
    ("require_ingest_report",
     "workspace-artifacts/wiki/reports/ingest_report.yaml",
     "SubagentStop"),
    ("require_handoff",
     "workspace-artifacts/wiki/reviews/1a2_handoff.yaml",
     "SubagentStop"),
])
def test_deny_when_missing(check, required_rel_path, event, run_hook, tmp_path):
    _build_workspace(tmp_path)
    rc, out, _ = run_hook(check, {"hookEventName": event}, cwd=tmp_path)
    assert out.get("permissionDecision") == "deny" or out.get("decision") == "block"


@pytest.mark.parametrize("check,required_rel_path,event,deny_mode", [
    ("gate_orchestrator_mode_preflight",
     "workspace-artifacts/runtime/stage2/precheck_request.yaml", "PreToolUse", "deny"),
    ("require_verdict_preflight",
     "workspace-artifacts/runtime/stage2/precheck_verdict.yaml", "SubagentStop", "block"),
    ("gate_ingest_workplan",
     "workspace-artifacts/runtime/ingest/work_plan.yaml", "PreToolUse", "deny"),
    ("require_handoff",
     "workspace-artifacts/wiki/reviews/1a2_handoff.yaml", "SubagentStop", "block"),
])
def test_allow_when_present(check, required_rel_path, event, deny_mode, run_hook, tmp_path):
    _build_workspace(tmp_path)
    target = tmp_path / required_rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if "verdict" in required_rel_path or "handoff" in required_rel_path:
        target.write_text("verdict: PROCEED\ndecision: PROCEED\n")
    else:
        target.write_text("placeholder: true\n")
    rc, out, _ = run_hook(check, {"hookEventName": event}, cwd=tmp_path)
    assert out.get("permissionDecision") != "deny"
    assert out.get("decision") != "block"


def test_require_verdict_rejects_missing_decision_field(run_hook, tmp_path):
    _build_workspace(tmp_path)
    target = tmp_path / "workspace-artifacts/runtime/stage2/precheck_verdict.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("something_else: true\n")
    rc, out, _ = run_hook("require_verdict_preflight", {"hookEventName": "SubagentStop"}, cwd=tmp_path)
    assert out.get("decision") == "block"
