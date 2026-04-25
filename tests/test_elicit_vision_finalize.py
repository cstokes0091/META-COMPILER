"""Integration tests for `meta-compiler elicit-vision --finalize`.

These tests exercise the transcript → Decision Log compile step end to
end without an LLM: the test writes a transcript by hand, then asserts
the compiled `decision_log_v<N>.yaml` validates and the mechanical
fidelity checks pass.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths
from meta_compiler.stages.elicit_stage import (
    run_elicit_vision_finalize,
    run_elicit_vision_start,
)
from tests.test_elicit_vision_start import _seed_workspace


MINIMAL_TRANSCRIPT_WITH_ONE_BLOCK = """\
# Stage 2 Transcript — v1

## Decision Area: Conventions

The human chose black-formatted Python after discussing 2-space vs 4-space.

### Decision: Code style
- Section: conventions
- Domain: code
- Choice: black-formatted, 4-space indentation, type hints on public functions
- Rationale: matches existing meta-compiler style; reduces review friction
- Citations: src-test

## Decision Area: Code Architecture

### Decision: language-choice
- Section: code-architecture
- Aspect: language
- Choice: Python 3.11
- Rationale: matches workspace toolchain
- Citations: src-test

### Decision: libraries-choice
- Section: code-architecture
- Aspect: libraries
- Choice: pyyaml + pytest
- Libraries:
  - pyyaml: serialization (>=6.0)
  - pytest: tests (>=8.0)
- Rationale: stable
- Citations: src-test
"""


FULL_TRANSCRIPT_ALL_SECTIONS = """\
# Stage 2 Transcript — v1

## Decision Area: Conventions

### Decision: Code style
- Section: conventions
- Domain: code
- Choice: black-formatted, 4-space
- Rationale: matches existing style
- Citations: src-test

## Decision Area: Architecture

### Decision: Core architecture
- Section: architecture
- Component: stage-2-orchestrator
- Approach: boundary integrity audit
- Alternatives rejected:
  - CLI-mediated dialog: too rigid
- Constraints applied: fresh context, artifact-only handoff
- Rationale: separates dialog from integrity
- Citations: src-test

## Decision Area: Code Architecture

### Decision: language-choice
- Section: code-architecture
- Aspect: language
- Choice: Python 3.11
- Rationale: matches the workspace toolchain
- Citations: src-test

### Decision: libraries-choice
- Section: code-architecture
- Aspect: libraries
- Choice: pyyaml + pytest
- Libraries:
  - pyyaml: config serialization (>=6.0)
  - pytest: unit tests (>=8.0)
- Rationale: stable and already vendored
- Citations: src-test

## Decision Area: Scope (in)

### Decision: Ralph-loop Python driver
- Section: scope-in
- Item: Stage 4 Python runner
- Rationale: scaffold describes contract but nothing executes it
- Citations: src-test

## Decision Area: Scope (out)

### Decision: GUI client
- Section: scope-out
- Item: standalone GUI client
- Rationale: VSCode + Copilot Chat covers the interactive surface today
- Revisit if: non-VSCode users adopt the tool at scale
- Citations: src-test

## Decision Area: Requirements

### Decision: Fast preflight
- Section: requirements
- Source: derived
- Description: When the user invokes elicit-vision --start, the system shall write brief.md within 2 seconds.
- Verification: Benchmark on a workspace with 50 wiki pages and confirm wall clock < 2s.
- Lens: performance
- Rationale: fast CLI keeps the prompt responsive
- Citations: src-test

## Decision Area: Open Items

### Decision: Dual prompt source
- Section: open_items
- Description: Decide whether to consolidate on .github/prompts/ only
- Deferred to: future_work
- Owner: human
- Rationale: not load-bearing for this release
- Citations: (none)

## Decision Area: Agents Needed

### Decision: stage2-orchestrator
- Section: agents_needed
- Role: stage2-orchestrator
- Responsibility: preflight + postflight integrity audit
- Inputs:
  - precheck_request: document
  - decision_log: document
  - transcript: document
- Outputs:
  - precheck_verdict: document
  - postcheck_verdict: document
- Key constraints: read-only against transcript
- Rationale: keeps CLI deterministic
- Citations: src-test
"""


# ---------------------------------------------------------------------------
# Happy path — minimal transcript → valid decision log v1
# ---------------------------------------------------------------------------


def test_finalize_with_minimal_transcript_writes_decision_log(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    # Must run --start first to produce the skeleton, then replace with our
    # crafted transcript.
    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        MINIMAL_TRANSCRIPT_WITH_ONE_BLOCK, encoding="utf-8"
    )

    result = run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "compiled"
    assert result["decision_log_version"] == 1
    assert result["block_count"] == 3
    assert result["requirement_count"] == 0

    decision_log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    assert decision_log_path.exists()
    with decision_log_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    root = payload["decision_log"]
    assert root["meta"]["version"] == 1
    assert root["meta"]["parent_version"] is None
    assert len(root["conventions"]) == 1
    assert root["conventions"][0]["name"] == "Code style"

    assert paths.stage2_postcheck_request_path.exists()
    with paths.stage2_postcheck_request_path.open("r", encoding="utf-8") as handle:
        postcheck = yaml.safe_load(handle)
    check_names = {
        c["name"] for c in postcheck["stage2_postcheck_request"]["mechanical_checks"]
    }
    assert check_names == {
        "block_count_matches_entry_count",
        "citation_ids_resolve",
        "req_ids_sequential",
        "schema_validates",
        "probe_coverage",
    }


def test_finalize_with_full_transcript_populates_every_section(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        FULL_TRANSCRIPT_ALL_SECTIONS, encoding="utf-8"
    )

    result = run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "compiled"
    assert result["requirement_count"] == 1
    assert result["block_count"] == 9

    decision_log_path = paths.decision_logs_dir / "decision_log_v1.yaml"
    with decision_log_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    root = payload["decision_log"]
    assert len(root["conventions"]) == 1
    assert len(root["architecture"]) == 1
    assert len(root["code_architecture"]) == 2
    assert len(root["scope"]["in_scope"]) == 1
    assert len(root["scope"]["out_of_scope"]) == 1
    assert len(root["requirements"]) == 1
    assert root["requirements"][0]["id"] == "REQ-001"
    assert len(root["open_items"]) == 1
    assert len(root["agents_needed"]) == 1


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_finalize_without_transcript_raises(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"

    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "transcript missing" in str(excinfo.value)


def test_finalize_with_empty_blocks_raises(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    # Skeleton has no decision blocks — leave it as-is.
    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "no decision blocks" in str(excinfo.value)


def test_finalize_with_malformed_block_raises_and_does_not_write_yaml(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    # Missing 'Domain:' for a conventions block.
    paths.stage2_transcript_path.write_text(
        "### Decision: Broken\n"
        "- Section: conventions\n"
        "- Choice: something\n"
        "- Rationale: whatever\n"
        "- Citations: (none)\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    assert "parse failed" in str(excinfo.value)
    assert not (paths.decision_logs_dir / "decision_log_v1.yaml").exists()


def test_finalize_with_unresolved_citation_fails_fidelity(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        "### Decision: Code style\n"
        "- Section: conventions\n"
        "- Domain: code\n"
        "- Choice: black-formatted\n"
        "- Rationale: consistency\n"
        "- Citations: src-never-registered\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as excinfo:
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )
    msg = str(excinfo.value)
    assert "fidelity check failed" in msg
    assert "citation_ids_resolve" in msg
    # Postcheck request is still written (for diagnostics).
    assert paths.stage2_postcheck_request_path.exists()
    # Decision log is NOT written.
    assert not (paths.decision_logs_dir / "decision_log_v1.yaml").exists()


# ---------------------------------------------------------------------------
# Re-entry — parent_version is set when a prior decision log exists
# ---------------------------------------------------------------------------


def test_finalize_v2_sets_parent_version(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    # First run: produces v1
    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        MINIMAL_TRANSCRIPT_WITH_ONE_BLOCK, encoding="utf-8"
    )
    run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert (paths.decision_logs_dir / "decision_log_v1.yaml").exists()

    # Second run: --start detects v1 and bumps to v2, transcript is rewritten.
    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        MINIMAL_TRANSCRIPT_WITH_ONE_BLOCK.replace(
            "Code style", "Code style revised"
        ),
        encoding="utf-8",
    )
    result = run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["decision_log_version"] == 2

    with (paths.decision_logs_dir / "decision_log_v2.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        v2 = yaml.safe_load(handle)
    assert v2["decision_log"]["meta"]["parent_version"] == 1
    assert "Revision" in (v2["decision_log"]["meta"]["reason_for_revision"] or "")


# ---------------------------------------------------------------------------
# Use case extraction from frontmatter
# ---------------------------------------------------------------------------


CONSTRAINT_TRANSCRIPT = """\
# Stage 2 Transcript — v1

## Decision Area: Code Architecture

### Decision: language-choice
- Section: code-architecture
- Aspect: language
- Choice: Python 3.11
- Constraints applied: CON-002
- Rationale: matches the workspace toolchain
- Citations: src-test

### Decision: libraries-choice
- Section: code-architecture
- Aspect: libraries
- Choice: pyyaml + pytest
- Libraries:
  - pyyaml: config serialization (>=6.0)
  - pytest: unit tests (>=8.0)
- Rationale: stable
- Citations: src-test

## Decision Area: Constraints

### Decision: latency-budget
- Section: constraints
- Description: System response < 250 ms p95
- Kind: performance_target
- Verification required: true
- Rationale: customer SLA
- Citations: src-test

### Decision: python-pin
- Section: constraints
- Description: Python 3.11 only
- Kind: tooling
- Rationale: existing toolchain
- Citations: (none)
"""


def test_finalize_compiles_constraints_section_with_con_ids(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
    )
    paths.stage2_transcript_path.write_text(CONSTRAINT_TRANSCRIPT, encoding="utf-8")

    result = run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    assert result["status"] == "compiled"

    with (paths.decision_logs_dir / "decision_log_v1.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        payload = yaml.safe_load(handle)
    constraints = payload["decision_log"]["constraints"]
    assert len(constraints) == 2
    assert constraints[0]["id"] == "CON-001"
    assert constraints[0]["kind"] == "performance_target"
    assert constraints[0]["verification_required"] is True
    assert constraints[1]["id"] == "CON-002"
    assert constraints[1]["kind"] == "tooling"
    assert constraints[1]["verification_required"] is False
    # constraints_applied on code_architecture[0] references CON-002.
    assert payload["decision_log"]["code_architecture"][0]["constraints_applied"] == ["CON-002"]


def test_finalize_rejects_unknown_constraint_kind(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
    )
    bad = (
        "# Stage 2 Transcript — v1\n\n"
        "## Decision Area: Constraints\n\n"
        "### Decision: bogus\n"
        "- Section: constraints\n"
        "- Description: anything\n"
        "- Kind: nonsense_kind\n"
        "- Rationale: x\n"
        "- Citations: (none)\n"
    )
    paths.stage2_transcript_path.write_text(bad, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Kind 'nonsense_kind'"):
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )


def test_finalize_rejects_unresolved_con_ref(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
    )
    bad = (
        "# Stage 2 Transcript — v1\n\n"
        "## Decision Area: Code Architecture\n\n"
        "### Decision: language-choice\n"
        "- Section: code-architecture\n"
        "- Aspect: language\n"
        "- Choice: Python 3.11\n"
        "- Constraints applied: CON-999\n"
        "- Rationale: x\n"
        "- Citations: src-test\n\n"
        "### Decision: libraries-choice\n"
        "- Section: code-architecture\n"
        "- Aspect: libraries\n"
        "- Choice: pyyaml\n"
        "- Libraries:\n"
        "  - pyyaml: x (>=6.0)\n"
        "- Rationale: stable\n"
        "- Citations: src-test\n"
    )
    paths.stage2_transcript_path.write_text(bad, encoding="utf-8")
    with pytest.raises(RuntimeError, match="CON-999"):
        run_elicit_vision_finalize(
            artifacts_root=artifacts_root,
            workspace_root=workspace_root,
        )


def test_finalize_extracts_use_case_from_frontmatter(tmp_path):
    workspace_root = _seed_workspace(tmp_path)
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)

    run_elicit_vision_start(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        skip_wiki_search=True,
        )
    paths.stage2_transcript_path.write_text(
        "---\n"
        "use_case: Initial scaffold for the widget factory\n"
        "---\n"
        + MINIMAL_TRANSCRIPT_WITH_ONE_BLOCK,
        encoding="utf-8",
    )
    run_elicit_vision_finalize(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )
    with (paths.decision_logs_dir / "decision_log_v1.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        payload = yaml.safe_load(handle)
    assert (
        payload["decision_log"]["meta"]["use_case"]
        == "Initial scaffold for the widget factory"
    )
