"""Phase C2 tests: deterministic probe_coverage check on Stage 2 transcripts."""
from __future__ import annotations

import pytest

from meta_compiler.stages.elicit_stage import (
    PROBE_COVERAGE_FLOOR,
    compile_decision_log,
    count_probes_per_block,
    mechanical_fidelity_checks,
    parse_decision_blocks,
)


_PROJECT_META = {"project_name": "Probe Test", "project_type": "algorithm"}


# ---------------------------------------------------------------------------
# count_probes_per_block
# ---------------------------------------------------------------------------


def test_count_probes_per_block_empty_transcript_returns_empty():
    assert count_probes_per_block("") == []
    assert count_probes_per_block("\n\n") == []


def test_count_probes_per_block_no_blocks_returns_empty():
    text = "## Decision Area: conventions\n\n- Probe: domain — math.\n"
    assert count_probes_per_block(text) == []


def test_count_probes_per_block_attributes_probes_to_following_block():
    text = (
        "## Decision Area: architecture\n"
        "- Probe: alternatives_rejected — discussed three options.\n"
        "- Probe: invariants — committed to monotonic order.\n"
        "- Probe: failure_modes — accepted eventual consistency.\n"
        "- Probe: measurable_success — < 250ms p95.\n"
        "\n"
        "### Decision: edit-engine\n"
        "- Section: architecture\n"
        "- Component: editor\n"
        "- Approach: CRDT\n"
        "- Constraints applied: latency, offline\n"
        "- Rationale: chosen for offline-first\n"
        "- Citations: (none)\n"
    )
    rows = count_probes_per_block(text)
    assert len(rows) == 1
    assert rows[0]["block_name"] == "edit-engine"
    assert rows[0]["probe_count"] == 4


def test_count_probes_per_block_resets_on_area_heading():
    text = (
        "## Area: A\n"
        "- Probe: one — addressed.\n"
        "## Area: B\n"
        "### Decision: only-after-area-B\n"
    )
    rows = count_probes_per_block(text)
    assert len(rows) == 1
    assert rows[0]["block_name"] == "only-after-area-B"
    assert rows[0]["probe_count"] == 0


def test_count_probes_per_block_resets_between_blocks():
    text = (
        "## Area: A\n"
        "- Probe: a1 — x\n"
        "- Probe: a2 — x\n"
        "- Probe: a3 — x\n"
        "- Probe: a4 — x\n"
        "### Decision: first\n"
        "- Probe: b1 — x\n"
        "### Decision: second\n"
    )
    rows = count_probes_per_block(text)
    assert [(r["block_name"], r["probe_count"]) for r in rows] == [
        ("first", 4),
        ("second", 1),
    ]


def test_count_probes_per_block_recognizes_indented_and_lowercase_probe():
    text = (
        "## Area: A\n"
        "  - probe: one — leading-space lowercase\n"
        "- PROBE: two — uppercase\n"
        "### Decision: x\n"
    )
    rows = count_probes_per_block(text)
    assert rows[0]["probe_count"] == 2


def test_count_probes_per_block_ignores_decoy_lines():
    text = (
        "## Area: A\n"
        "- This is not a probe.\n"
        "- ProbeQuestion: not the form\n"
        "- probed: also not the form\n"
        "### Decision: x\n"
    )
    rows = count_probes_per_block(text)
    assert rows[0]["probe_count"] == 0


# ---------------------------------------------------------------------------
# mechanical_fidelity_checks: probe_coverage entry
# ---------------------------------------------------------------------------


_BLOCK_TEMPLATE = (
    "## Decision Area: conventions\n"
    "{probes}"
    "\n"
    "### Decision: notation-choice\n"
    "- Section: conventions\n"
    "- Domain: math\n"
    "- Choice: vector-bold\n"
    "- Rationale: matches wiki convention\n"
    "- Citations: (none)\n"
)


def _transcript_with_probes(n: int) -> str:
    probes = "".join(f"- Probe: probe-{i} — addressed in dialog.\n" for i in range(n))
    return _BLOCK_TEMPLATE.format(probes=probes)


def _checks_for(text: str) -> dict[str, dict]:
    blocks, errors = parse_decision_blocks(text)
    assert errors == []
    compiled = compile_decision_log(blocks, project_meta=_PROJECT_META)
    checks = mechanical_fidelity_checks(
        blocks=blocks,
        compiled=compiled,
        known_citation_ids=set(),
        transcript_text=text,
    )
    return {c["name"]: c for c in checks}


def test_mechanical_fidelity_omits_probe_coverage_when_transcript_not_provided():
    blocks, _ = parse_decision_blocks(_transcript_with_probes(4))
    compiled = compile_decision_log(blocks, project_meta=_PROJECT_META)
    checks = mechanical_fidelity_checks(
        blocks=blocks,
        compiled=compiled,
        known_citation_ids=set(),
    )
    assert all(c["name"] != "probe_coverage" for c in checks)


def test_mechanical_fidelity_probe_coverage_passes_at_floor():
    text = _transcript_with_probes(PROBE_COVERAGE_FLOOR)
    by_name = _checks_for(text)
    assert by_name["probe_coverage"]["result"] == "PASS"
    assert "1 blocks meet" in by_name["probe_coverage"]["evidence"]


def test_mechanical_fidelity_probe_coverage_warns_below_floor():
    text = _transcript_with_probes(PROBE_COVERAGE_FLOOR - 1)
    by_name = _checks_for(text)
    assert by_name["probe_coverage"]["result"] == "WARN"
    assert "notation-choice" in by_name["probe_coverage"]["evidence"]
    assert by_name["probe_coverage"]["remediation"]
    assert by_name["probe_coverage"]["details"][0]["probe_count"] == PROBE_COVERAGE_FLOOR - 1


def test_mechanical_fidelity_probe_coverage_warn_does_not_fail_compile():
    """WARN must not appear in the FAIL bucket — a shallow block should not
    block --finalize. The orchestrator semantic audit handles REVISE."""
    text = _transcript_with_probes(0)
    by_name = _checks_for(text)
    assert by_name["probe_coverage"]["result"] == "WARN"
    failures = [c for c in by_name.values() if c["result"] == "FAIL"]
    assert failures == []


def test_mechanical_fidelity_probe_coverage_handles_multiple_blocks_partial_shallow():
    text = (
        "## Decision Area: architecture\n"
        + "".join(f"- Probe: a-{i} — x\n" for i in range(4))
        + "\n### Decision: deep-block\n"
        "- Section: architecture\n"
        "- Component: c1\n"
        "- Approach: a1\n"
        "- Constraints applied: x\n"
        "- Rationale: r\n"
        "- Citations: (none)\n"
        "\n"
        + "- Probe: b-1 — only one\n"
        "\n### Decision: shallow-block\n"
        "- Section: architecture\n"
        "- Component: c2\n"
        "- Approach: a2\n"
        "- Constraints applied: x\n"
        "- Rationale: r\n"
        "- Citations: (none)\n"
    )
    by_name = _checks_for(text)
    assert by_name["probe_coverage"]["result"] == "WARN"
    rows = {row["block_name"]: row["probe_count"] for row in by_name["probe_coverage"]["details"]}
    assert rows["deep-block"] == 4
    assert rows["shallow-block"] == 1
